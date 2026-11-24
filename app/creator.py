"""Creator validation and durable, explicitly continued campaign creation."""
from threading import Lock
from uuid import UUID
import os
import hashlib
import json

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
            raise ValueError('GEO: enter valid two-letter ISO 3166-1 country codes')
        return codes


class CreationInput(PreviewInput):
    operation_id: UUID
    preview_token: str


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
            raise ValueError(f'Setting {env}: configure a positive ID on the server') from None
        if entity_id <= 0:
            raise ValueError(f'Setting {env}: configure a positive ID on the server')
        matches = [item for item in catalogue() if item['id'] == entity_id]
        if len(matches) != 1:
            raise ValueError(f'Setting {env} is unavailable in Keitaro')
        result[key] = {'id': matches[0]['id'], 'name': matches[0]['name']}
    return result


def final_name(value):
    name = value.strip()
    if not name:
        raise ValueError('Campaign name must not be empty')
    prefix = os.getenv('CREATOR_NAME_PREFIX', '')
    if not name.startswith(prefix):
        name = prefix + name
    if len(name) > 255:
        raise ValueError('The final name including the prefix must not exceed 255 characters')
    return name


def preview(remote, data):
    config = settings(remote)
    name = final_name(data.name)
    offers = [offer for offer in remote.offers() if offer['id'] == data.offer_id]
    if len(offers) != 1:
        raise ValueError('The selected offer is unavailable in Keitaro; choose another')
    summary = {
        'name': name, 'geos': data.geos, 'settings': config,
        'campaign': {'state': 'disabled', 'type': 'position'},
        'regular': {'name': 'GEO → Google', 'type': 'regular', 'schema': 'redirect', 'action_type': 'http',
                    'action_payload': 'https://google.com',
                    'filters': [{'name': 'country', 'mode': 'accept', 'payload': data.geos}]},
        'default': {'name': 'Default distribution', 'type': 'default', 'schema': 'landings', 'action_type': 'do_nothing',
                    'offers': [{'offer_id': data.offer_id, 'name': offers[0]['name'],
                                'share': 100, 'state': 'active'}]},
    }
    # Content identity survives restarts without retaining a process-local preview.
    summary['preview_token'] = hashlib.sha256(
        json.dumps(summary, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return summary


class Creator:
    def __init__(self, remote, store):
        self.remote, self.store = remote, store
        self._guard = Lock()
        self._running = set()

    def continue_operation(self, operation_id):
        with self._guard:
            if operation_id in self._running:
                raise RuntimeError('The operation is already in progress. Reload its status.')
            self._running.add(operation_id)
        try:
            op = self.store.creation(operation_id)
            if op['status'] == 'succeeded':
                return op
            try:
                self._run(op)
            except ConnectionError:
                op.update(status='unknown', reason='The Keitaro response could not be confirmed. Check the same operation; it will not be retried blindly.')
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
            summary = self._reviewed_preview(op)
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
                    raise ValueError('Alias collision: the campaign already exists. Writing is blocked.')
                self._write(op, 'campaign', lambda: remote.create_campaign(plan['campaign']))
            campaigns = [c for c in remote.campaigns() if c.get('alias') == op['alias']]
            if len(campaigns) != 1:
                raise ValueError('Creation outcome is unknown: the campaign could not be uniquely identified by alias. Retrying is blocked.')
            campaign = remote.campaign(campaigns[0]['id'])
            self._matches(campaign, plan['campaign'], 'campaign')
            op['campaign_id'] = campaign['id']
            self._confirmed(op)
        for kind in ('regular', 'default'):
            if kind + '_id' not in op:
                if not op.get('pending'):
                    self._write(op, kind, lambda: remote.create_stream(op['campaign_id'], plan[kind]))
                streams = [s for s in remote.streams(op['campaign_id']) if s['type'] == kind]
                if len(streams) != 1:
                    raise ValueError('Creation outcome is unknown: the stream could not be uniquely identified. Retrying is blocked.')
                self._matches(streams[0], plan[kind], 'stream')
                op[kind + '_id'] = streams[0]['id']
                self._confirmed(op)
        campaign = remote.campaign(op['campaign_id'])
        expected_campaign = dict(plan['campaign'])
        if op.get('pending') and op['step'] == 'enable':
            expected_campaign['state'] = campaign.get('state')
            if expected_campaign['state'] not in ('disabled', 'active'):
                raise ValueError('Unknown campaign status')
        self._matches(campaign, expected_campaign, 'campaign')
        streams = remote.streams(op['campaign_id'])
        if len(streams) != 2:
            raise ValueError('Expected exactly two streams')
        for kind in ('regular', 'default'):
            matches = [s for s in streams if s['id'] == op[kind + '_id']]
            if len(matches) != 1:
                raise ValueError('The stream configuration does not match the saved configuration')
            self._matches(matches[0], plan[kind], 'stream')
        if not op.get('pending'):
            self._write(op, 'enable', lambda: remote.enable_campaign(op['campaign_id']))
        if remote.campaign(op['campaign_id']).get('state') != 'active':
            raise ValueError('The campaign could not be confirmed as active')
        op.update(status='succeeded', step='done', pending=False, reason=None)

    @staticmethod
    def _matches(actual, expected, entity):
        def contains(actual, expected):
            if isinstance(expected, dict):
                return isinstance(actual, dict) and all(
                    key in actual and contains(actual[key], value) for key, value in expected.items())
            if isinstance(expected, list):
                return isinstance(actual, list) and len(actual) == len(expected) and all(
                    contains(a, e) for a, e in zip(actual, expected))
            return type(actual) is type(expected) and actual == expected

        if not contains(actual, expected):
            raise ValueError(f'The {entity} configuration does not match the saved configuration. Writing has stopped.')

    def _confirmed(self, op):
        op.update(pending=False, status='ready', reason=None)
        self.store.save_creation(op)

    def _reviewed_preview(self, op):
        summary = preview(self.remote, PreviewInput(**op['inputs']))
        if op['inputs'].get('preview_token') != summary['preview_token']:
            raise ValueError('The reviewed summary has changed. Review and confirm the new summary before creation.')
        return summary

    def _write(self, op, step, write):
        # Recheck catalogue/configuration before every new write. Reads on a pending
        # step remain available even when configuration is temporarily unavailable.
        config = self._reviewed_preview(op)['settings']
        for key in ('domain', 'group', 'traffic_source'):
            if config[key]['id'] != op['plan']['campaign'][key + '_id']:
                raise ValueError('Server settings changed after the operation was saved')
        if op['campaign_id'] is not None:
            self._matches(self.remote.campaign(op['campaign_id']), op['plan']['campaign'], 'campaign')
            streams = self.remote.streams(op['campaign_id'])
            known = {op[k + '_id']: op['plan'][k] for k in ('regular', 'default') if k + '_id' in op}
            if len(streams) != len(known) or {s['id'] for s in streams} != set(known):
                raise ValueError('Unexpected or missing streams detected. Writing has stopped.')
            for stream in streams:
                self._matches(stream, known[stream['id']], 'stream')
        op.update(status='running', step=step, pending=True)
        self.store.save_creation(op)
        write()
