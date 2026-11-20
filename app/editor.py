"""Read-side Editor use case; it never writes to Keitaro."""


def same_bindings(left, right):
    return sorted(left, key=lambda b: b['offer_id']) == sorted(right, key=lambda b: b['offer_id'])


def validation_errors(bindings):
    if any(b['state'] not in ('active', 'disabled') for b in bindings):
        return ['Неизвестный статус привязки']
    if len({b['offer_id'] for b in bindings}) != len(bindings):
        return ['ID оффера не должен повторяться в потоке']
    active = [b for b in bindings if b['state'] == 'active']
    if not active:
        return ['Нужна хотя бы одна активная привязка']
    if any(type(b['share']) is not int or not 0 <= b['share'] <= 100 for b in active):
        return ['Активные доли должны быть целыми числами от 0 до 100']
    if sum(b['share'] for b in active) != 100:
        return ['Сумма активных долей должна быть 100%']
    return []


def open_streams(keitaro, store, campaign_id):
    streams = keitaro.streams(campaign_id)
    catalogue = {o['id']: o['name'] for o in keitaro.offers()} if any(s.get('offers') for s in streams) else {}
    result = []
    for stream in streams:
        offers = [{k: b[k] for k in ('offer_id', 'share', 'state')} for b in stream.get('offers', [])]
        editable = stream.get('type') in ('regular', 'default') and stream.get('schema') == 'landings'
        draft = store.open(campaign_id, stream['id'], offers) if editable else None
        result.append({
            'id': stream['id'], 'name': stream.get('name', ''),
            'type': stream.get('type', ''), 'schema': stream.get('schema', ''),
            'offers': [dict(b, name=catalogue.get(b['offer_id'], 'Оффер недоступен в каталоге')) for b in offers],
            'offer_names': {str(b['offer_id']): catalogue.get(b['offer_id'], 'Оффер недоступен в каталоге')
                            for b in (draft['bindings'] if draft else offers)},
            'editable': editable, 'draft': draft,
            'read_only_reason': None if editable else 'Этот тип или схема потока доступны только для просмотра',
            'conflict': draft is not None and not same_bindings(draft['base'], offers),
            'validation_errors': validation_errors(draft['bindings']) if draft else [],
        })
    return result
