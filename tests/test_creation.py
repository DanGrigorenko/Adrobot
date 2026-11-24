"""Durable Creator through the browser API and a file-backed database."""
from copy import deepcopy
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app.main import create_app
from tests.test_creator import CreatorKeitaro, HEADERS


class WritingKeitaro(CreatorKeitaro):
    def __init__(self):
        self.campaign_rows = []
        self.stream_rows = []
        self.writes = []
        self.lose = None

    def campaigns(self):
        return deepcopy(self.campaign_rows)

    def campaign(self, campaign_id):
        return deepcopy(next(c for c in self.campaign_rows if c['id'] == campaign_id))

    def streams(self, campaign_id):
        return deepcopy(self.stream_rows)

    def written(self, step):
        self.writes.append(step)
        if self.lose == step:
            self.lose = None
            raise ConnectionError('secret upstream failure')

    def create_campaign(self, payload):
        self.campaign_rows.append(dict(deepcopy(payload), id=100))
        self.written('campaign')

    def create_stream(self, campaign_id, payload):
        assert campaign_id == 100 and payload.get('name')
        self.stream_rows.append(dict(deepcopy(payload), id=len(self.stream_rows) + 200))
        self.written(payload['type'])

    def enable_campaign(self, campaign_id):
        # The test double refuses premature activation.
        assert self.campaign_rows[0]['state'] == 'disabled'
        assert len(self.stream_rows) == 2
        self.campaign_rows[0]['state'] = 'active'
        self.written('enable')


@pytest.fixture
def setup_creation(tmp_path, monkeypatch):
    for key, value in {'CREATOR_DOMAIN_ID': '2', 'CREATOR_GROUP_ID': '3',
                       'CREATOR_TRAFFIC_SOURCE_ID': '4', 'CREATOR_NAME_PREFIX': 'test_'}.items():
        monkeypatch.setenv(key, value)
    return WritingKeitaro(), f"sqlite:///{tmp_path / 'creation.db'}"


def prepare(client, **changes):
    body = {'operation_id': str(uuid4()), 'name': 'Summer', 'geos': ['DE', 'FR', 'DE'], 'offer_id': 9}
    body.update(changes)
    if 'preview_token' not in body:
        reviewed = client.post('/api/creator/preview', headers=HEADERS, json=body)
        assert reviewed.status_code == 200, reviewed.text
        body['preview_token'] = reviewed.json()['preview_token']
    response = client.post('/api/creator/operations', headers=HEADERS, json=body)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize('confirm_before_restart', [False, True])
@pytest.mark.parametrize('change', ['prefix', 'domain', 'group', 'traffic_source'])
def test_reviewed_configuration_cannot_change_across_restart_before_first_write(
        setup_creation, monkeypatch, confirm_before_restart, change):
    remote, db = setup_creation
    with TestClient(create_app(remote, database_url=db)) as client:
        reviewed = client.post('/api/creator/preview', headers=HEADERS, json={
            'name': 'Summer', 'geos': ['DE'], 'offer_id': 9}).json()
        confirmation = dict(name=reviewed['name'], geos=reviewed['geos'],
                            preview_token=reviewed['preview_token'])
        if confirm_before_restart:
            op = prepare(client, **confirmation)
    if change == 'prefix':
        monkeypatch.setenv('CREATOR_NAME_PREFIX', 'changed_')
    else:
        catalogue = {'domain': 'domains', 'group': 'groups', 'traffic_source': 'traffic_sources'}[change]
        setattr(remote, catalogue, lambda: [{'id': 99, 'name': 'Changed setting'}])
        monkeypatch.setenv(f'CREATOR_{change.upper()}_ID', '99')
    with TestClient(create_app(remote, database_url=db)) as client:
        if not confirm_before_restart:
            op = prepare(client, **confirmation)
        result = resume(client, op)
        assert result['status'] == 'blocked'
        assert result['reason']
        assert remote.writes == []
        # Only reviewing and confirming the changed configuration permits creation.
        fresh = prepare(client)
        assert resume(client, fresh)['status'] == 'succeeded'


