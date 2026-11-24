"""Refresh/Cancel acceptance through HTTP and durable SQLite; Keitaro is read-only."""
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

HEADERS = {'Origin': 'http://127.0.0.1:8000', 'X-Adrobot-Request': '1'}
STREAMS = '/api/campaigns/12/streams'
ACTIONS = STREAMS + '/7/actions'
CANCEL = STREAMS + '/7/cancel'


class Keitaro:
    def __init__(self):
        self.bindings = [dict(offer_id=1, share=70, state='active'),
                         dict(offer_id=2, share=30, state='active')]
        self.remote_streams = [dict(id=7, name='Поток', type='regular', schema='landings', offers=self.bindings)]
        self.catalogue = [dict(id=i, name=f'Оффер {i}') for i in (1, 2, 3)]
        self.failure = False

    def streams(self, campaign_id):
        if self.failure:
            raise ConnectionError('untrusted upstream error')
        return deepcopy(self.remote_streams)

    def offers(self):
        if self.failure:
            raise ConnectionError('untrusted upstream error')
        return deepcopy(self.catalogue)


def current(client):
    response = client.get(STREAMS)
    assert response.status_code == 200
    return response.json()[0]


def action(client, version, action='pin', offer_id=1, **extra):
    return client.post(ACTIONS, headers=HEADERS,
                       json=dict(version=version, action=action, offer_id=offer_id, **extra))


def cancel(client, version, **extra):
    return client.post(CANCEL, headers=HEADERS, json=dict(version=version, confirmed=True, **extra))


def test_confirmed_cancel_discards_local_pin_remove_and_share_without_remote_write(tmp_path):
    remote = Keitaro()
    url = f"sqlite:///{tmp_path / 'drafts.db'}"
    original = deepcopy(remote.bindings)
    with TestClient(create_app(remote, database_url=url)) as client:
        initial = current(client)['draft']
        assert action(client, 1, 'share', share=60).status_code == 200
        assert action(client, 2, 'remove', offer_id=1).status_code == 200
        response = cancel(client, 3)
        assert response.status_code == 200
        saved = response.json()
        assert saved['version'] == 4
        assert saved['base'] == original
        assert saved['bindings'] == initial['bindings']
        assert saved['removed'] == []
        assert current(client)['conflict'] is False
        assert remote.bindings == original
    with TestClient(create_app(remote, database_url=url)) as client:
        assert current(client)['draft'] == saved


@pytest.mark.parametrize('failure_at', ['streams', 'offers'])
def test_failed_cancel_keeps_full_draft_and_base_after_reopen(tmp_path, failure_at):
    remote = Keitaro()
    url = f"sqlite:///{tmp_path / 'drafts.db'}"
    with TestClient(create_app(remote, database_url=url)) as client:
        current(client)
        action(client, 1)
        saved = action(client, 2, 'remove', offer_id=1).json()
        original = getattr(remote, failure_at)
        def unavailable(*args):
            raise ConnectionError('upstream secret must not appear')
        setattr(remote, failure_at, unavailable)
        failed = cancel(client, 3)
        assert failed.status_code == 502
        assert 'upstream secret' not in failed.text
        setattr(remote, failure_at, original)
        assert current(client)['draft'] == saved
    with TestClient(create_app(remote, database_url=url)) as client:
        assert current(client)['draft'] == saved


def test_cancel_requires_confirmation_csrf_and_current_version(tmp_path):
    with TestClient(create_app(Keitaro(), database_url=f"sqlite:///{tmp_path / 'drafts.db'}")) as client:
        initial = current(client)['draft']
        for payload in ({'version': 1}, {'version': 1, 'confirmed': False},
                        {'version': True, 'confirmed': True}, {'version': 1, 'confirmed': 'true'}):
            assert client.post(CANCEL, headers=HEADERS, json=payload).status_code == 422
        assert client.post(CANCEL, json={'version': 1, 'confirmed': True}).status_code == 403
        assert current(client)['draft'] == initial
        saved = action(client, 1).json()
        assert cancel(client, 1).status_code == 409
        assert current(client)['draft'] == saved


