"""Durable per-stream snapshots. Network I/O never happens in these transactions."""
from copy import deepcopy
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import JSON, Integer, String, create_engine, select
from uuid import uuid4
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


class Creation(Base):
    __tablename__ = 'creator_operations'

    id: Mapped[str] = mapped_column(String, primary_key=True)
    alias: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    inputs: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    step: Mapped[str] = mapped_column(String, nullable=False)
    campaign_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state: Mapped[dict] = mapped_column(JSON, nullable=False)


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

    def edit(self, campaign_id, stream_id, version, action, offer_id, share):
        from app.editor import edit_binding

        with Session(self.engine) as session, session.begin():
            # Acquire SQLite's writer lock before reading the expected version.
            session.connection().exec_driver_sql('BEGIN IMMEDIATE')
            draft = session.scalar(select(Draft).where(
                Draft.stream_id == stream_id, Draft.campaign_id == campaign_id))
            if draft is None:
                raise LookupError('Сначала загрузите черновик потока')
            if draft.version != version:
                raise RuntimeError('Черновик изменён в другой вкладке. Перечитайте потоки.')
            draft.bindings = edit_binding(draft.bindings, offer_id, action, share)
            draft.version += 1
            return {'version': draft.version, 'base': draft.base, 'bindings': draft.bindings}

    def prepare_creation(self, operation_id, inputs):
        with Session(self.engine) as session, session.begin():
            session.execute(insert(Creation).values(
                id=operation_id, alias='adrobot_' + uuid4().hex, inputs=inputs,
                status='ready', step='campaign', campaign_id=None, state={},
            ).on_conflict_do_nothing(index_elements=['id']))
            operation = session.get(Creation, operation_id)
            if operation.inputs != inputs:
                raise RuntimeError('Операция уже сохранена с другими входными данными')
            return self.creation_data(operation)

    @staticmethod
    def creation_data(operation):
        return dict(deepcopy(operation.state), id=operation.id, alias=operation.alias,
                    inputs=deepcopy(operation.inputs), status=operation.status,
                    step=operation.step, campaign_id=operation.campaign_id)

    def creations(self):
        with Session(self.engine) as session:
            return [self.creation_data(op) for op in session.scalars(select(Creation).order_by(Creation.id))]

    def creation(self, operation_id):
        with Session(self.engine) as session:
            operation = session.get(Creation, operation_id)
            if operation is None:
                raise LookupError('Операция не найдена')
            return self.creation_data(operation)

    def save_creation(self, operation):
        with Session(self.engine) as session, session.begin():
            row = session.get(Creation, operation['id'])
            row.status, row.step, row.campaign_id = operation['status'], operation['step'], operation['campaign_id']
            row.state = {k: deepcopy(v) for k, v in operation.items()
                         if k not in ('id', 'alias', 'inputs', 'status', 'step', 'campaign_id')}

    def close(self):
        self.engine.dispose()
