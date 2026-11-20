from fastapi.testclient import TestClient

from app.main import create_app


class KeitaroStub:
    def campaigns(self):
        return [
            {"id": 12, "name": "Summer sale"},
            {"id": 120, "name": "Winter sale"},
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
