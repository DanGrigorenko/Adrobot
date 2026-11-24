import pytest
from fastapi.testclient import TestClient

from app.main import create_app


class Keitaro:
    def streams(self, campaign_id):
        return [{"id": 7, "name": "Main", "type": "regular", "schema": "landings",
                 "offers": [{"offer_id": 9, "share": 70, "state": "active"},
                            {"offer_id": 10, "share": 30, "state": "active"}]}]

    def offers(self):
        return [{"id": 9, "name": "First offer"}, {"id": 10, "name": "Second offer"}]


def test_initial_draft_survives_database_reopen_and_remote_changes(tmp_path):
    url = f"sqlite:///{tmp_path / 'drafts.db'}"
    remote = Keitaro()
    with TestClient(create_app(remote, database_url=url)) as client:
        first = client.get('/api/campaigns/12/streams')
        assert first.status_code == 200
        draft = first.json()[0]['draft']
        assert draft == {
            'version': 1, 'removed': [], 'notices': [],
            'base': [{'offer_id': 9, 'share': 70, 'state': 'active'},
                     {'offer_id': 10, 'share': 30, 'state': 'active'}],
            'bindings': [{'offer_id': 9, 'share': 70, 'state': 'active', 'pinned': False},
                         {'offer_id': 10, 'share': 30, 'state': 'active', 'pinned': False}],
        }
    remote.streams = lambda _: [{"id": 7, "name": "Main", "type": "regular", "schema": "landings",
                                "offers": [{"offer_id": 9, "share": 100, "state": "active"}]}]
    with TestClient(create_app(remote, database_url=url)) as client:
        reopened = client.get('/api/campaigns/12/streams').json()[0]
        assert reopened['draft']['removed'] == [draft['bindings'][1]]
        assert reopened['draft']['bindings'] == [dict(offer_id=9, share=100, state='active', pinned=False)]
        assert reopened['conflict'] is False
        assert reopened['offers'][0]['share'] == 100
        assert client.get('/api/campaigns/12/streams').json()[0]['draft'] == reopened['draft']
        remote.streams = lambda _: [{'id': 7, 'type': 'regular', 'schema': 'landings', 'offers': [
            {'offer_id': 9, 'share': 70, 'state': 'active'},
            {'offer_id': 10, 'share': 30, 'state': 'active'}]}]
        restored = client.get('/api/campaigns/12/streams').json()[0]['draft']
        assert restored['removed'] == []
        assert [b['offer_id'] for b in restored['bindings']] == [9, 10]


@pytest.mark.parametrize(('bindings', 'reason'), [
    ([{'offer_id': 9, 'share': 70, 'state': 'active'}, {'offer_id': 10, 'share': 30, 'state': 'active'}], None),
    ([{'offer_id': 9, 'share': 100, 'state': 'active'}, {'offer_id': 10, 'share': 0, 'state': 'active'}], None),
    ([{'offer_id': 9, 'share': 100, 'state': 'active'}, {'offer_id': 10, 'share': 31.5, 'state': 'disabled'}], None),
    ([{'offer_id': 9, 'share': 20, 'state': 'active'}, {'offer_id': 10, 'share': 20, 'state': 'active'}], 'Active shares must total 100%'),
    ([{'offer_id': 9, 'share': 99.5, 'state': 'active'}], 'Active shares must be integers from 0 to 100'),
    ([{'offer_id': 9, 'share': -1, 'state': 'active'}], 'Active shares must be integers from 0 to 100'),
    ([{'offer_id': 9, 'share': 101, 'state': 'active'}], 'Active shares must be integers from 0 to 100'),
    ([{'offer_id': 9, 'share': 100, 'state': 'disabled'}], 'At least one active binding is required'),
    ([], 'At least one active binding is required'),
])
def test_initial_shares_are_preserved_and_explain_future_push_block(tmp_path, bindings, reason):
    remote = Keitaro()
    remote.streams = lambda _: [{'id': 7, 'type': 'default', 'schema': 'landings', 'offers': bindings}]
    with TestClient(create_app(remote, database_url=f"sqlite:///{tmp_path / 'drafts.db'}")) as client:
        stream = client.get('/api/campaigns/12/streams').json()[0]
        assert stream['offers'][0]['name'] == 'First offer' if bindings else stream['offers'] == []
        assert stream['draft']['base'] == bindings
        assert [{k: b[k] for k in ('offer_id', 'share', 'state')} for b in stream['draft']['bindings']] == bindings
        assert stream['validation_errors'] == ([] if reason is None else [reason])


