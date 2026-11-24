"""Publication acceptance at the HTTP seam, with durable SQLite and a fake Keitaro."""
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.test_refresh_cancel import Keitaro, HEADERS, STREAMS, ACTIONS, CANCEL, current, action, cancel

PUSH = STREAMS + '/7/push'
CHECK = STREAMS + '/7/check-publication'


class PublishingKeitaro(Keitaro):
    def __init__(self):
        super().__init__()
        self.remote_streams[0].update(filters=[{'name': 'country', 'payload': ['DE']}], action_type='do_nothing')
        self.remote_streams.append(dict(id=8, type='default', schema='landings', offers=[dict(offer_id=3, share=100, state='active')]))
        self.writes = []

    def update_offers(self, stream_id, offers):
        self.writes.append((stream_id, deepcopy(offers)))
        next(s for s in self.remote_streams if s['id'] == stream_id)['offers'] = deepcopy(offers)


def publish(client, version):
    return client.post(PUSH, headers=HEADERS, json={'version': version})


def check(client, version):
    return client.post(CHECK, headers=HEADERS, json={'version': version})


def test_verified_push_preserves_other_configuration_and_confirms_removed_and_pins(tmp_path):
    remote = PublishingKeitaro()
    original = deepcopy(remote.remote_streams)
    catalogue = deepcopy(remote.catalogue)
    url = f"sqlite:///{tmp_path / 'drafts.db'}"
    with TestClient(create_app(remote, database_url=url)) as client:
        current(client)
        assert action(client, 1).status_code == 200
        assert action(client, 2, 'pin', offer_id=1).status_code == 200
        assert action(client, 3, 'remove', offer_id=1).status_code == 200
        assert action(client, 4, 'pin', offer_id=2).status_code == 200
        response = publish(client, 5)
        assert response.status_code == 200
        result = response.json()
        assert result['publication']['status'] == 'succeeded'
        saved = current(client)['draft']
        assert saved['base'] == [dict(offer_id=2, share=100, state='active')]
        assert saved['bindings'] == [dict(offer_id=2, share=100, state='active', pinned=True)]
        assert saved['removed'] == [dict(offer_id=1, share=70, state='active', pinned=True)]
        assert remote.remote_streams[0] == dict(original[0], offers=saved['base'])
        assert remote.remote_streams[1] == original[1]
        assert remote.catalogue == catalogue
        assert remote.writes == [(7, saved['base'])]
    with TestClient(create_app(remote, database_url=url)) as client:
        assert current(client)['draft'] == saved
        assert cancel(client, saved['version']).status_code == 200
        restored = current(client)['draft']
        assert restored['removed'] == saved['removed']
        assert restored['bindings'] == saved['bindings']
        unpinned = action(client, restored['version'], 'unpin', offer_id=2).json()
        returned = action(client, unpinned['version'], 'bring_back', offer_id=1)
        assert returned.status_code == 200
        assert [(b['offer_id'], b['share'], b['pinned']) for b in returned.json()['bindings']] == [(2, 50, False), (1, 50, False)]
        assert len(remote.writes) == 1


def test_lost_response_recovers_only_by_read_after_reopening_database(tmp_path):
    remote = PublishingKeitaro()
    update = remote.update_offers
    def lose_response(stream_id, offers):
        update(stream_id, offers)
        raise ConnectionError('secret upstream timeout')
    remote.update_offers = lose_response
    url = f"sqlite:///{tmp_path / 'drafts.db'}"
    with TestClient(create_app(remote, database_url=url)) as client:
        current(client)
        action(client, 1, 'share', share=60)
        result = publish(client, 2)
        assert result.status_code == 200
        assert 'secret' not in result.text
        unknown = result.json()
        assert unknown['publication']['status'] == 'unknown'
        assert unknown['publication']['locked'] is True
        version = unknown['draft']['version']
        assert action(client, version).status_code == 409
        assert cancel(client, version).status_code == 409
        assert publish(client, version).status_code == 409
    with TestClient(create_app(remote, database_url=url)) as client:
        assert current(client)['publication'] == unknown['publication']
        assert current(client)['draft'] == unknown['draft']
        assert len(remote.writes) == 1
        checked = check(client, version)
        assert checked.status_code == 200
        assert checked.json()['publication']['status'] == 'succeeded'
        assert checked.json()['publication']['locked'] is False
        assert current(client)['conflict'] is False
        assert len(remote.writes) == 1


