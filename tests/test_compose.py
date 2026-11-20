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
    compose = ['docker', 'compose', '-p', 'adrobot-issue12-test', '-f', 'compose.yml', '-f', 'compose.test.yml']
    app_url = f"http://127.0.0.1:{os.getenv('TEST_APP_PORT', '18080')}"
    fixture_url = f"http://127.0.0.1:{os.getenv('TEST_FIXTURE_PORT', '18081')}"
    def run(*args, check=True):
        return subprocess.run(compose + list(args), cwd=ROOT, check=check, capture_output=True, text=True)

    def wait_ready():
        for _ in range(90):
            try:
                response = httpx.get(f'{app_url}/api/health', timeout=2)
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(1)
        pytest.fail('Container failed to start: ' + run('logs', '--no-color', check=False).stdout)

    try:
        run('up', '--build', '-d')
        wait_ready()
        with httpx.Client(base_url=app_url, timeout=10) as client:
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
            streams = client.get('/api/campaigns/12/streams').json()
            assert streams[0]['offers'][0]['name'] == 'Оффер А'
            assert streams[0]['draft']['base'][0]['share'] == 70
            assert streams[0]['keitaro_url'] == 'http://fixture:8081/admin/#!/campaigns/12'
            assert streams[1]['draft'] is None
            assert streams[1]['editable'] is False
            path = f"/api/campaigns/12/streams/{streams[0]['id']}/actions"
            offer_id = streams[0]['draft']['bindings'][0]['offer_id']
            headers = {'Origin': app_url, 'X-Adrobot-Request': '1'}
            assert client.post(path, json={'version': 1, 'action': 'pin', 'offer_id': offer_id}).status_code == 403
            saved = client.post(path, headers=headers, json={
                'version': 1, 'action': 'share', 'offer_id': offer_id, 'share': 50,
            })
            assert saved.status_code == 200
            draft = saved.json()
            assert draft['version'] == 2
            assert [(b['share'], b['pinned']) for b in draft['bindings']] == [(50, True), (50, False)]
            assert client.post(path, headers=headers, json={
                'version': 1, 'action': 'share', 'offer_id': offer_id, 'share': 40,
            }).status_code == 409
            assert client.get('/api/campaigns/12/streams').json()[0]['offers'][0]['share'] == 70
            httpx.post(f'{fixture_url}/control', json={'mode': 'changed'}).raise_for_status()
            run('up', '-d', '--force-recreate', 'app')
            wait_ready()
            reopened = client.get('/api/campaigns/12/streams').json()[0]
            assert reopened['draft'] == draft
            assert reopened['conflict'] is True
            httpx.post(f'{fixture_url}/control', json={'mode': 'failure'}).raise_for_status()
            failure = client.get('/api/campaigns')
            assert failure.status_code == 502
            assert 'fixture-only-secret' not in failure.text
            httpx.post(f'{fixture_url}/control', json={'mode': 'malformed'}).raise_for_status()
            assert client.get('/api/campaigns').status_code == 502
            for mode, path in [('invalid-json', '/api/campaigns'), ('stuck-pagination', '/api/campaigns'),
                               ('bad-stream', '/api/campaigns/12/streams'), ('bad-catalogue', '/api/campaigns/12/streams')]:
                httpx.post(f'{fixture_url}/control', json={'mode': mode}).raise_for_status()
                response = client.get(path)
                assert response.status_code == 502, mode
                assert 'fixture-only-secret' not in response.text
            httpx.post(f'{fixture_url}/control', json={'mode': 'initial'}).raise_for_status()
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