@pytest.mark.parametrize('stream_type', ['regular', 'default'])
def test_offer_distribution_is_supported_but_redirects_never_gain_a_draft(tmp_path, stream_type):
    remote = Keitaro()
    remote.streams = lambda _: [
        {'id': 1, 'type': stream_type, 'schema': 'landings', 'offers': []},
        {'id': 2, 'type': stream_type, 'schema': 'redirect', 'offers': []},
        {'id': 3, 'type': 'forced', 'schema': 'landings', 'offers': []},
    ]
    url = f"sqlite:///{tmp_path / 'drafts.db'}"
    with TestClient(create_app(remote, database_url=url)) as client:
        streams = client.get('/api/campaigns/12/streams').json()
        assert streams[0]['editable'] is True
        assert streams[0]['draft']['version'] == 1
        for stream in streams[1:]:
            assert stream['editable'] is False
            assert stream['draft'] is None
            assert stream['read_only_reason']
    remote.streams = lambda _: [{'id': 2, 'type': 'default', 'schema': 'landings', 'offers': []}]
    with TestClient(create_app(remote, database_url=url)) as client:
        assert client.get('/api/campaigns/12/streams').json()[0]['draft']['version'] == 1


def test_reordered_remote_bindings_do_not_create_a_conflict(tmp_path):
    remote = Keitaro()
    original = remote.streams(12)[0]
    url = f"sqlite:///{tmp_path / 'drafts.db'}"
    with TestClient(create_app(remote, database_url=url)) as client:
        draft = client.get('/api/campaigns/12/streams').json()[0]['draft']
        remote.streams = lambda _: [dict(original, offers=list(reversed(original['offers'])))]
        stream = client.get('/api/campaigns/12/streams').json()[0]
        assert stream['draft'] == draft
        assert stream['conflict'] is False


def test_stream_or_catalogue_failure_does_not_replace_the_saved_base(tmp_path):
    remote = Keitaro()
    url = f"sqlite:///{tmp_path / 'drafts.db'}"
    with TestClient(create_app(remote, database_url=url)) as client:
        draft = client.get('/api/campaigns/12/streams').json()[0]['draft']
        original = remote.streams
        def unavailable(*args):
            raise ConnectionError('private upstream details')
        remote.streams = unavailable
        failed = client.get('/api/campaigns/12/streams')
        assert failed.status_code == 502
        assert 'private upstream details' not in failed.text
        remote.streams = original
        original_offers = remote.offers
        remote.offers = unavailable
        assert client.get('/api/campaigns/12/streams').status_code == 502
        remote.offers = original_offers
        assert client.get('/api/campaigns/12/streams').json()[0]['draft'] == draft
        remote.streams = lambda _: []
        deleted = client.get('/api/campaigns/12/streams').json()[0]
        assert deleted['draft'] == draft
        assert deleted['editable'] is False
        assert 'deleted' in deleted['read_only_reason']


def test_concurrent_first_opens_establish_one_shared_initial_draft(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    url = f"sqlite:///{tmp_path / 'drafts.db'}"
    with TestClient(create_app(Keitaro(), database_url=url)) as client:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: client.get('/api/campaigns/12/streams'), range(8)))
        assert all(result.status_code == 200 for result in results)
        assert all(result.json()[0]['draft'] == results[0].json()[0]['draft'] for result in results)
        assert results[0].json()[0]['draft']['version'] == 1


def test_waiting_for_one_campaign_does_not_block_another(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    waiting, release = Event(), Event()
    original = Keitaro().streams(12)[0]
    class SlowKeitaro(Keitaro):
        def streams(self, campaign_id):
            if campaign_id == 12:
                waiting.set()
                if not release.wait(5):
                    raise ConnectionError('test timeout')
            return [dict(original, id=campaign_id)]

    with TestClient(create_app(SlowKeitaro(), database_url=f"sqlite:///{tmp_path / 'drafts.db'}")) as client:
        with ThreadPoolExecutor(max_workers=2) as pool:
            slow = pool.submit(client.get, '/api/campaigns/12/streams')
            try:
                assert waiting.wait(3)
                fast = pool.submit(client.get, '/api/campaigns/120/streams').result(timeout=3)
                assert fast.status_code == 200
                assert fast.json()[0]['draft']['base'][0]['share'] == 70
            finally:
                release.set()
            assert slow.result(timeout=3).status_code == 200
