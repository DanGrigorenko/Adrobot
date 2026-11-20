"""Explicit opt-in packaged HTTP acceptance; never uses a live Keitaro instance."""
import os
from pathlib import Path
import subprocess
import time

import httpx
import pytest

pytestmark = [pytest.mark.compose, pytest.mark.skipif(
    os.getenv('COMPOSE_TEST') != '1', reason='Set COMPOSE_TEST=1 to build and test Docker Compose')]
ROOT = Path(__file__).resolve().parents[1]


def test_packaged_ui_real_adapter_and_drafts_survive_container_recreation():
    compose = ['docker', 'compose', '-p', 'adrobot-issue9-test', '-f', 'compose.yml', '-f', 'compose.test.yml']
    def run(*args, check=True):
        return subprocess.run(compose + list(args), cwd=ROOT, check=check, capture_output=True, text=True)

    def wait_ready():
        for _ in range(90):
            try:
                response = httpx.get('http://127.0.0.1:18080/api/health', timeout=2)
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(1)
        pytest.fail('Container failed to start: ' + run('logs', '--no-color', check=False).stdout)

    try:
        run('up', '--build', '-d')
        wait_ready()
        with httpx.Client(base_url='http://127.0.0.1:18080', timeout=10) as client:
            ui = client.get('/')
            assert ui.status_code == 200
            assert '<html lang="ru">' in ui.text
            assert '/assets/' in ui.text
            asset = ui.text.split('src="')[1].split('"')[0]
            assert client.get(asset).status_code == 200
            assert client.get('/api/campaigns', params={'q': 'ЛЕТО'}).json()[0]['id'] == 12
            assert [c['id'] for c in client.get('/api/campaigns', params={'q': '12'}).json()] == [12]
            assert [c['id'] for c in client.get('/api/campaigns', params={'q': '120'}).json()] == [120]
            assert client.get('/api/campaigns', params={'q': 'missing'}).json() == []
            headers = {'Origin': 'http://127.0.0.1:18080', 'X-Adrobot-Request': '1'}
            settings = client.get('/api/creator/settings')
            assert settings.status_code == 200
            assert settings.json()['domain']['name'] == 'tracking.example'
            assert client.get('/api/offers', params={'q': '9'}).json() == [{'id': 9, 'name': 'Оффер А'}]
            preview = client.post('/api/creator/preview', headers=headers, json={
                'name': 'campaign1', 'geos': ['DE', 'FR', 'DE'], 'offer_id': 9})
            assert preview.status_code == 200
            assert preview.json()['name'] == 'test_example_campaign1'
            assert preview.json()['regular']['filters'][0]['payload'] == ['DE', 'FR']
            assert preview.json()['default']['offers'][0]['share'] == 100
            assert client.post('/api/creator/preview', headers=headers, json={
                'name': 'campaign1', 'geos': ['XX'], 'offer_id': 9}).status_code == 422
            assert client.post('/api/campaigns', headers=headers, json={}).status_code == 405
            streams = client.get('/api/campaigns/12/streams').json()
            assert streams[0]['offers'][0]['name'] == 'Оффер А'
            assert streams[0]['draft']['base'][0]['share'] == 70
            assert streams[0]['keitaro_url'] == 'http://fixture:8081/admin/#!/campaigns/12'
            assert streams[1]['draft'] is None
            assert streams[1]['editable'] is False
            draft = streams[0]['draft']
            httpx.post('http://127.0.0.1:18081/control', json={'mode': 'changed'}).raise_for_status()
            run('up', '-d', '--force-recreate', 'app')
            wait_ready()
            reopened = client.get('/api/campaigns/12/streams').json()[0]
            assert reopened['draft'] == draft
            assert reopened['conflict'] is True
            httpx.post('http://127.0.0.1:18081/control', json={'mode': 'failure'}).raise_for_status()
            failure = client.get('/api/campaigns')
            assert failure.status_code == 502
            assert 'fixture-only-secret' not in failure.text
            httpx.post('http://127.0.0.1:18081/control', json={'mode': 'malformed'}).raise_for_status()
            assert client.get('/api/campaigns').status_code == 502
            for mode, path in [('invalid-json', '/api/campaigns'), ('stuck-pagination', '/api/campaigns'),
                               ('bad-stream', '/api/campaigns/12/streams'), ('bad-catalogue', '/api/campaigns/12/streams')]:
                httpx.post('http://127.0.0.1:18081/control', json={'mode': mode}).raise_for_status()
                response = client.get(path)
                assert response.status_code == 502, mode
                assert 'fixture-only-secret' not in response.text
            httpx.post('http://127.0.0.1:18081/control', json={'mode': 'initial'}).raise_for_status()
            assert client.get('/api/campaigns/120/streams').json() == []
            assert client.post('/api/campaigns', headers={'Origin': 'https://attacker.invalid'}).status_code == 403
            assert client.get('/api/not-found').status_code == 404
        # A startup migration error must fail closed, not serve a partially initialized API.
        failure = run('run', '--rm', '-e', 'DATABASE_URL=sqlite:////proc/not-writable/db.sqlite', 'app', check=False)
        assert failure.returncode != 0
    except subprocess.CalledProcessError as exc:
        pytest.fail(f'Compose failed: {exc.stdout}\n{exc.stderr}')
    finally:
        run('down', '--volumes', '--remove-orphans', check=False)
