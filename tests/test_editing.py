from fastapi.testclient import TestClient
import pytest

from app.main import create_app

HEADERS = {'Origin': 'http://127.0.0.1:8000', 'X-Adrobot-Request': '1'}
PATH = '/api/campaigns/12/streams/7/actions'


class Keitaro:
    def __init__(self, shares=(34, 33, 33), states=None):
        self.bindings = [dict(offer_id=i + 1, share=share, state=(states or ['active'] * len(shares))[i])
                         for i, share in enumerate(shares)]

    def streams(self, campaign_id):
        return [dict(id=7, type='regular', schema='landings', offers=self.bindings),
                dict(id=8, type='default', schema='landings', offers=self.bindings)]

    def offers(self):
        return [dict(id=b['offer_id'], name=str(b['offer_id'])) for b in self.bindings]


def draft(client, index=0):
    return client.get('/api/campaigns/12/streams').json()[index]['draft']


def act(client, version, action='share', offer_id=1, **extra):
    return client.post(PATH, headers=HEADERS, json=dict(version=version, action=action, offer_id=offer_id, **extra))


def test_manual_share_pins_redistributes_and_survives_reopen(tmp_path):
    url = f"sqlite:///{tmp_path / 'drafts.db'}"
    remote = Keitaro()
    with TestClient(create_app(remote, database_url=url)) as client:
        initial = draft(client)
        response = act(client, 1, share=50)
        assert response.status_code == 200
        saved = response.json()
        assert saved['version'] == 2
        assert [(b['share'], b['pinned']) for b in saved['bindings']] == [(50, True), (25, False), (25, False)]
        assert saved['base'] == initial['base']
        assert draft(client, 1) == initial
    with TestClient(create_app(remote, database_url=url)) as client:
        assert draft(client) == saved
        assert remote.bindings == saved['base']


def test_pin_preserves_70_30_and_unpin_redistributes_by_id(tmp_path):
    remote = Keitaro((70, 30))
    remote.bindings.reverse()
    with TestClient(create_app(remote, database_url=f"sqlite:///{tmp_path / 'drafts.db'}")) as client:
        draft(client)
        pinned = act(client, 1, 'pin').json()
        assert [(b['share'], b['pinned']) for b in pinned['bindings']] == [(30, False), (70, True)]
        unpinned = act(client, 2, 'unpin').json()
        assert [(b['share'], b['pinned']) for b in unpinned['bindings']] == [(50, False), (50, False)]


@pytest.mark.parametrize('value', [-1, 101, 12.5, True, '50', None])
def test_invalid_input_is_atomic(tmp_path, value):
    with TestClient(create_app(Keitaro(), database_url=f"sqlite:///{tmp_path / 'drafts.db'}")) as client:
        before = draft(client)
        assert act(client, 1, share=value).status_code == 422
        assert draft(client) == before


def test_impossible_pinned_sum_and_stale_actions_are_atomic(tmp_path):
    with TestClient(create_app(Keitaro(), database_url=f"sqlite:///{tmp_path / 'drafts.db'}")) as client:
        draft(client)
        saved = act(client, 1, share=60).json()
        assert act(client, 2, offer_id=2, share=50).status_code == 422
        assert draft(client) == saved
        stale = act(client, 1, share=20)
        assert stale.status_code == 409
        assert 'Перечитайте' in stale.json()['detail']
        assert draft(client) == saved


@pytest.mark.parametrize(('shares', 'states', 'value', 'expected'), [
    ((34, 33, 33), None, 100, [(100, True), (0, False), (0, False)]),
    ((20.5, 79.5, 31.5), ['active', 'active', 'disabled'], 40, [(40, True), (60, False), (31.5, False)]),
    ((100,), None, 100, [(100, True)]),
    ((25, 25, 25, 25), None, 25, [(25, True), (25, False), (25, False), (25, False)]),
])
def test_recalculation_examples_preserve_disabled(tmp_path, shares, states, value, expected):
    with TestClient(create_app(Keitaro(shares, states), database_url=f"sqlite:///{tmp_path / 'drafts.db'}")) as client:
        draft(client)
        result = act(client, 1, share=value)
        assert result.status_code == 200
        assert [(b['share'], b['pinned']) for b in result.json()['bindings']] == expected


def test_unpin_assigns_remainder_by_id_not_response_order(tmp_path):
    remote = Keitaro()
    remote.bindings.reverse()
    with TestClient(create_app(remote, database_url=f"sqlite:///{tmp_path / 'drafts.db'}")) as client:
        draft(client)
        assert act(client, 1, share=50).status_code == 200
        result = act(client, 2, 'unpin').json()
        assert [(b['offer_id'], b['share']) for b in result['bindings']] == [(3, 33), (2, 33), (1, 34)]


@pytest.mark.parametrize('action', ['share', 'pin', 'unpin'])
def test_disabled_and_missing_bindings_cannot_be_edited(tmp_path, action):
    with TestClient(create_app(Keitaro((100, 22), ['active', 'disabled']), database_url=f"sqlite:///{tmp_path / 'drafts.db'}")) as client:
        before = draft(client)
        for offer_id in (2, 999):
            assert act(client, 1, action, offer_id, share=50).status_code == 422
            assert draft(client) == before


def test_all_pinned_require_100_and_invalid_pin_preserves_draft(tmp_path):
    with TestClient(create_app(Keitaro((100,)), database_url=f"sqlite:///{tmp_path / 'single.db'}")) as client:
        before = draft(client)
        assert act(client, 1, share=50).status_code == 422
        assert draft(client) == before
        assert act(client, 1, 'pin').json()['bindings'][0]['pinned'] is True
    with TestClient(create_app(Keitaro((20, 20)), database_url=f"sqlite:///{tmp_path / 'invalid.db'}")) as client:
        draft(client)
        pinned = act(client, 1, 'pin').json()
        assert [b['share'] for b in pinned['bindings']] == [20, 20]
        assert act(client, 2, 'pin', offer_id=2).status_code == 422
        assert draft(client) == pinned
    with TestClient(create_app(Keitaro((20.5, 79.5)), database_url=f"sqlite:///{tmp_path / 'fraction.db'}")) as client:
        before = draft(client)
        assert act(client, 1, 'pin').status_code == 422
        assert draft(client) == before


def test_concurrent_tabs_only_one_action_succeeds(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    with TestClient(create_app(Keitaro(), database_url=f"sqlite:///{tmp_path / 'drafts.db'}")) as client:
        draft(client)
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda value: act(client, 1, share=value), (40, 60)))
        assert sorted(r.status_code for r in responses) == [200, 409]
        assert draft(client) == next(r.json() for r in responses if r.status_code == 200)


def test_mutation_requires_csrf_and_current_editable_stream(tmp_path):
    remote = Keitaro()
    with TestClient(create_app(remote, database_url=f"sqlite:///{tmp_path / 'drafts.db'}")) as client:
        before = draft(client)
        assert client.post(PATH, json=dict(version=1, action='pin', offer_id=1)).status_code == 403
        remote.streams = lambda _: [dict(id=7, type='regular', schema='redirect')]
        assert act(client, 1, share=50).status_code == 409
        remote.streams = lambda _: [dict(id=7, type='regular', schema='landings', offers=remote.bindings)]
        assert draft(client) == before