def test_changed_prefix_blocks_new_writes_when_continuing_reviewed_plan(setup_creation, monkeypatch):
    remote, db = setup_creation
    remote.lose = 'regular'
    with TestClient(create_app(remote, database_url=db)) as client:
        op = prepare(client)
        assert resume(client, op)['status'] == 'unknown'
    monkeypatch.setenv('CREATOR_NAME_PREFIX', 'changed_')
    with TestClient(create_app(remote, database_url=db)) as client:
        result = resume(client, op)
        assert result['status'] == 'blocked'
        assert remote.writes == ['campaign', 'regular']


def resume(client, op):
    response = client.post(f"/api/creator/operations/{op['id']}/continue", headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


def test_server_metadata_on_nested_stream_fields_is_accepted(setup_creation):
    remote, db = setup_creation
    original = remote.streams

    def streams(campaign_id):
        rows = original(campaign_id)
        for row in rows:
            for field in ('filters', 'offers'):
                for item in row.get(field, []):
                    item.update(id=11, stream_id=row['id'], oid=11)
        return rows

    remote.streams = streams
    with TestClient(create_app(remote, database_url=db)) as client:
        result = resume(client, prepare(client, geos=['DE']))
        assert result['status'] == 'succeeded', result
        assert remote.writes == ['campaign', 'regular', 'default', 'enable']


@pytest.mark.parametrize('fault', ['missing', 'mode', 'payload', 'extra-filter', 'extra-country'])
@pytest.mark.parametrize('final_check', [False, True])
def test_nested_filter_mismatch_blocks_before_activation(setup_creation, fault, final_check):
    remote, db = setup_creation
    original = remote.streams
    calls_with_both = 0

    def streams(campaign_id):
        nonlocal calls_with_both
        rows = original(campaign_id)
        if len(rows) == 2:
            calls_with_both += 1
        if rows and (not final_check or (len(rows) == 2 and calls_with_both >= 1)):
            filters = rows[0]['filters']
            filters[0].update(id=11, stream_id=rows[0]['id'], oid=11)
            if fault == 'missing':
                del filters[0]['mode']
            elif fault == 'mode':
                filters[0]['mode'] = 'reject'
            elif fault == 'payload':
                filters[0]['payload'] = ['FR']
            elif fault == 'extra-filter':
                filters.append(deepcopy(filters[0]))
            else:
                filters[0]['payload'].append('FR')
        return rows

    remote.streams = streams
    with TestClient(create_app(remote, database_url=db)) as client:
        result = resume(client, prepare(client, geos=['DE']))
        assert result['status'] == 'blocked'
        assert 'enable' not in remote.writes
        assert ('default' in remote.writes) == final_check


@pytest.mark.parametrize('geos', [['DE'], ['DE', 'FR', 'DE']])
def test_confirmed_creation_verifies_routes_and_active_campaign(setup_creation, geos):
    remote, db = setup_creation
    with TestClient(create_app(remote, database_url=db)) as client:
        op = prepare(client, geos=geos)
        assert remote.writes == []
        assert op['alias']
        assert client.get('/api/creator/operations').json() == [op]
        result = resume(client, op)
        assert result['status'] == 'succeeded'
        assert result['campaign_id'] == 100
        assert result['editor_url'] == '/?campaign=100'
        assert result['keitaro_url'] == 'https://keitaro.example/admin/#!/campaign.editor/100'
        assert remote.writes == ['campaign', 'regular', 'default', 'enable']
        assert remote.campaign_rows[0] == {
            'id': 100, 'name': 'test_Summer', 'alias': op['alias'], 'state': 'active', 'type': 'position',
            'domain_id': 2, 'group_id': 3, 'traffic_source_id': 4,
        }
        assert [row['name'] for row in remote.stream_rows] == ['GEO → Google', 'Default distribution']
        assert remote.stream_rows[0]['filters'] == [
            {'name': 'country', 'mode': 'accept', 'payload': list(dict.fromkeys(geos))}]
        assert remote.stream_rows[0]['action_payload'] == 'https://google.com'
        assert remote.stream_rows[1]['offers'] == [{'offer_id': 9, 'share': 100, 'state': 'active'}]
        assert resume(client, op)['status'] == 'succeeded'
        assert len(remote.writes) == 4


@pytest.mark.parametrize('step', ['campaign', 'regular', 'default', 'enable'])
def test_lost_response_recovers_same_operation_after_restart_without_duplicate_writes(setup_creation, step):
    remote, db = setup_creation
    remote.lose = step
    with TestClient(create_app(remote, database_url=db)) as client:
        op = prepare(client)
        unknown = resume(client, op)
        assert unknown['status'] == 'unknown'
        assert unknown['step'] == step
        assert 'secret' not in str(unknown)
        saved_writes = list(remote.writes)
    with TestClient(create_app(remote, database_url=db)) as client:
        assert client.get('/api/creator/operations').json()[0] == unknown
        assert remote.writes == saved_writes  # Startup must never retry writes.
        result = resume(client, op)
        assert result['status'] == 'succeeded'
        assert remote.writes == ['campaign', 'regular', 'default', 'enable']
        assert len(remote.campaign_rows) == 1
        assert len(remote.stream_rows) == 2


@pytest.mark.parametrize('change', ['alias', 'settings', 'offer'])
def test_creation_rechecks_prerequisites_before_any_remote_write(setup_creation, monkeypatch, change):
    remote, db = setup_creation
    with TestClient(create_app(remote, database_url=db)) as client:
        op = prepare(client)
        if change == 'alias':
            remote.campaign_rows.append({'id': 99, 'alias': op['alias']})
        elif change == 'settings':
            monkeypatch.setenv('CREATOR_DOMAIN_ID', '999')
        else:
            remote.offers = lambda: []
        result = resume(client, op)
        assert result['status'] == 'blocked'
        assert result['reason']
        assert remote.writes == []


@pytest.mark.parametrize('fault', ['missing', 'ambiguous', 'campaign-mismatch', 'stream-mismatch', 'stream-name', 'extra-stream'])
def test_unknown_or_mismatched_recovery_never_issues_another_write(setup_creation, fault):
    remote, db = setup_creation
    remote.lose = 'regular'
    with TestClient(create_app(remote, database_url=db)) as client:
        op = prepare(client)
        assert resume(client, op)['status'] == 'unknown'
        if fault == 'missing':
            remote.stream_rows.clear()
        elif fault == 'ambiguous':
            remote.stream_rows.append(dict(remote.stream_rows[0], id=999))
        elif fault == 'campaign-mismatch':
            remote.campaign_rows[0]['domain_id'] = 999
        elif fault == 'stream-mismatch':
            remote.stream_rows[0]['action_payload'] = 'https://wrong.example'
        elif fault == 'stream-name':
            remote.stream_rows[0]['name'] = 'Wrong name'
        else:
            remote.stream_rows.append({'id': 999, 'type': 'default', 'schema': 'landings', 'offers': []})
        before = list(remote.writes)
        for _ in range(2):
            result = resume(client, op)
            assert result['status'] == 'blocked'
            assert result['reason']
            assert remote.writes == before


@pytest.mark.parametrize('step', ['campaign', 'regular', 'default', 'enable'])
def test_absent_result_does_not_prove_previous_write_finished_or_allow_retry(setup_creation, step):
    remote, db = setup_creation
    def lost_without_visible_result(*args):
        remote.writes.append(step)
        raise ConnectionError('No response; request may still be in flight')
    if step == 'campaign':
        remote.create_campaign = lost_without_visible_result
    elif step == 'enable':
        remote.enable_campaign = lost_without_visible_result
    else:
        original = remote.create_stream
        def create_stream(campaign_id, payload):
            if payload['type'] == step:
                return lost_without_visible_result()
            return original(campaign_id, payload)
        remote.create_stream = create_stream
    with TestClient(create_app(remote, database_url=db)) as client:
        op = prepare(client)
        assert resume(client, op)['status'] == 'unknown'
        writes = list(remote.writes)
        for _ in range(2):
            assert resume(client, op)['status'] == 'blocked'
            assert remote.writes == writes
        assert remote.writes.count(step) == 1
        assert 'enable' not in remote.writes or step == 'enable'


def test_repeated_identity_and_concurrent_continuation_share_one_durable_operation(setup_creation):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    remote, db = setup_creation
    entered, release = Event(), Event()
    original = remote.create_campaign
    def delayed(payload):
        entered.set()
        assert release.wait(10)
        original(payload)
    remote.create_campaign = delayed
    with TestClient(create_app(remote, database_url=db)) as client, ThreadPoolExecutor() as pool:
        op = prepare(client)
        same = prepare(client, operation_id=op['id'])
        assert same == op
        conflict = client.post('/api/creator/operations', headers=HEADERS, json={
            'operation_id': op['id'], 'name': 'different', 'geos': ['DE', 'FR'], 'offer_id': 9,
            'preview_token': op['inputs']['preview_token']})
        assert conflict.status_code == 409
        running = pool.submit(resume, client, op)
        try:
            assert entered.wait(10)
            persisted = client.get(f"/api/creator/operations/{op['id']}").json()
            assert persisted['status'] == 'running'
            assert persisted['pending'] is True
            assert persisted['alias'] == op['alias']
            # A network wait holds neither the SQLite writer lock nor another operation.
            other = prepare(client, name='Other')
            assert other['id'] != op['id']
            assert client.post(f"/api/creator/operations/{op['id']}/continue", headers=HEADERS).status_code == 409
        finally:
            release.set()
        assert running.result()['status'] == 'succeeded'
        assert remote.writes == ['campaign', 'regular', 'default', 'enable']


@pytest.mark.parametrize('fault', ['ambiguous-alias', 'wrong-campaign', 'wrong-stream'])
def test_lost_campaign_or_stream_response_requires_unique_exact_match(setup_creation, fault):
    remote, db = setup_creation
    remote.lose = 'default' if fault == 'wrong-stream' else 'campaign'
    with TestClient(create_app(remote, database_url=db)) as client:
        op = prepare(client)
        assert resume(client, op)['status'] == 'unknown'
        if fault == 'ambiguous-alias':
            remote.campaign_rows.append(dict(remote.campaign_rows[0], id=999))
        elif fault == 'wrong-campaign':
            remote.campaign_rows[0]['type'] = 'weight'
        else:
            remote.stream_rows[1]['offers'][0]['share'] = 99
        writes = list(remote.writes)
        assert resume(client, op)['status'] == 'blocked'
        assert remote.writes == writes


def test_successful_http_reply_without_active_state_is_not_success_or_permission_to_retry(setup_creation):
    remote, db = setup_creation
    remote.enable_campaign = lambda campaign_id: remote.writes.append('enable')
    with TestClient(create_app(remote, database_url=db)) as client:
        op = prepare(client)
        for _ in range(2):
            result = resume(client, op)
            assert result['status'] == 'blocked'
            assert 'editor_url' not in result
            assert remote.campaign_rows[0]['state'] == 'disabled'
        assert remote.writes == ['campaign', 'regular', 'default', 'enable']


def test_creation_mutations_require_csrf_and_saved_input_is_validated(setup_creation):
    remote, db = setup_creation
    with TestClient(create_app(remote, database_url=db)) as client:
        assert client.post('/api/creator/operations', json={}).status_code == 403
        assert client.post('/api/creator/operations/missing/continue').status_code == 403
        assert client.post('/api/creator/operations', headers=HEADERS, json={
            'operation_id': str(uuid4()), 'name': 'name', 'geos': ['XX'], 'offer_id': 9,
        }).status_code == 422
        assert client.get('/api/creator/operations').json() == []
        assert client.get('/api/creator/operations/missing').status_code == 404
        assert remote.writes == []
