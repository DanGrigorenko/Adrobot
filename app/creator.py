"""Read-only Creator use cases. No operation or Keitaro write exists in this slice."""
import os

import pycountry
from pydantic import BaseModel, Field, StrictInt, field_validator


class PreviewInput(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    geos: list[str] = Field(min_length=1)
    offer_id: StrictInt = Field(gt=0)

    @field_validator('geos')
    @classmethod
    def valid_geos(cls, values):
        codes = list(dict.fromkeys(value.strip().upper() for value in values))
        if any(pycountry.countries.get(alpha_2=code) is None for code in codes):
            raise ValueError('GEO: укажите допустимые двухбуквенные коды стран ISO 3166-1')
        return codes


def settings(remote):
    result = {'name_prefix': os.getenv('CREATOR_NAME_PREFIX', '')}
    for key, env, catalogue in (
        ('domain', 'CREATOR_DOMAIN_ID', remote.domains),
        ('group', 'CREATOR_GROUP_ID', remote.groups),
        ('traffic_source', 'CREATOR_TRAFFIC_SOURCE_ID', remote.traffic_sources),
    ):
        try:
            entity_id = int(os.getenv(env, ''))
        except ValueError:
            raise ValueError(f'Настройка {env}: задайте положительный ID на сервере') from None
        if entity_id <= 0:
            raise ValueError(f'Настройка {env}: задайте положительный ID на сервере')
        matches = [item for item in catalogue() if item['id'] == entity_id]
        if len(matches) != 1:
            raise ValueError(f'Настройка {env} недоступна в Keitaro')
        result[key] = {'id': matches[0]['id'], 'name': matches[0]['name']}
    return result


def final_name(value):
    name = value.strip()
    if not name:
        raise ValueError('Название кампании не должно быть пустым')
    prefix = os.getenv('CREATOR_NAME_PREFIX', '')
    if not name.startswith(prefix):
        name = prefix + name
    if len(name) > 255:
        raise ValueError('Итоговое название с префиксом должно содержать не более 255 символов')
    return name


def preview(remote, data):
    config = settings(remote)
    name = final_name(data.name)
    offers = [offer for offer in remote.offers() if offer['id'] == data.offer_id]
    if len(offers) != 1:
        raise ValueError('Выбранный оффер недоступен в Keitaro; выберите другой')
    return {
        'name': name, 'geos': data.geos, 'settings': config,
        'campaign': {'state': 'disabled', 'type': 'position'},
        'regular': {'type': 'regular', 'schema': 'redirect', 'action_type': 'http',
                    'action_payload': 'https://google.com',
                    'filters': [{'name': 'country', 'mode': 'accept', 'payload': data.geos}]},
        'default': {'type': 'default', 'schema': 'landings', 'action_type': 'do_nothing',
                    'offers': [{'offer_id': data.offer_id, 'name': offers[0]['name'],
                                'share': 100, 'state': 'active'}]},
    }
