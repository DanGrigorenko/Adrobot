from contextlib import ExitStack

from fastapi.testclient import TestClient
import pytest

from app.main import create_app


class KeitaroStub:
    def offers(self):
        return []

    def campaigns(self):
        return [
            {'id': 12, 'name': 'Summer sale'},
            {'id': 120, 'name': 'Winter sale'},
        ]


@pytest.fixture
def client_factory(tmp_path):
    with ExitStack() as stack:
        def make(remote):
            return stack.enter_context(TestClient(create_app(
                remote, database_url=f"sqlite:///{tmp_path / 'drafts.db'}")))
        yield make


def test_campaign_streams_can_be_opened(client_factory):
    class StreamsKeitaro(KeitaroStub):
        def streams(self, campaign_id):
            return [{'id': 7, 'name': 'Default', 'type': 'default', 'schema': 'landings', 'offers': [
                {'offer_id': 9, 'share': 70, 'state': 'active'},
                {'offer_id': 10, 'share': 30, 'state': 'disabled'},
            ]}] if campaign_id == 12 else []

    response = client_factory(StreamsKeitaro()).get('/api/campaigns/12/streams')
    assert response.status_code == 200
    assert [{k: b[k] for k in ('offer_id', 'share', 'state')} for b in response.json()[0]['offers']] == [
        {'offer_id': 9, 'share': 70, 'state': 'active'},
        {'offer_id': 10, 'share': 30, 'state': 'disabled'},
    ]


def test_campaign_search_matches_name_or_exact_id(client_factory):
    client = client_factory(KeitaroStub())
    expected = [{'id': 12, 'name': 'Summer sale', 'keitaro_url': 'https://keitaro.example/admin/#!/campaigns/12'}]
    assert client.get('/api/campaigns', params={'q': 'SUMMER'}).json() == expected
    assert client.get('/api/campaigns', params={'q': ' 12 '}).json() == expected
    assert client.get('/api/campaigns', params={'q': 'missing'}).json() == []
    assert [c['id'] for c in client.get('/api/campaigns').json()] == [12, 120]


def test_campaign_search_reports_upstream_failure_without_leaking_details(client_factory):
    class UnavailableKeitaro:
        def campaigns(self):
            raise ConnectionError('sensitive upstream details')

    response = client_factory(UnavailableKeitaro()).get('/api/campaigns')
    assert response.status_code == 502
    assert 'sensitive upstream details' not in response.text


def test_empty_campaign_source_is_a_successful_empty_result(client_factory):
    class EmptyKeitaro:
        def campaigns(self):
            return []
    response = client_factory(EmptyKeitaro()).get('/api/campaigns')
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.parametrize('headers', [
    {'Origin': 'https://attacker.invalid', 'X-Adrobot-Request': '1'},
    {},
    {'Origin': 'http://127.0.0.1:8000'},
    {'Origin': 'http://127.0.0.1:8000', 'X-Adrobot-Request': '1', 'Sec-Fetch-Site': 'cross-site'},
])
def test_browser_mutations_fail_closed_on_csrf(client_factory, headers):
    response = client_factory(KeitaroStub()).post('/api/campaigns', headers=headers)
    assert response.status_code == 403


def test_same_origin_does_not_make_unimplemented_mutations_available(client_factory):
    response = client_factory(KeitaroStub()).post('/api/campaigns', headers={
        'Origin': 'http://127.0.0.1:8000', 'X-Adrobot-Request': '1',
    })
    assert response.status_code == 405


def test_dns_rebinding_host_is_rejected_and_api_has_no_cache(client_factory):
    client = client_factory(KeitaroStub())
    assert client.get('/api/campaigns', headers={'Host': 'attacker.invalid'}).status_code == 400
    response = client.get('/api/campaigns')
    assert response.headers['cache-control'] == 'no-store'
    assert "frame-ancestors 'none'" in response.headers['content-security-policy']
    assert client.get('/api/unknown').status_code == 404
