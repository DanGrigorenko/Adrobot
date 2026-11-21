"""Creator validation and durable, explicitly continued campaign creation."""
from threading import Lock
from uuid import UUID
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


class CreationInput(PreviewInput):
    operation_id: UUID


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


class Creator:
    def __init__(self, remote, store):
        self.remote, self.store = remote, store
        self._guard = Lock()
        self._running = set()

    def continue_operation(self, operation_id):
        with self._guard:
            if operation_id in self._running:
                raise RuntimeError('Операция уже выполняется. Перечитайте её состояние.')
            self._running.add(operation_id)
        try:
            op = self.store.creation(operation_id)
            if op['status'] == 'succeeded':
                return op
            try:
                self._run(op)
            except ConnectionError:
                op.update(status='unknown', reason='Ответ Keitaro не подтверждён. Проверьте ту же операцию; слепого повтора не будет.')
            except ValueError as error:
                op.update(status='blocked', reason=str(error))
            self.store.save_creation(op)
            return op
        finally:
            with self._guard:
                self._running.remove(operation_id)

    def _run(self, op):
        remote = self.remote
        if 'plan' not in op:
            summary = preview(remote, PreviewInput(**op['inputs']))
            campaign = dict(summary['campaign'], name=summary['name'], alias=op['alias'])
            for key in ('domain', 'group', 'traffic_source'):
                campaign[key + '_id'] = summary['settings'][key]['id']
            default = dict(summary['default'])
            default['offers'] = [{k: v for k, v in b.items() if k != 'name'} for b in default['offers']]
            op['plan'] = {'campaign': campaign, 'regular': summary['regular'], 'default': default}
            self.store.save_creation(op)
        plan = op['plan']
        if op['campaign_id'] is None:
            if not op.get('pending'):
                if any(c.get('alias') == op['alias'] for c in remote.campaigns()):
                    raise ValueError('Коллизия alias: кампания уже существует. Запись запрещена.')
                self._write(op, 'campaign', lambda: remote.create_campaign(plan['campaign']))
            campaigns = [c for c in remote.campaigns() if c.get('alias') == op['alias']]
            if len(campaigns) != 1:
                raise ValueError('Исход создания неизвестен: кампания по alias не найдена однозначно. Повтор запрещён.')
            campaign = remote.campaign(campaigns[0]['id'])
            self._matches(campaign, plan['campaign'], 'кампании')
            op['campaign_id'] = campaign['id']
            self._confirmed(op)
        for kind in ('regular', 'default'):
            if kind + '_id' not in op:
                if not op.get('pending'):
                    self._write(op, kind, lambda: remote.create_stream(op['campaign_id'], plan[kind]))
                streams = [s for s in remote.streams(op['campaign_id']) if s['type'] == kind]
                if len(streams) != 1:
                    raise ValueError('Исход создания неизвестен: поток не найден однозначно. Повтор запрещён.')
                self._matches(streams[0], plan[kind], 'потока')
                op[kind + '_id'] = streams[0]['id']
                self._confirmed(op)
        campaign = remote.campaign(op['campaign_id'])
        expected_campaign = dict(plan['campaign'])
        if op.get('pending') and op['step'] == 'enable':
            expected_campaign['state'] = campaign.get('state')
            if expected_campaign['state'] not in ('disabled', 'active'):
                raise ValueError('Неизвестное состояние кампании')
        self._matches(campaign, expected_campaign, 'кампании')
        streams = remote.streams(op['campaign_id'])
        if len(streams) != 2:
            raise ValueError('Ожидалось ровно два потока')
        for kind in ('regular', 'default'):
            matches = [s for s in streams if s['id'] == op[kind + '_id']]
            if len(matches) != 1 or any(matches[0].get(k) != v for k, v in plan[kind].items()):
                raise ValueError('Конфигурация потока не совпадает с сохранённой')
        if not op.get('pending'):
            self._write(op, 'enable', lambda: remote.enable_campaign(op['campaign_id']))
        if remote.campaign(op['campaign_id']).get('state') != 'active':
            raise ValueError('Активное состояние кампании не подтверждено')
        op.update(status='succeeded', step='done', pending=False, reason=None)

    @staticmethod
    def _matches(actual, expected, entity):
        if any(actual.get(k) != v for k, v in expected.items()):
            raise ValueError(f'Конфигурация {entity} не совпадает с сохранённой. Запись остановлена.')

    def _confirmed(self, op):
        op.update(pending=False, status='ready', reason=None)
        self.store.save_creation(op)

    def _write(self, op, step, write):
        # Recheck catalogue/configuration before every new write. Reads on a pending
        # step remain available even when configuration is temporarily unavailable.
        config = settings(self.remote)
        if not any(o['id'] == op['inputs']['offer_id'] for o in self.remote.offers()):
            raise ValueError('Выбранный оффер недоступен в Keitaro')
        for key in ('domain', 'group', 'traffic_source'):
            if config[key]['id'] != op['plan']['campaign'][key + '_id']:
                raise ValueError('Настройки сервера изменились после сохранения операции')
        if op['campaign_id'] is not None:
            self._matches(self.remote.campaign(op['campaign_id']), op['plan']['campaign'], 'кампании')
            streams = self.remote.streams(op['campaign_id'])
            known = {op[k + '_id']: op['plan'][k] for k in ('regular', 'default') if k + '_id' in op}
            if len(streams) != len(known) or {s['id'] for s in streams} != set(known):
                raise ValueError('Обнаружены неожиданные или отсутствующие потоки. Запись остановлена.')
            for stream in streams:
                self._matches(stream, known[stream['id']], 'потока')
        op.update(status='running', step=step, pending=True)
        self.store.save_creation(op)
        write()