@pytest.mark.parametrize('outcome', ['sent', 'base', 'third', 'unreadable'])
def test_http_200_requires_read_and_all_four_check_outcomes(tmp_path, outcome):
    remote = PublishingKeitaro()
    base = deepcopy(remote.remote_streams[0]['offers'])
    update = remote.update_offers
    def write_then_fail_read(stream_id, offers):
        update(stream_id, offers)
        remote.failure = True
    remote.update_offers = write_then_fail_read
    url = f"sqlite:///{tmp_path / 'drafts.db'}"
    with TestClient(create_app(remote, database_url=url)) as client:
        current(client)
        action(client, 1, 'share', share=60)
        unknown = publish(client, 2).json()
        assert unknown['publication']['status'] == 'unknown'
        assert unknown['publication']['locked'] is True
        assert unknown['publication']['completed'] is True
        assert unknown['draft']['base'] == base
    with TestClient(create_app(remote, database_url=url)) as client:
        remote.failure = outcome == 'unreadable'
        if outcome == 'base':
            remote.remote_streams[0]['offers'] = base
        elif outcome == 'third':
            remote.remote_streams[0]['offers'] = [dict(offer_id=3, share=100, state='active')]
        result = check(client, unknown['draft']['version'])
        assert result.status_code == 200
        checked = result.json()
        assert checked['publication']['status'] == {'sent': 'succeeded', 'base': 'not_applied', 'third': 'conflict', 'unreadable': 'unknown'}[outcome]
        assert checked['publication']['locked'] is (outcome == 'unreadable')
        assert len(remote.writes) == 1
        if outcome != 'sent':
            assert checked['draft']['base'] == base
            assert checked['draft']['bindings'] == unknown['draft']['bindings']
        if outcome == 'base':
            # Only an explicit new Push may issue another write, after fresh validation.
            remote.update_offers = update
            assert publish(client, checked['draft']['version']).json()['publication']['status'] == 'succeeded'
            assert len(remote.writes) == 2
        elif outcome == 'third':
            assert publish(client, checked['draft']['version']).status_code == 409
            assert len(remote.writes) == 1
            assert cancel(client, checked['draft']['version']).status_code == 200
            assert current(client)['conflict'] is False
            assert current(client)['publication'] is None


@pytest.mark.parametrize('observed', ['base', 'third', 'unreadable'])
def test_timeout_never_unlocks_on_old_state_or_time_elapsed(tmp_path, observed):
    remote = PublishingKeitaro()
    def timeout(stream_id, offers):
        remote.writes.append((stream_id, deepcopy(offers)))
        raise ConnectionError('Request may still be running')
    remote.update_offers = timeout
    url = f"sqlite:///{tmp_path / 'drafts.db'}"
    with TestClient(create_app(remote, database_url=url)) as client:
        current(client)
        action(client, 1, 'share', share=60)
        unknown = publish(client, 2).json()
    with TestClient(create_app(remote, database_url=url)) as client:
        if observed == 'third':
            remote.remote_streams[0]['offers'] = [dict(offer_id=3, share=100, state='active')]
        remote.failure = observed == 'unreadable'
        for _ in range(3):
            result = check(client, unknown['draft']['version'])
            assert result.status_code == 200
            unknown = result.json()
            assert unknown['publication']['status'] == ('conflict' if observed == 'third' else 'unknown')
            assert unknown['publication']['locked'] is True
            assert unknown['publication']['completed'] is False
        remote.failure = False
        assert action(client, unknown['draft']['version']).status_code == 409
        assert cancel(client, unknown['draft']['version']).status_code == 409
        assert publish(client, unknown['draft']['version']).status_code == 409
        assert len(remote.writes) == 1


