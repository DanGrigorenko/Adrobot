"""Durable per-stream snapshots. Network I/O never happens in these transactions."""
from copy import deepcopy
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import JSON, Integer, create_engine, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


class Base(DeclarativeBase):
    pass


class Draft(Base):
    __tablename__ = 'stream_drafts'

    stream_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    base: Mapped[list] = mapped_column(JSON, nullable=False)
    bindings: Mapped[list] = mapped_column(JSON, nullable=False)
    removed: Mapped[list] = mapped_column(JSON, nullable=False, default=list)


class DraftStore:
    def __init__(self, database_url):
        self.engine = create_engine(database_url, connect_args={'check_same_thread': False, 'timeout': 30})

    def migrate(self):
        config = Config()
        config.set_main_option('script_location', str(Path(__file__).resolve().parent.parent / 'migrations'))
        with self.engine.begin() as connection:
            config.attributes['connection'] = connection
            command.upgrade(config, 'head')

    def open(self, campaign_id, stream_id, offers):
        bindings = [dict(binding, pinned=False) for binding in offers]
        with Session(self.engine) as session, session.begin():
            # A concurrent first open cannot replace an already established base.
            session.execute(insert(Draft).values(
                stream_id=stream_id, campaign_id=campaign_id, version=1,
                base=deepcopy(offers), bindings=bindings,
            ).on_conflict_do_nothing(index_elements=['stream_id']))
            draft = session.scalar(select(Draft).where(Draft.stream_id == stream_id))
            return {'version': draft.version, 'base': draft.base, 'bindings': draft.bindings, 'removed': draft.removed}

    def edit(self, campaign_id, stream_id, version, action, offer_id, share, catalogue=None):
        from app.editor import edit_binding, redistribute

        with Session(self.engine) as session, session.begin():
            # Acquire SQLite's writer lock before reading the expected version.
            session.connection().exec_driver_sql('BEGIN IMMEDIATE')
            draft = session.scalar(select(Draft).where(
                Draft.stream_id == stream_id, Draft.campaign_id == campaign_id))
            if draft is None:
                raise LookupError('Сначала загрузите черновик потока')
            if draft.version != version:
                raise RuntimeError('Черновик изменён в другой вкладке. Перечитайте потоки.')
            if action in ('remove', 'bring_back'):
                bindings, removed = deepcopy(draft.bindings), deepcopy(draft.removed)
                source, destination = (bindings, removed) if action == 'remove' else (removed, bindings)
                matches = [b for b in source if b['offer_id'] == offer_id]
                if len(matches) != 1:
                    raise ValueError('Привязка не найдена или ID повторяется')
                target = matches[0]
                if action == 'bring_back' and offer_id not in catalogue:
                    raise ValueError('Оффер недоступен в каталоге; возврат запрещён')
                source.remove(target)
                destination.append(target)
                if target['state'] == 'active':
                    if not any(b['state'] == 'active' for b in bindings):
                        raise ValueError('Нельзя удалить последнюю активную привязку')
                    redistribute(bindings)
                draft.bindings, draft.removed = bindings, removed
            elif action == 'add':
                if any(b['offer_id'] == offer_id for b in draft.removed):
                    raise ValueError('Оффер в Removed: используйте Bring Back (Вернуть)')
                if any(b['offer_id'] == offer_id for b in draft.bindings):
                    raise ValueError('Оффер уже есть в потоке, включая disabled')
                if offer_id not in catalogue:
                    raise ValueError('Оффер недоступен в каталоге')
                bindings = draft.bindings + [dict(offer_id=offer_id, share=0, state='active', pinned=False)]
                draft.bindings = edit_binding(bindings, offer_id, 'unpin')
            else:
                draft.bindings = edit_binding(draft.bindings, offer_id, action, share)
            draft.version += 1
            return {'version': draft.version, 'base': draft.base, 'bindings': draft.bindings, 'removed': draft.removed}

    def close(self):
        self.engine.dispose()
