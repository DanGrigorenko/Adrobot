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
            return {'version': draft.version, 'base': draft.base, 'bindings': draft.bindings}

    def close(self):
        self.engine.dispose()
