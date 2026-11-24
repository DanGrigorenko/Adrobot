"""One durable publication per stream. Recovery reads; it never repeats a write."""
from threading import Lock

from app.editor import same_bindings, stream_is_editable


class Publisher:
    def __init__(self, keitaro, store):
        self.keitaro, self.store = keitaro, store
        self.locks = {}

    def stream(self, campaign_id, stream_id):
        remote = next((s for s in self.keitaro.streams(campaign_id) if s['id'] == stream_id), None)
        if not stream_is_editable(remote):
            raise RuntimeError('The stream was deleted or no longer supports offer editing')
        return [{k: b[k] for k in ('offer_id', 'share', 'state')} for b in remote.get('offers', [])]

    def run(self, campaign_id, stream_id, version, *, check=False):
        lock = self.locks.setdefault(stream_id, Lock())
        if not lock.acquire(blocking=False):
            raise RuntimeError('Publication is still in progress; the outcome is not yet known')
        try:
            if check:
                draft = self.store.existing(campaign_id).get(stream_id)
                if draft is None:
                    raise LookupError('Draft not found')
                if draft['version'] != version:
                    raise RuntimeError('The draft changed in another tab. Reload the streams.')
                operation = self.store.publication(campaign_id, stream_id)
                if operation is None:
                    raise LookupError('Publication not found')
                if not operation['locked']:
                    return self.result(campaign_id, stream_id, operation)
            else:
                offers = self.stream(campaign_id, stream_id)
                catalogue = {o['id'] for o in self.keitaro.offers()}
                operation = self.store.prepare_publication(campaign_id, stream_id, version, offers, catalogue)
                if not same_bindings(operation['base'], operation['sent']):
                    try:
                        self.keitaro.update_offers(stream_id, operation['sent'])
                    except ConnectionError:
                        # Timeout/transport errors do not establish remote completion.
                        return self.result(campaign_id, stream_id, operation)
                self.store.publication_completed(operation['id'])
            try:
                offers = self.stream(campaign_id, stream_id)
            except (ConnectionError, RuntimeError):
                offers = None
            operation = self.store.reconcile_publication(campaign_id, stream_id, operation['id'], offers)
            return self.result(campaign_id, stream_id, operation)
        finally:
            lock.release()

    def result(self, campaign_id, stream_id, operation):
        return {'draft': self.store.existing(campaign_id)[stream_id], 'publication': operation}
