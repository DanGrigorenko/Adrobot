"""Creator behavior at the browser HTTP seam; remote supports reads only."""
from fastapi.testclient import TestClient
import pytest

from app.main import create_app

HEADERS = {'Origin': 'http://127.0.0.1:8000', 'X-Adrobot-Request': '1'}


class CreatorKeitaro:
    def offers(self):
        return [{'id': 9, 'name': 'Summer offer'}, {'id': 90, 'name': 'Winter offer'}]

    def domains(self):
        return [{'id': 2, 'name': 'tracking.example'}]

    def groups(self):
        return [{'id': 3, 'name': 'Test campaigns'}]

    def traffic_sources(self):
        return [{'id': 4, 'name': 'Test source'}]


@pytest.fixture
def creator(tmp_path, monkeypatch):
    for key, value in {'CREATOR_DOMAIN_ID': '2', 'CREATOR_GROUP_ID': '3',
                       'CREATOR_TRAFFIC_SOURCE_ID': '4', 'CREATOR_NAME_PREFIX': 'test_example_'}.items():
        monkeypatch.setenv(key, value)
    with TestClient(create_app(CreatorKeitaro(), database_url=f"sqlite:///{tmp_path / 'creator.db'}")) as client:
        yield client


def test_preview_shows_validated_name_settings_and_two_routes_without_remote_writes(creator):
    response = creator.post('/api/creator/preview', headers=HEADERS, json={
        'name': 'campaign1', 'geos': ['DE'], 'offer_id': 9,
    })
    assert response.status_code == 200
    assert response.json() == {
        'name': 'test_example_campaign1', 'geos': ['DE'],
        'settings': {'domain': {'id': 2, 'name': 'tracking.example'},
                     'group': {'id': 3, 'name': 'Test campaigns'},
                     'traffic_source': {'id': 4, 'name': 'Test source'},
                     'name_prefix': 'test_example_'},
        'campaign': {'state': 'disabled', 'type': 'position'},
        'regular': {'type': 'regular', 'schema': 'redirect', 'action_type': 'http',
                    'action_payload': 'https://google.com',
                    'filters': [{'name': 'country', 'mode': 'accept', 'payload': ['DE']}]},
        'default': {'type': 'default', 'schema': 'landings', 'action_type': 'do_nothing',
                    'offers': [{'offer_id': 9, 'name': 'Summer offer', 'share': 100, 'state': 'active'}]},
    }
    assert creator.post('/api/campaigns', headers=HEADERS, json={}).status_code == 405


@pytest.mark.parametrize('geos, expected', [
    (['DE', 'FR', 'DE'], ['DE', 'FR']), ([' de ', 'fr'], ['DE', 'FR']),
    ([], None), (['XX'], None), (['UK'], None), (['DE', 'ZZ'], None),
])
def test_geo_validation_and_deduplication(creator, geos, expected):
    response = creator.post('/api/creator/preview', headers=HEADERS,
                            json={'name': 'campaign1', 'geos': geos, 'offer_id': 9})
    assert response.status_code == (200 if expected else 422)
    if expected:
        assert response.json()['geos'] == expected
        assert response.json()['regular']['filters'][0]['payload'] == expected
    else:
        assert response.json()['detail']


def test_offer_search_by_name_or_exact_id(creator):
    expected = [{'id': 9, 'name': 'Summer offer'}]
    assert creator.get('/api/offers', params={'q': 'SUMMER'}).json() == expected
    assert creator.get('/api/offers', params={'q': ' 9 '}).json() == expected
    assert creator.get('/api/offers', params={'q': 'missing'}).json() == []
    assert len(creator.get('/api/offers').json()) == 2


def test_settings_are_visible_and_checked_before_preview(creator):
    response = creator.get('/api/creator/settings')
    assert response.status_code == 200
    assert response.json()['domain'] == {'id': 2, 'name': 'tracking.example'}
    assert response.json()['group']['id'] == 3
    assert response.json()['traffic_source']['id'] == 4
    assert response.json()['name_prefix'] == 'test_example_'


@pytest.mark.parametrize('name', ['campaign1', ' test_example_campaign1 '])
def test_prefix_is_added_once(creator, name):
    response = creator.post('/api/creator/preview', headers=HEADERS,
                            json={'name': name, 'geos': ['DE'], 'offer_id': 9})
    assert response.status_code == 200
    assert response.json()['name'] == 'test_example_campaign1'


@pytest.mark.parametrize('env', ['CREATOR_DOMAIN_ID', 'CREATOR_GROUP_ID', 'CREATOR_TRAFFIC_SOURCE_ID'])
@pytest.mark.parametrize('value', ['', 'not-an-id', '0', '999'])
def test_missing_or_unavailable_setting_blocks_preview(creator, monkeypatch, env, value):
    monkeypatch.setenv(env, value)
    for response in [creator.get('/api/creator/settings'), creator.post('/api/creator/preview', headers=HEADERS,
                     json={'name': 'campaign1', 'geos': ['DE'], 'offer_id': 9})]:
        assert response.status_code == 422
        assert env in response.json()['detail']


def test_unavailable_selected_offer_blocks_preview(creator):
    response = creator.post('/api/creator/preview', headers=HEADERS,
                            json={'name': 'campaign1', 'geos': ['DE'], 'offer_id': 999})
    assert response.status_code == 422
    assert 'оффер недоступен' in response.json()['detail']


@pytest.mark.parametrize('method', ['domains', 'groups', 'traffic_sources', 'offers'])
def test_upstream_read_failure_blocks_preview_without_leaking_details(creator, monkeypatch, method):
    def fail(self):
        raise ConnectionError('upstream-secret')
    monkeypatch.setattr(CreatorKeitaro, method, fail)
    response = creator.post('/api/creator/preview', headers=HEADERS,
                            json={'name': 'campaign1', 'geos': ['DE'], 'offer_id': 9})
    assert response.status_code == 502
    assert 'upstream-secret' not in response.text


@pytest.mark.parametrize('data', [
    {'name': ' ', 'geos': ['DE'], 'offer_id': 9},
    {'name': 'x' * 255, 'geos': ['DE'], 'offer_id': 9},
    {'name': 'campaign1', 'geos': [], 'offer_id': 9},
    {'name': 'campaign1', 'geos': ['DE'], 'offer_id': True},
])
def test_invalid_input_has_readable_russian_error(creator, data):
    response = creator.post('/api/creator/preview', headers=HEADERS, json=data)
    assert response.status_code == 422
    assert isinstance(response.json()['detail'], str)
    assert any('а' <= char <= 'я' for char in response.json()['detail'])


def test_final_name_is_available_before_form_submission(creator):
    assert creator.get('/api/creator/name', params={'name': 'campaign1'}).json() == {'name': 'test_example_campaign1'}
    assert creator.get('/api/creator/name', params={'name': 'test_example_campaign1'}).json() == {'name': 'test_example_campaign1'}
    assert creator.get('/api/creator/name', params={'name': ' '}).status_code == 422


def test_preview_keeps_csrf_protection(creator):
    response = creator.post('/api/creator/preview', json={'name': 'x', 'geos': ['DE'], 'offer_id': 9})
    assert response.status_code == 403
