import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from test_editing import Keitaro, act, draft


def test_remove_and_return_pinned_binding_preserves_history_on_reopen(tmp_path):
    remote = Catalogue((40, 30, 30))
    url = f"sqlite:///{tmp_path / 'draft.db'}"
    with TestClient(create_app(remote, database_url=url)) as client:
        draft(client)
        pinned = act(client, 1, 'pin', 1).json()['bindings'][0]
        removed = act(client, 2, 'remove', 1)
        assert removed.status_code == 200
        saved = removed.json()
        assert saved['removed'] == [pinned]
        assert [b['share'] for b in saved['bindings']] == [50, 50]
        assert act(client, 3, 'add', 1).status_code == 422
        assert draft(client) == saved
    with TestClient(create_app(remote, database_url=url)) as client:
        assert draft(client) == saved
        returned = act(client, 3, 'bring_back', 1)
        assert returned.status_code == 200
        assert returned.json()['removed'] == []
        assert [(b['offer_id'], b['share'], b['pinned']) for b in returned.json()['bindings']] == [(2, 30, False), (3, 30, False), (1, 40, True)]


def test_unavailable_removed_offer_has_reason_and_cannot_return(tmp_path):
    remote = Catalogue((70, 30))
    with TestClient(create_app(remote, database_url=f"sqlite:///{tmp_path / 'draft.db'}")) as client:
        draft(client)
        saved = act(client, 1, 'remove', 1).json()
        remote.offers = lambda: [dict(id=2, name='Beta')]
        stream = client.get('/api/campaigns/12/streams').json()[0]
        assert stream['draft'] == saved
        assert stream['unavailable_offers'] == {'1': 'Оффер недоступен в каталоге; возврат запрещён'}
        assert act(client, 2, 'bring_back', 1).status_code == 422
        assert draft(client) == saved


@pytest.mark.parametrize(('shares', 'states', 'setup', 'action', 'offer_id', 'status', 'expected'), [
    ((100,), None, [('pin', 1, None)], 'add', 2, 200, [(1, 100, True), (2, 0, False)]),
    ((100, 0), None, [('pin', 1, None)], 'remove', 2, 200, [(1, 100, True)]),
    ((40, 60), None, [('pin', 1, None), ('pin', 2, None)], 'remove', 1, 422, [(1, 40, True), (2, 60, True)]),
    ((100, 27), ['active', 'disabled'], [], 'remove', 1, 422, [(1, 100, False), (2, 27, False)]),
    ((70, 15, 15), None, [('remove', 1, None)], 'bring_back', 1, 200, [(2, 33, False), (3, 33, False), (1, 34, False)]),
    ((40, 30, 30), None, [('pin', 1, None), ('remove', 1, None), ('share', 2, 80)], 'bring_back', 1, 422, [(2, 80, True), (3, 20, False)]),
    ((70, 30, 31.5), ['active', 'active', 'disabled'], [], 'remove', 3, 200, [(1, 70, False), (2, 30, False)]),
    ((70, 30, 31.5), ['active', 'active', 'disabled'], [('remove', 3, None)], 'bring_back', 3, 200, [(1, 70, False), (2, 30, False), (3, 31.5, False)]),
    ((100, 27), ['active', 'disabled'], [], 'add', 2, 422, [(1, 100, False), (2, 27, False)]),
    ((70, 30), None, [], 'add', 1, 422, [(1, 70, False), (2, 30, False)]),
    ((70, 30), None, [], 'add', 999, 422, [(1, 70, False), (2, 30, False)]),
])
def test_composition_acceptance_matrix(tmp_path, shares, states, setup, action, offer_id, status, expected):
    remote = Catalogue(shares, states)
    with TestClient(create_app(remote, database_url=f"sqlite:///{tmp_path / 'draft.db'}")) as client:
        before = draft(client)
        for step, target, share in setup:
            response = act(client, before['version'], step, target, **({'share': share} if share is not None else {}))
            assert response.status_code == 200
            before = response.json()
        response = act(client, before['version'], action, offer_id)
        assert response.status_code == status
        after = draft(client)
        assert [(b['offer_id'], b['share'], b['pinned']) for b in after['bindings']] == expected
        if status != 200:
            assert after == before
        else:
            assert act(client, before['version'], action, offer_id).status_code == 409
            assert draft(client) == after
        assert remote.bindings == after['base']


def test_catalogue_names_remain_available_when_remote_stream_becomes_empty(tmp_path):
    remote = Catalogue((70, 30))
    with TestClient(create_app(remote, database_url=f"sqlite:///{tmp_path / 'draft.db'}")) as client:
        draft(client)
        saved = act(client, 1, 'remove', 1).json()
        remote.bindings = []
        stream = client.get('/api/campaigns/12/streams').json()[0]
        assert stream['draft'] == saved
        assert stream['offer_names']['1'] == 'Alpha'
        assert stream['unavailable_offers'] == {}


class Catalogue(Keitaro):
    def offers(self):
        return [dict(id=i, name=name) for i, name in [(1, 'Alpha'), (2, 'Beta'), (3, 'Gamma')]]


def test_add_existing_offer_redistributes_without_remote_writes(tmp_path):
    remote = Catalogue((70, 30))
    with TestClient(create_app(remote, database_url=f"sqlite:///{tmp_path / 'draft.db'}")) as client:
        before = draft(client)
        response = act(client, 1, 'add', 3)
        assert response.status_code == 200
        saved = response.json()
        assert [(b['share'], b['pinned']) for b in saved['bindings']] == [(34, False), (33, False), (33, False)]
        assert saved['base'] == before['base']
        assert remote.bindings == before['base']
        assert client.get('/api/offers?q=GAM').json() == [dict(id=3, name='Gamma')]
        assert client.get('/api/offers?q=3').json() == [dict(id=3, name='Gamma')]
        assert client.get('/api/offers?q=33').json() == []
