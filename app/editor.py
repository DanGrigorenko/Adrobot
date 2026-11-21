"""Editor reads and local draft rules; it never writes to Keitaro."""


def edit_binding(bindings, offer_id, action, share=None):
    from copy import deepcopy

    result = deepcopy(bindings)
    matches = [b for b in result if b['offer_id'] == offer_id]
    if len(matches) != 1 or matches[0]['state'] != 'active':
        raise ValueError('Изменять можно только существующую активную привязку')
    target = matches[0]
    if action == 'share':
        if type(share) is not int or not 0 <= share <= 100:
            raise ValueError('Доля должна быть целым числом от 0 до 100')
        target.update(share=share, pinned=True)
    elif action == 'pin':
        if type(target['share']) is not int or not 0 <= target['share'] <= 100:
            raise ValueError('Нельзя закрепить некорректную долю: сначала исправьте её')
        target['pinned'] = True
        if sum(b['share'] for b in result if b['state'] == 'active' and b['pinned']) > 100:
            raise ValueError('Сумма закреплённых долей превышает 100%')
        if all(b['pinned'] for b in result if b['state'] == 'active') and validation_errors(result):
            raise ValueError('Сумма закреплённых долей должна быть 100%')
        return result
    elif action == 'unpin':
        target['pinned'] = False
    else:
        raise ValueError('Неизвестное действие')
    return redistribute(result)


def redistribute(result):
    active = [b for b in result if b['state'] == 'active']
    pinned = [b for b in active if b['pinned']]
    if any(type(b['share']) is not int or not 0 <= b['share'] <= 100 for b in pinned):
        raise ValueError('Закреплённые доли должны быть целыми числами от 0 до 100')
    remaining = 100 - sum(b['share'] for b in pinned)
    free = sorted((b for b in active if not b['pinned']), key=lambda b: b['offer_id'])
    if remaining < 0 or (not free and remaining != 0):
        raise ValueError('Невозможно распределить 100% без изменения закреплённых долей')
    if free:
        whole, extra = divmod(remaining, len(free))
        for index, binding in enumerate(free):
            binding['share'] = whole + (index < extra)
    errors = validation_errors(result)
    if errors:
        raise ValueError(errors[0])
    return result


def same_bindings(left, right):
    left = sorted(left, key=lambda b: b['offer_id'])
    right = sorted(right, key=lambda b: b['offer_id'])
    # Invalid source values remain distinct: JSON true is not an integer 1%.
    return left == right and all(type(a['share']) is type(b['share']) for a, b in zip(left, right))


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
    saved = store.existing(campaign_id)
    present = {s['id'] for s in streams}
    streams = streams + [dict(id=stream_id, deleted=True) for stream_id in saved if stream_id not in present]
    catalogue = {o['id']: o['name'] for o in keitaro.offers()} if streams else {}
    result = []
    for stream in streams:
        offers = [{k: b[k] for k in ('offer_id', 'share', 'state')} for b in stream.get('offers', [])]
        editable = stream.get('type') in ('regular', 'default') and stream.get('schema') == 'landings'
        draft = store.open(campaign_id, stream['id'], offers) if editable else saved.get(stream['id'])
        result.append({
            'id': stream['id'], 'name': stream.get('name', ''),
            'type': stream.get('type', ''), 'schema': stream.get('schema', ''),
            'offers': [dict(b, name=catalogue.get(b['offer_id'], 'Оффер недоступен в каталоге')) for b in offers],
            'offer_names': {str(b['offer_id']): catalogue.get(b['offer_id'], 'Оффер недоступен в каталоге')
                            for b in (draft['bindings'] + draft['removed'] if draft else offers)},
            'unavailable_offers': {str(b['offer_id']): 'Оффер недоступен в каталоге; возврат запрещён'
                                   for b in (draft['removed'] if draft else []) if b['offer_id'] not in catalogue},
            'editable': editable, 'draft': draft,
            'publication': store.publication(campaign_id, stream['id']) if draft else None,
            'read_only_reason': (None if editable else 'Поток удалён из Keitaro. Черновик сохранён; публикация запрещена'
                                 if stream.get('deleted') else 'Этот тип или схема потока доступны только для просмотра; публикация запрещена'),
            'conflict': draft is not None and not same_bindings(draft['base'], offers),
            'validation_errors': validation_errors(draft['bindings']) if draft else [],
        })
    return result
