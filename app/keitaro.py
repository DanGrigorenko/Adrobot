"""Small Admin API v1 adapter. Credentials never leave this boundary."""
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError


class KeitaroClient(Protocol):
    def campaigns(self) -> list[dict]: ...
    def streams(self, campaign_id: int) -> list[dict]: ...
    def offers(self) -> list[dict]: ...
    def domains(self) -> list[dict]: ...
    def groups(self) -> list[dict]: ...
    def traffic_sources(self) -> list[dict]: ...
    def campaign(self, campaign_id: int) -> dict: ...
    def create_campaign(self, payload: dict) -> None: ...
    def create_stream(self, campaign_id: int, payload: dict) -> None: ...
    def enable_campaign(self, campaign_id: int) -> None: ...


class NamedEntity(BaseModel):
    id: StrictInt
    name: str


class CampaignSummary(NamedEntity):
    alias: str | None = None


class Campaign(CampaignSummary):
    alias: str
    state: str
    type: str
    domain_id: StrictInt
    group_id: StrictInt
    traffic_source_id: StrictInt


class Binding(BaseModel):
    offer_id: StrictInt
    # Deliberately preserve invalid source shares; do not coerce or round.
    share: Any
    state: str


class Stream(BaseModel):
    model_config = ConfigDict(extra='ignore')
    id: StrictInt
    name: str = ''
    type: str
    stream_schema: str = Field(alias='schema')
    offers: list[Binding] = Field(default_factory=list)
    action_type: str | None = None
    action_payload: Any = None
    filters: list[dict] = Field(default_factory=list)


def validate_base_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError('KEITARO_BASE_URL должен быть HTTP(S) адресом без секрета, query и fragment')
    return value.rstrip('/')


class HttpKeitaro:
    def __init__(self, base_url, api_key):
        self.base_url = validate_base_url(base_url)
        if not api_key or api_key.strip() != api_key:
            raise ValueError('Задайте KEITARO_API_KEY на сервере')
        self._http = httpx.Client(
            base_url=self.base_url + '/admin_api/v1/',
            headers={'Api-Key': api_key, 'Accept': 'application/json'},
            timeout=httpx.Timeout(15, connect=5), follow_redirects=False, trust_env=False,
        )

    def _list(self, path, schema, params=None):
        try:
            response = self._http.get(path, params=params)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, list):
                raise ValueError('Expected a list')
            return [schema.model_validate(item).model_dump(by_alias=True) for item in data]
        except (httpx.HTTPError, ValueError, ValidationError):
            # Never expose upstream bodies, request headers, URLs or key in errors.
            raise ConnectionError('Keitaro недоступен или вернул некорректный ответ') from None

    def campaigns(self):
        results = []
        seen = set()
        while True:
            page = self._list('campaigns', CampaignSummary, {'limit': 100, 'offset': len(results)})
            if not page:
                return results
            ids = {c['id'] for c in page}
            # Fail closed on broken pagination rather than loop or silently omit campaigns.
            if ids & seen or len(ids) != len(page):
                raise ConnectionError('Некорректная пагинация Keitaro')
            seen.update(ids)
            results.extend(page)

    def streams(self, campaign_id):
        return self._list(f'campaigns/{campaign_id}/streams', Stream)

    def offers(self):
        return self._list('offers', NamedEntity)

    def domains(self):
        return self._list('domains', NamedEntity)

    def groups(self):
        return self._list('groups', NamedEntity, {'type': 'campaigns'})

    def traffic_sources(self):
        return self._list('traffic_sources', NamedEntity)

    def campaign(self, campaign_id):
        try:
            response = self._http.get(f'campaigns/{campaign_id}')
            response.raise_for_status()
            return Campaign.model_validate(response.json()).model_dump()
        except (httpx.HTTPError, ValueError, ValidationError):
            raise ConnectionError('Не удалось проверить кампанию в Keitaro') from None

    def _post(self, path, **kwargs):
        try:
            response = self._http.post(path, **kwargs)
            response.raise_for_status()
        except httpx.HTTPError:
            # Even an error status does not prove that a write was not applied.
            raise ConnectionError('Исход записи Keitaro не подтверждён') from None

    def create_campaign(self, payload):
        self._post('campaigns', json=payload)

    def create_stream(self, campaign_id, payload):
        self._post(f'campaigns/{campaign_id}/streams', json=payload)

    def enable_campaign(self, campaign_id):
        self._post(f'campaigns/{campaign_id}/enable')

    def close(self):
        self._http.close()