@pytest.mark.parametrize('change,expected', [
    ('external', 409), ('deleted', 409), ('redirect', 409), ('type', 409),
    ('unavailable', 422), ('unavailable-disabled', 422), ('invalid-total', 422),
    ('fractional', 422), ('no-active', 422), ('read-error', 502),
])
def test_push_preflight_rejects_unsafe_states_without_mutating_draft(tmp_path, change, expected):
    remote = PublishingKeitaro()
    if change == 'invalid-total':
        remote.bindings[0]['share'] = 20
    elif change == 'fractional':
        remote.bindings[0]['share'] = 70.5
    elif change == 'no-active':
        for binding in remote.bindings:
            binding['state'] = 'disabled'
    elif change == 'unavailable-disabled':
        remote.bindings[0].update(state='disabled', share=70)
        remote.bindings[1]['share'] = 100
    with TestClient(create_app(remote, database_url=f"sqlite:///{tmp_path / 'drafts.db'}")) as client:
        saved = current(client)['draft']
        if change == 'external':
            remote.bindings[0]['share'] = 80
        elif change == 'deleted':
            remote.remote_streams.pop(0)
        elif change == 'redirect':
            remote.remote_streams[0]['schema'] = 'redirect'
        elif change == 'type':
            remote.remote_streams[0]['type'] = 'forced'
        elif change.startswith('unavailable'):
            remote.catalogue.pop(0)
        elif change == 'read-error':
            remote.failure = True
        assert publish(client, 1).status_code == expected
        remote.failure = False
        assert next(s for s in client.get(STREAMS).json() if s['id'] == 7)['draft'] == saved
        assert remote.writes == []


@pytest.mark.parametrize('pin_only', [False, True])
def test_equal_sets_or_pin_only_confirm_locally_but_recheck_remote(tmp_path, pin_only):
    remote = PublishingKeitaro()
    with TestClient(create_app(remote, database_url=f"sqlite:///{tmp_path / 'drafts.db'}")) as client:
        initial = current(client)['draft']
        version = 1
        if pin_only:
            version = action(client, 1).json()['version']
        remote.remote_streams[0]['offers'].reverse()
        result = publish(client, version)
        assert result.status_code == 200
        saved = result.json()['draft']
        assert result.json()['publication']['status'] == 'succeeded'
        assert saved['base'] == initial['base']
        assert saved['bindings'][0]['pinned'] is pin_only
        assert cancel(client, saved['version']).json()['bindings'][1]['pinned'] is pin_only
        assert remote.writes == []
        # Even a no-op must reject stale base and unavailable offers.
        remote.remote_streams[0]['offers'][0]['share'] = 20
        assert publish(client, current(client)['draft']['version']).status_code == 409
        assert remote.writes == []