def test_cancel_does_not_overwrite_edit_made_while_remote_read_was_in_flight(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    remote = Keitaro()
    with TestClient(create_app(remote, database_url=f"sqlite:///{tmp_path / 'drafts.db'}")) as client:
        current(client)
        waiting, release = Event(), Event()
        original = remote.offers
        def slow_offers():
            waiting.set()
            assert release.wait(5)
            return original()
        remote.offers = slow_offers
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(cancel, client, 1)
            try:
                assert waiting.wait(3)
                saved = pool.submit(action, client, 1).result(timeout=3)
                assert saved.status_code == 200
            finally:
                release.set()
            assert pending.result(timeout=3).status_code == 409
        assert current(client)['draft'] == saved.json()


def test_cancel_undoes_unpublished_bring_back_and_accepts_external_return(tmp_path):
    remote = Keitaro()
    with TestClient(create_app(remote, database_url=f"sqlite:///{tmp_path / 'drafts.db'}")) as client:
        current(client)
        assert action(client, 1, 'remove', offer_id=1).status_code == 200
        assert action(client, 2, 'bring_back', offer_id=1).status_code == 200
        assert action(client, 3, 'remove', offer_id=1).status_code == 200
        remote.catalogue = [dict(id=2, name='Оффер 2')]
        fetched = current(client)
        assert fetched['unavailable_offers']['1']
        assert action(client, 4, 'bring_back', offer_id=1).status_code == 422
        accepted = cancel(client, 4).json()
        assert accepted['removed'] == []
        assert accepted['bindings'][0] == dict(offer_id=1, share=70, state='active', pinned=False)


@pytest.mark.parametrize('change', ['share', 'status'])
def test_fetch_accepts_external_changes_for_clean_draft(tmp_path, change):
    remote = Keitaro()
    with TestClient(create_app(remote, database_url=f"sqlite:///{tmp_path / 'drafts.db'}")) as client:
        initial = current(client)['draft']
        if change == 'share':
            remote.bindings[0]['share'], remote.bindings[1]['share'] = 50, 50
        else:
            remote.bindings[0]['state'] = 'disabled'
        fetched = current(client)
        assert fetched['conflict'] is False
        assert fetched['draft']['version'] == initial['version'] + 1
        assert fetched['draft']['base'] == remote.bindings
        assert fetched['draft']['bindings'] == [dict(b, pinned=False) for b in remote.bindings]
        assert current(client)['draft'] == fetched['draft']


@pytest.mark.parametrize('change', ['share', 'status', 'removed'])
def test_fetch_preserves_conflicting_pins_and_cancel_reports_external_pin_loss(tmp_path, change):
    remote = Keitaro()
    url = f"sqlite:///{tmp_path / 'drafts.db'}"
    with TestClient(create_app(remote, database_url=url)) as client:
        base = current(client)['draft']['base']
        saved = action(client, 1).json()
        if change == 'share':
            remote.bindings[0]['share'] = 20.5
        elif change == 'status':
            remote.bindings[0]['state'] = 'disabled'
        else:
            remote.bindings.pop(0)
        fetched = current(client)
        assert fetched['conflict'] is True
        assert fetched['draft'] == saved
        assert fetched['draft']['base'] == base
        response = cancel(client, 2)
        assert response.status_code == 200
        accepted = response.json()
        assert accepted['base'] == remote.bindings
        assert accepted['bindings'] == [dict(b, pinned=False) for b in remote.bindings]
        assert accepted['removed'] == ([dict(base[0], pinned=False)] if change == 'removed' else [])
        assert any('Offer 1 was unpinned' in notice for notice in accepted['notices'])
        assert current(client)['conflict'] is False
    with TestClient(create_app(remote, database_url=url)) as client:
        assert current(client)['draft'] == accepted


def test_fetch_and_cancel_distinguish_invalid_boolean_share_from_integer(tmp_path):
    remote = Keitaro()
    remote.bindings[0]['share'] = 1
    remote.bindings[1]['share'] = 99
    with TestClient(create_app(remote, database_url=f"sqlite:///{tmp_path / 'drafts.db'}")) as client:
        current(client)
        saved = action(client, 1).json()
        # JSON true is invalid, not the integer 1%, despite Python's True == 1.
        remote.bindings[0]['share'] = True
        fetched = current(client)
        assert fetched['conflict'] is True
        assert fetched['draft'] == saved
        accepted = cancel(client, 2)
        assert accepted.status_code == 200
        assert accepted.json()['bindings'][0]['share'] is True
        assert accepted.json()['bindings'][0]['pinned'] is False
        assert any('Offer 1 was unpinned' in notice for notice in accepted.json()['notices'])
        assert current(client)['validation_errors']
        assert current(client)['conflict'] is False


@pytest.mark.parametrize('change', ['deleted', 'redirect', 'unsupported-type'])
def test_unavailable_stream_retains_visible_draft_and_blocks_cancel_and_edits(tmp_path, change):
    remote = Keitaro()
    url = f"sqlite:///{tmp_path / 'drafts.db'}"
    with TestClient(create_app(remote, database_url=url)) as client:
        current(client)
        saved = action(client, 1).json()
        if change == 'deleted':
            remote.remote_streams = []
        elif change == 'redirect':
            remote.remote_streams[0]['schema'] = 'redirect'
        else:
            remote.remote_streams[0]['type'] = 'forced'
        stream = current(client)
        assert stream['draft'] == saved
        assert stream['editable'] is False
        assert stream['read_only_reason']
        assert cancel(client, 2).status_code == 409
        assert action(client, 2).status_code == 409
        assert current(client)['draft'] == saved
    with TestClient(create_app(remote, database_url=url)) as client:
        assert current(client)['draft'] == saved
