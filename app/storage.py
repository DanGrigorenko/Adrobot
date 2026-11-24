"""Durable per-stream snapshots. Network I/O never happens in these transactions."""
from copy import deepcopy
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Boolean, JSON, Integer, String, create_engine, select
from uuid import uuid4
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.orm.attributes import flag_modified


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
    publication_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # Push confirms these separately; editing never changes them.
    confirmed_pins: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    confirmed_removed: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    notices: Mapped[list] = mapped_column(JSON, nullable=False, default=list)


class Publication(Base):
    __tablename__ = 'publications'

    id: Mapped[str] = mapped_column(String, primary_key=True)
    stream_id: Mapped[int] = mapped_column(Integer, nullable=False)
    campaign_id: Mapped[int] = mapped_column(Integer, nullable=False)
    base: Mapped[list] = mapped_column(JSON, nullable=False)
    sent: Mapped[list] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    completed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    unfinished: Mapped[bool] = mapped_column(Boolean, nullable=False)


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
            session.connection().exec_driver_sql('BEGIN IMMEDIATE')
            # A concurrent first open cannot replace an already established base.
            session.execute(insert(Draft).values(
                stream_id=stream_id, campaign_id=campaign_id, version=1,
                base=deepcopy(offers), bindings=bindings,
            ).on_conflict_do_nothing(index_elements=['stream_id']))
            draft = session.scalar(select(Draft).where(Draft.stream_id == stream_id))
            from app.editor import same_bindings
            operation = session.get(Publication, draft.publication_id) if draft.publication_id else None
            plain = [{k: b[k] for k in ('offer_id', 'share', 'state')} for b in draft.bindings]
            clean = (same_bindings(plain, draft.base) and draft.removed == draft.confirmed_removed
                     and {b['offer_id'] for b in draft.bindings if b['pinned']} == set(draft.confirmed_pins))
            base_ids = [b['offer_id'] for b in draft.base]
            offer_ids = [b['offer_id'] for b in offers]
            if (clean and not (operation and operation.unfinished)
                    and len(set(base_ids)) == len(base_ids) and len(set(offer_ids)) == len(offer_ids)
                    and set(base_ids) != set(offer_ids)):
                self._accept_remote(draft, offers)
                draft.version += 1
            return {'version': draft.version, 'base': draft.base, 'bindings': draft.bindings, 'removed': draft.removed, 'notices': draft.notices}

    def existing(self, campaign_id):
        with Session(self.engine) as session:
            return {d.stream_id: {'version': d.version, 'base': d.base, 'bindings': d.bindings, 'removed': d.removed, 'notices': d.notices}
                    for d in session.scalars(select(Draft).where(Draft.campaign_id == campaign_id))}

    def edit(self, campaign_id, stream_id, version, action, offer_id, share, catalogue=None):
        from app.editor import edit_binding, redistribute

        with Session(self.engine) as session, session.begin():
            # Acquire SQLite's writer lock before reading the expected version.
            session.connection().exec_driver_sql('BEGIN IMMEDIATE')
            draft = session.scalar(select(Draft).where(
                Draft.stream_id == stream_id, Draft.campaign_id == campaign_id))
            if draft is None:
                raise LookupError('Load the stream draft first')
            self.check_draft(session, draft, version)
            if action in ('remove', 'bring_back'):
                bindings, removed = deepcopy(draft.bindings), deepcopy(draft.removed)
                source, destination = (bindings, removed) if action == 'remove' else (removed, bindings)
                matches = [b for b in source if b['offer_id'] == offer_id]
                if len(matches) != 1:
                    raise ValueError('Binding not found or ID is duplicated')
                target = matches[0]
                if action == 'bring_back' and offer_id not in catalogue:
                    raise ValueError('Offer is unavailable in the catalog; it cannot be brought back')
                source.remove(target)
                if action == 'bring_back':
                    target['pinned'] = False
                destination.append(target)
                if target['state'] == 'active':
                    if not any(b['state'] == 'active' for b in bindings):
                        raise ValueError('Cannot remove the last active binding')
                    redistribute(bindings)
                draft.bindings, draft.removed = bindings, removed
            elif action == 'add':
                if any(b['offer_id'] == offer_id for b in draft.removed):
                    raise ValueError('The offer is in Removed; use Bring Back')
                if any(b['offer_id'] == offer_id for b in draft.bindings):
                    raise ValueError('The offer is already in the stream, including disabled offers')
                if offer_id not in catalogue:
                    raise ValueError('Offer is unavailable in the catalog')
                bindings = draft.bindings + [dict(offer_id=offer_id, share=0, state='active', pinned=False)]
                draft.bindings = edit_binding(bindings, offer_id, 'unpin')
            else:
                draft.bindings = edit_binding(draft.bindings, offer_id, action, share)
                # JSON booleans compare equal to integers in Python, but must persist as distinct values.
                flag_modified(draft, 'bindings')
            draft.version += 1
            return {'version': draft.version, 'base': draft.base, 'bindings': draft.bindings, 'removed': draft.removed, 'notices': draft.notices}

    def cancel(self, campaign_id, stream_id, version, offers):
        with Session(self.engine) as session, session.begin():
            session.connection().exec_driver_sql('BEGIN IMMEDIATE')
            draft = session.scalar(select(Draft).where(
                Draft.stream_id == stream_id, Draft.campaign_id == campaign_id))
            if draft is None:
                raise LookupError('Load the stream draft first')
            self.check_draft(session, draft, version)
            self._accept_remote(draft, offers)
            # Acceptance resolves a finished publication's notice, not its durable record.
            draft.publication_id = None
            draft.version += 1
            return {'version': draft.version, 'base': draft.base, 'bindings': draft.bindings, 'removed': draft.removed, 'notices': draft.notices}

    @staticmethod
    def _accept_remote(draft, offers):
            from app.editor import same_bindings

            previous = {b['offer_id']: b for b in draft.base}
            latest = {b['offer_id']: b for b in offers}
            changed = {offer_id for offer_id, binding in previous.items()
                       if offer_id not in latest or not same_bindings([binding], [latest[offer_id]])}
            pinned = set(draft.confirmed_pins)
            visible_pins = pinned | {b['offer_id'] for b in draft.bindings + draft.removed if b['pinned']}
            draft.notices = [f'Offer {offer_id} was unpinned: its share or status changed in Keitaro, or the binding no longer exists.'
                             for offer_id in sorted(changed & visible_pins)]
            pinned -= changed
            draft.confirmed_pins = sorted(pinned & latest.keys())
            draft.base = deepcopy(offers)
            draft.bindings = [dict(binding, pinned=binding['offer_id'] in pinned) for binding in offers]
            # SQLAlchemy's JSON equality otherwise treats True and 1 as unchanged.
            flag_modified(draft, 'base')
            flag_modified(draft, 'bindings')
            # Keep published removals and remember bindings removed outside the editor.
            removed = [b for b in draft.confirmed_removed if b['offer_id'] not in latest]
            known = {b['offer_id'] for b in removed}
            removed.extend(dict(b, pinned=False) for b in previous.values()
                           if b['offer_id'] not in latest and b['offer_id'] not in known)
            draft.confirmed_removed = deepcopy(removed)
            draft.removed = deepcopy(removed)

    @staticmethod
    def check_draft(session, draft, version):
        if draft.version != version:
            raise RuntimeError('The draft changed in another tab. Reload the streams.')
        operation = session.get(Publication, draft.publication_id) if draft.publication_id else None
        if operation and operation.unfinished:
            raise RuntimeError('The publication outcome is unknown. Check the result; changes are blocked.')

    @staticmethod
    def publication_data(operation):
        if operation is None:
            return None
        return dict(id=operation.id, status=operation.status, completed=operation.completed,
                    locked=operation.unfinished, base=deepcopy(operation.base), sent=deepcopy(operation.sent))

    def publication(self, campaign_id, stream_id):
        with Session(self.engine) as session:
            draft = session.get(Draft, stream_id)
            if draft is None or draft.campaign_id != campaign_id:
                raise LookupError('Load the stream draft first')
            return self.publication_data(session.get(Publication, draft.publication_id)) if draft.publication_id else None

    def prepare_publication(self, campaign_id, stream_id, version, offers, catalogue):
        from app.editor import same_bindings, validation_errors

        with Session(self.engine) as session, session.begin():
            session.connection().exec_driver_sql('BEGIN IMMEDIATE')
            draft = session.get(Draft, stream_id)
            if draft is None or draft.campaign_id != campaign_id:
                raise LookupError('Load the stream draft first')
            self.check_draft(session, draft, version)
            sent = [{k: b[k] for k in ('offer_id', 'share', 'state')} for b in draft.bindings]
            if not same_bindings(draft.base, offers):
                raise RuntimeError('Conflict: Keitaro has changed since the baseline. Discard the draft and load the current state.')
            errors = validation_errors(sent)
            if errors:
                raise ValueError(errors[0])
            if any(b['offer_id'] not in catalogue for b in sent):
                raise ValueError('An offer in the final set is unavailable in the catalog')
            operation = Publication(id=uuid4().hex, stream_id=stream_id, campaign_id=campaign_id,
                                    base=deepcopy(draft.base), sent=sent, status='unknown',
                                    completed=False, unfinished=True)
            session.add(operation)
            draft.publication_id = operation.id
            draft.version += 1
            return self.publication_data(operation)

    def publication_completed(self, operation_id):
        with Session(self.engine) as session, session.begin():
            session.get(Publication, operation_id).completed = True

    def reconcile_publication(self, campaign_id, stream_id, operation_id, offers):
        from app.editor import same_bindings

        with Session(self.engine) as session, session.begin():
            session.connection().exec_driver_sql('BEGIN IMMEDIATE')
            draft = session.get(Draft, stream_id)
            if draft is None or draft.campaign_id != campaign_id or draft.publication_id != operation_id:
                raise RuntimeError('The publication changed. Reload the streams.')
            operation = session.get(Publication, operation_id)
            if not operation.unfinished:
                return self.publication_data(operation)
            if offers is not None and same_bindings(offers, operation.sent):
                operation.status, operation.unfinished = 'succeeded', False
                draft.base = deepcopy(operation.sent)
                # A correction from JSON true to integer 1 must reach SQLite.
                flag_modified(draft, 'base')
                draft.confirmed_pins = [b['offer_id'] for b in draft.bindings if b['pinned']]
                draft.confirmed_removed = deepcopy(draft.removed)
                draft.notices = []
            elif offers is not None and not same_bindings(offers, operation.base):
                operation.status = 'conflict'
                operation.unfinished = not operation.completed
            elif offers is not None and operation.completed:
                operation.status, operation.unfinished = 'not_applied', False
            else:
                operation.status = 'unknown'
            draft.version += 1
            return self.publication_data(operation)

    def prepare_creation(self, operation_id, inputs):
        with Session(self.engine) as session, session.begin():
            session.execute(insert(Creation).values(
                id=operation_id, alias='adrobot_' + uuid4().hex, inputs=inputs,
                status='ready', step='campaign', campaign_id=None, state={},
            ).on_conflict_do_nothing(index_elements=['id']))
            operation = session.get(Creation, operation_id)
            if operation.inputs != inputs:
                raise RuntimeError('The operation was already saved with different inputs')
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
                raise LookupError('Operation not found')
            return self.creation_data(operation)

    def save_creation(self, operation):
        with Session(self.engine) as session, session.begin():
            row = session.get(Creation, operation['id'])
            row.status, row.step, row.campaign_id = operation['status'], operation['step'], operation['campaign_id']
            row.state = {k: deepcopy(v) for k, v in operation.items()
                         if k not in ('id', 'alias', 'inputs', 'status', 'step', 'campaign_id')}

    def close(self):
        self.engine.dispose()
