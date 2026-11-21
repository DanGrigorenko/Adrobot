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
    compose = ['docker', 'compose', '-p', 'adrobot-issue14-test', '-f', 'compose.yml', '-f', 'compose.test.yml']
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
            headers = {'Origin': app_url, 'X-Adrobot-Request': '1'}
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
            httpx.post(f'{fixture_url}/control', json={'mode': 'lose-regular'}).raise_for_status()
            created = client.post('/api/creator/operations', headers=headers, json={
                'operation_id': 'fe795f9d-0920-4572-a17f-d27ab4f3f096',
                'name': 'Packaged campaign', 'geos': ['DE', 'FR'], 'offer_id': 9,
            })
            assert created.status_code == 200
            creation_path = f"/api/creator/operations/{created.json()['id']}/continue"
            unknown = client.post(creation_path, headers=headers).json()
            assert unknown['status'] == 'unknown', unknown
            assert unknown['step'] == 'regular'
            run('up', '-d', '--force-recreate', 'app')
            wait_ready()
            assert client.get('/api/creator/operations').json() == [unknown]
            completed = client.post(creation_path, headers=headers).json()
            assert completed['status'] == 'succeeded', completed
            assert completed['keitaro_url'].endswith('/campaigns/100')
            assert client.post(creation_path, headers=headers).json() == completed
            evidence = httpx.get(f'{fixture_url}/control').json()
            assert evidence['writes'] == ['campaign', 'regular', 'default', 'enable']
            assert evidence['campaigns'][0]['state'] == 'active'
            assert len(evidence['streams']) == 2
            httpx.post(f'{fixture_url}/control', json={'mode': 'initial'}).raise_for_status()
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
            assert client.get('/api/offers', params={'q': '11'}).json() == [{'id': 11, 'name': 'Оффер В'}]
            added = client.post(path, headers=headers, json={'version': 2, 'action': 'add', 'offer_id': 11})
            assert added.status_code == 200
            assert [b['share'] for b in added.json()['bindings']] == [50, 25, 25]
            removed = client.post(path, headers=headers, json={'version': 3, 'action': 'remove', 'offer_id': offer_id})
            assert removed.status_code == 200
            draft = removed.json()
            assert draft['removed'] == [dict(offer_id=offer_id, share=50, state='active', pinned=True)]
            assert [b['share'] for b in draft['bindings']] == [50, 50]
            httpx.post(f'{fixture_url}/control', json={'mode': 'changed'}).raise_for_status()
            run('up', '-d', '--force-recreate', 'app')
            wait_ready()
            reopened = client.get('/api/campaigns/12/streams').json()[0]
            assert reopened['draft'] == draft
            assert reopened['conflict'] is True
            returned = client.post(path, headers=headers, json={'version': 4, 'action': 'bring_back', 'offer_id': offer_id})
            assert returned.status_code == 200
            assert returned.json()['removed'] == []
            assert [(b['offer_id'], b['share'], b['pinned']) for b in returned.json()['bindings']] == [(10, 25, False), (11, 25, False), (offer_id, 50, True)]
            httpx.post(f'{fixture_url}/control', json={'mode': 'failure'}).raise_for_status()
            failure = client.get('/api/campaigns')
            assert failure.status_code == 502
            assert 'fixture-only-secret' not in failure.text
            cancel_path = '/api/campaigns/12/streams/7/cancel'
            assert client.post(cancel_path, headers=headers, json={'version': 5, 'confirmed': True}).status_code == 502
            httpx.post(f'{fixture_url}/control', json={'mode': 'changed'}).raise_for_status()
            assert client.get('/api/campaigns/12/streams').json()[0]['draft'] == returned.json()
            assert client.post(cancel_path, headers=headers, json={'version': 4, 'confirmed': True}).status_code == 409
            accepted = client.post(cancel_path, headers=headers, json={'version': 5, 'confirmed': True})
            assert accepted.status_code == 200
            assert accepted.json()['bindings'] == [dict(offer_id=9, share=100, state='active', pinned=False)]
            assert accepted.json()['removed'] == []
            assert accepted.json()['notices']
            assert client.get('/api/campaigns/12/streams').json()[0]['conflict'] is False
            run('up', '-d', '--force-recreate', 'app')
            wait_ready()
            assert client.get('/api/campaigns/12/streams').json()[0]['draft'] == accepted.json()
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
