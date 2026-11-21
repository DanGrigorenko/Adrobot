from contextlib import asynccontextmanager
import os
from pathlib import Path
from urllib.parse import urlsplit
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, StrictInt
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.creator import PreviewInput, final_name, preview, settings
from app.editor import open_streams
from app.keitaro import HttpKeitaro, validate_base_url
from app.storage import DraftStore


class DraftAction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: StrictInt
    action: Literal['share', 'pin', 'unpin', 'add', 'remove', 'bring_back']
    offer_id: StrictInt
    share: StrictInt | None = None


def create_app(keitaro=None, *, database_url=None, keitaro_url=None, frontend_dir=None):
    base_url = validate_base_url(keitaro_url or os.getenv('KEITARO_BASE_URL', 'https://keitaro.example'))
    owned_client = keitaro is None
    if owned_client:
        keitaro = HttpKeitaro(base_url, os.getenv('KEITARO_API_KEY', ''))
    store = DraftStore(database_url or os.getenv('DATABASE_URL', 'sqlite:///./adrobot.db'))
    origin = os.getenv('APP_ORIGIN', 'http://127.0.0.1:8000').rstrip('/')

    @asynccontextmanager
    async def lifespan(app):
        try:
            # Migration failure aborts ASGI startup before any requests are served.
            store.migrate()
            yield
        finally:
            store.close()
            if owned_client:
                keitaro.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[
        '127.0.0.1', 'localhost', 'testserver', urlsplit(origin).hostname or 'localhost',
    ])

    @app.middleware('http')
    async def browser_security(request: Request, call_next):
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            if (request.headers.get('origin') != origin
                    or request.headers.get('x-adrobot-request') != '1'
                    or request.headers.get('sec-fetch-site') == 'cross-site'):
                return JSONResponse({'detail': 'Запрос отклонён: проверка CSRF'}, status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error):
        messages = {
            'name': 'Название: введите от 1 до 255 символов',
            'geos': 'GEO: выберите хотя бы одну страну; допустимы двухбуквенные коды ISO 3166-1',
            'offer_id': 'Оффер: выберите существующий оффер с положительным целым ID',
        }
        details = list(dict.fromkeys(messages.get(str(item['loc'][-1]), 'Некорректный формат запроса')
                                    for item in error.errors()))
        return JSONResponse({'detail': '; '.join(details)}, status_code=422)

    @app.get('/api/health')
    def health():
        return {'status': 'ok'}

    @app.get('/api/offers')
    def offers(q: str = ''):
        query = q.strip().casefold()
        try:
            return [offer for offer in keitaro.offers()
                    if not query or query in offer['name'].casefold() or query == str(offer['id'])]
        except ConnectionError:
            raise HTTPException(status_code=502, detail='Не удалось загрузить каталог офферов из Keitaro') from None

    @app.get('/api/creator/name')
    def creator_name(name: str = ''):
        try:
            return {'name': final_name(name)}
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None

    @app.get('/api/creator/settings')
    def creator_settings():
        try:
            return settings(keitaro)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        except ConnectionError:
            raise HTTPException(status_code=502, detail='Не удалось проверить настройки в Keitaro') from None

    @app.post('/api/creator/preview')
    def creator_preview(data: PreviewInput):
        try:
            return preview(keitaro, data)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        except ConnectionError:
            raise HTTPException(status_code=502, detail='Не удалось проверить настройки или оффер в Keitaro') from None

    @app.get('/api/campaigns')
    def campaigns(q: str = ''):
        query = q.strip().casefold()
        try:
            results = keitaro.campaigns()
        except ConnectionError:
            raise HTTPException(status_code=502, detail='Keitaro недоступен или вернул некорректный ответ') from None
        return [
            {'id': c['id'], 'name': c['name'], 'keitaro_url': f"{base_url}/admin/#!/campaigns/{c['id']}"}
            for c in results
            if not query or query in c['name'].casefold() or query == str(c['id'])
        ]

    @app.get('/api/campaigns/{campaign_id}/streams')
    def streams(campaign_id: int):
        if campaign_id <= 0:
            raise HTTPException(status_code=422, detail='ID кампании должен быть положительным')
        try:
            result = open_streams(keitaro, store, campaign_id)
        except ConnectionError:
            raise HTTPException(status_code=502, detail='Keitaro недоступен или вернул некорректный ответ') from None
        for stream in result:
            stream['keitaro_url'] = f'{base_url}/admin/#!/campaigns/{campaign_id}'
        return result

    @app.post('/api/campaigns/{campaign_id}/streams/{stream_id}/actions')
    def edit(campaign_id: int, stream_id: int, body: DraftAction):
        try:
            remote = next((s for s in keitaro.streams(campaign_id) if s['id'] == stream_id), None)
            if remote is None or remote.get('type') not in ('regular', 'default') or remote.get('schema') != 'landings':
                raise HTTPException(status_code=409, detail='Поток больше не поддерживает редактирование офферов')
            catalogue = {o['id'] for o in keitaro.offers()} if body.action in ('add', 'bring_back') else None
            return store.edit(campaign_id, stream_id, body.version, body.action, body.offer_id, body.share, catalogue)
        except ConnectionError:
            raise HTTPException(status_code=502, detail='Не удалось проверить доступность потока в Keitaro') from None
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from None
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None

    build = Path(frontend_dir or os.getenv('FRONTEND_DIR', 'frontend/dist'))
    if (build / 'assets').is_dir():
        app.mount('/assets', StaticFiles(directory=build / 'assets'), name='assets')

    @app.get('/', include_in_schema=False)
    def index():
        if not (build / 'index.html').is_file():
            raise HTTPException(status_code=503, detail='Интерфейс не собран')
        return FileResponse(build / 'index.html', headers={'Cache-Control': 'no-cache'})

    return app
