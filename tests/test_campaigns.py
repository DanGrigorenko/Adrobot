from fastapi.testclient import TestClient

from app.main import create_app


class KeitaroStub:
    def campaigns(self):
        return [
            {"id": 12, "name": "Summer sale"},
            {"id": 120, "name": "Winter sale"},
        ]


def test_campaign_streams_can_be_opened():
    class StreamsKeitaro(KeitaroStub):
        def streams(self, campaign_id):
            assert campaign_id == 12
            return [{'id': 7, 'name': 'Default', 'schema': 'landings', 'offers': [
                {'offer_id': 9, 'share': 70, 'state': 'active'},
                {'offer_id': 10, 'share': 30, 'state': 'disabled'},
            ]}]

    response = TestClient(create_app(StreamsKeitaro())).get('/api/campaigns/12/streams')
    assert response.status_code == 200
    assert response.json()[0]['offers'] == [
        {'offer_id': 9, 'share': 70, 'state': 'active'},
        {'offer_id': 10, 'share': 30, 'state': 'disabled'},
    ]


def test_campaign_search_matches_name_or_exact_id():
    client = TestClient(create_app(KeitaroStub()))
    assert client.get('/api/campaigns', params={'q': 'SUMMER'}).json() == [
        {'id': 12, 'name': 'Summer sale'}
    ]
    assert client.get('/api/campaigns', params={'q': '12'}).json() == [
        {'id': 12, 'name': 'Summer sale'}
    ]
    assert client.get('/api/campaigns', params={'q': 'missing'}).json() == []


def test_campaign_search_reports_upstream_failure_without_leaking_details():
    class UnavailableKeitaro:
        def campaigns(self):
            raise ConnectionError('sensitive upstream details')

    response = TestClient(create_app(UnavailableKeitaro())).get('/api/campaigns')
    assert response.status_code == 502
    assert 'sensitive upstream details' not in response.text