def test_in_flight_push_locks_one_stream_not_database_and_rejects_two_tabs(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    remote = PublishingKeitaro()
    entered, release = Event(), Event()
    update = remote.update_offers
    def waiting_write(stream_id, offers):
        entered.set()
        assert release.wait(10)
        update(stream_id, offers)
    remote.update_offers = waiting_write
    url = f"sqlite:///{tmp_path / 'drafts.db'}"
    with TestClient(create_app(remote, database_url=url)) as client:
        current(client)
        action(client, 1, 'share', share=60)
        assert publish(client, 1).status_code == 409
        assert client.post(PUSH, json={'version': 2}).status_code == 403
        assert client.post(CHECK, json={'version': 2}).status_code == 403
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(publish, client, 2)
            try:
                assert entered.wait(3)
                # The reserved operation is committed before the network request starts.
                with TestClient(create_app(remote, database_url=url)) as reader:
                    during = current(reader)
                    assert during['publication']['locked'] is True
                    assert during['publication']['base'][0]['share'] == 70
                    assert during['publication']['sent'][0]['share'] == 60
                version = during['draft']['version']
                assert pool.submit(action, client, version).result(3).status_code == 409
                assert pool.submit(cancel, client, version).result(3).status_code == 409
                assert pool.submit(publish, client, version).result(3).status_code == 409
                assert pool.submit(check, client, version).result(3).status_code == 409
                independent = pool.submit(client.post, STREAMS + '/8/actions', headers=HEADERS,
                                          json={'version': 1, 'action': 'pin', 'offer_id': 3}).result(3)
                assert independent.status_code == 200
            finally:
                release.set()
            assert pending.result(3).json()['publication']['status'] == 'succeeded'
        assert len(remote.writes) == 1
        assert check(client, version).status_code == 409


def test_saved_unknown_publication_is_visible_when_keitaro_is_down_after_restart(tmp_path):
    remote = PublishingKeitaro()
    def timeout(*args):
        raise ConnectionError('timeout')
    remote.update_offers = timeout
    url = f"sqlite:///{tmp_path / 'drafts.db'}"
    with TestClient(create_app(remote, database_url=url)) as client:
        current(client)
        action(client, 1, 'share', share=60)
        unknown = publish(client, 2).json()
    remote.failure = True
    with TestClient(create_app(remote, database_url=url)) as client:
        response = client.get('/api/campaigns/12/drafts')
        assert response.status_code == 200
        saved = next(s for s in response.json() if s['id'] == 7)
        assert saved['draft'] == unknown['draft']
        assert saved['publication'] == unknown['publication']
        assert saved['editable'] is False
        assert saved['read_only_reason']
        assert check(client, saved['draft']['version']).json()['publication']['locked'] is True


def test_published_disabled_binding_keeps_status_and_share(tmp_path):
    remote = PublishingKeitaro()
    remote.bindings.append(dict(offer_id=3, share=27, state='disabled'))
    with TestClient(create_app(remote, database_url=f"sqlite:///{tmp_path / 'drafts.db'}")) as client:
        current(client)
        action(client, 1, 'share', share=60)
        response = publish(client, 2)
        assert response.status_code == 200
        assert response.json()['publication']['status'] == 'succeeded'
        assert remote.remote_streams[0]['offers'] == [dict(offer_id=1, share=60, state='active'),
                                                    dict(offer_id=2, share=40, state='active'),
                                                    dict(offer_id=3, share=27, state='disabled')]
        for invalid in ({}, {'version': True}, {'version': 4.0}, {'version': '4'}, {'version': 4, 'force': True}):
            assert client.post(PUSH, headers=HEADERS, json=invalid).status_code == 422
            assert client.post(CHECK, headers=HEADERS, json=invalid).status_code == 422
        assert len(remote.writes) == 1


def test_push_rejects_edit_from_another_tab_during_preflight(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    remote = PublishingKeitaro()
    entered, release = Event(), Event()
    original = remote.offers
    def slow_catalogue():
        entered.set()
        assert release.wait(5)
        return original()
    with TestClient(create_app(remote, database_url=f"sqlite:///{tmp_path / 'drafts.db'}")) as client:
        current(client)
        remote.offers = slow_catalogue
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(publish, client, 1)
            try:
                assert entered.wait(3)
                edited = pool.submit(action, client, 1, 'share', 1, share=60).result(3)
                assert edited.status_code == 200
            finally:
                release.set()
            assert pending.result(3).status_code == 409
        remote.offers = original
        assert current(client)['draft'] == edited.json()
        assert remote.writes == []


def test_push_confirmation_persists_explicit_boolean_share_correction(tmp_path):
    remote = PublishingKeitaro()
    remote.bindings[0]['share'] = True
    remote.bindings[1]['share'] = 99
    url = f"sqlite:///{tmp_path / 'drafts.db'}"
    with TestClient(create_app(remote, database_url=url)) as client:
        assert current(client)['validation_errors']
        action(client, 1, 'share', share=1)
        assert publish(client, 2).json()['publication']['status'] == 'succeeded'
        assert current(client)['draft']['base'][0]['share'] == 1
        assert type(current(client)['draft']['base'][0]['share']) is int
        assert current(client)['conflict'] is False
    with TestClient(create_app(remote, database_url=url)) as client:
        assert type(current(client)['draft']['base'][0]['share']) is int
        assert current(client)['conflict'] is False
