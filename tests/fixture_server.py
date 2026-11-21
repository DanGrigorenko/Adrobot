"""Local fake HTTP Keitaro for opt-in packaged acceptance, not a live integration."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from urllib.parse import parse_qs, urlsplit

mode = 'initial'
created_campaigns = []
created_streams = []
writes = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def do_POST(self):
        global mode
        raw = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        if self.path == '/control':
            mode = json.loads(raw)['mode']
            return self.send_json({'mode': mode})
        if self.headers.get('Api-Key') != 'fixture-only-secret':
            return self.send_json({}, 401)
        if self.path == '/admin_api/v1/campaigns':
            created_campaigns.append(dict(json.loads(raw), id=100))
            writes.append('campaign')
        elif self.path == '/admin_api/v1/campaigns/100/streams':
            stream = dict(json.loads(raw), id=200 + len(created_streams))
            created_streams.append(stream)
            writes.append(stream['type'])
            if mode == 'lose-regular' and stream['type'] == 'regular':
                mode = 'initial'
                # A disconnected response after application, not a real Keitaro write.
                self.close_connection = True
                return
        elif self.path == '/admin_api/v1/campaigns/100/enable':
            if raw or len(created_streams) != 2:
                return self.send_json({'error': 'enable must be bodyless and follow both streams'}, 400)
            created_campaigns[0]['state'] = 'active'
            writes.append('enable')
        else:
            return self.send_json({}, 404)
        self.send_json({})

    def do_GET(self):
        if self.path == '/control':
            return self.send_json({'campaigns': created_campaigns, 'streams': created_streams, 'writes': writes})
        if self.headers.get('Api-Key') != 'fixture-only-secret':
            return self.send_json({'error': 'missing or wrong API key'}, 401)
        if mode == 'failure':
            return self.send_json({'error': 'fixture-only-secret'}, 500)
        if mode == 'malformed':
            return self.send_json({'unexpected': 'fixture-only-secret'})
        if mode == 'invalid-json':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            return self.wfile.write(b'<html>fixture-only-secret</html>')
        if mode == 'stuck-pagination':
            return self.send_json([{'id': 12, 'name': 'Repeated page'}])
        if mode == 'bad-stream' and self.path.endswith('/streams'):
            return self.send_json([{'id': 7, 'type': 'default', 'schema': 'landings',
                                    'offers': [{'offer_id': 9, 'state': 'active'}]}])
        if mode == 'bad-catalogue' and self.path.endswith('/offers'):
            return self.send_json([{'id': 9}])
        if urlsplit(self.path).path == '/admin_api/v1/campaigns':
            # Simulate a server capped at one result per page, regardless of requested limit.
            offset = int(parse_qs(urlsplit(self.path).query).get('offset', ['0'])[0])
            campaigns = [{'id': 12, 'name': 'Лето — основные офферы'}, {'id': 120, 'name': 'Осень'}] + created_campaigns
            return self.send_json(campaigns[offset:offset + 1])
        if self.path == '/admin_api/v1/campaigns/100':
            return self.send_json(created_campaigns[0] if created_campaigns else {}, 200 if created_campaigns else 404)
        if self.path == '/admin_api/v1/campaigns/100/streams':
            return self.send_json(created_streams)
        if self.path == '/admin_api/v1/domains':
            return self.send_json([{'id': 2, 'name': 'tracking.example'}])
        if self.path == '/admin_api/v1/groups?type=campaigns':
            return self.send_json([{'id': 3, 'name': 'Test campaigns'}])
        if self.path == '/admin_api/v1/traffic_sources':
            return self.send_json([{'id': 4, 'name': 'Test source'}])
        if self.path == '/admin_api/v1/offers':
            return self.send_json([{'id': 9, 'name': 'Оффер А'}, {'id': 10, 'name': 'Оффер Б'}])
        if self.path == '/admin_api/v1/campaigns/12/streams':
            offers = [{'offer_id': 9, 'share': 70, 'state': 'active'}, {'offer_id': 10, 'share': 30, 'state': 'active'}]
            if mode == 'changed':
                offers = [{'offer_id': 9, 'share': 100, 'state': 'active'}]
            return self.send_json([
                {'id': 7, 'name': 'Основное распределение', 'type': 'default', 'schema': 'landings', 'offers': offers},
                {'id': 8, 'name': 'GEO → Google', 'type': 'regular', 'schema': 'redirect', 'offers': []},
                {'id': 9, 'name': 'Проверка долей', 'type': 'regular', 'schema': 'landings', 'offers': [
                    {'offer_id': 9, 'share': 20, 'state': 'active'}, {'offer_id': 10, 'share': 0, 'state': 'active'}]},
                {'id': 10, 'name': 'Отключённая привязка', 'type': 'regular', 'schema': 'landings', 'offers': [
                    {'offer_id': 9, 'share': 100, 'state': 'active'}, {'offer_id': 10, 'share': 30, 'state': 'disabled'}]},
            ])
        if self.path == '/admin_api/v1/campaigns/120/streams':
            return self.send_json([])
        self.send_json({}, 404)


if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', 8081), Handler).serve_forever()
