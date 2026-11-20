import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';

type Entity = { id: number; name: string };
type Settings = { domain: Entity; group: Entity; traffic_source: Entity; name_prefix: string };
type Preview = {
  name: string; geos: string[]; settings: Settings;
  default: { offers: { offer_id: number; name: string; share: number; state: string }[] };
};

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { ...options, headers: { Accept: 'application/json', ...options.headers } });
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Не удалось проверить данные. Повторите запрос.');
  return body;
}

function Configuration({ settings }: { settings: Settings }) {
  return <dl className="creator-settings">
    <div><dt>Домен</dt><dd>{settings.domain.name} <span className="entity-id">ID {settings.domain.id}</span></dd></div>
    <div><dt>Группа кампаний</dt><dd>{settings.group.name} <span className="entity-id">ID {settings.group.id}</span></dd></div>
    <div><dt>Источник трафика</dt><dd>{settings.traffic_source.name} <span className="entity-id">ID {settings.traffic_source.id}</span></dd></div>
  </dl>;
}

export function Creator() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [settingsError, setSettingsError] = useState('');
  const [retry, setRetry] = useState(0);
  const [name, setName] = useState('');
  const [finalName, setFinalName] = useState('');
  const [nameError, setNameError] = useState('');
  const [geo, setGeo] = useState('');
  const [offerQuery, setOfferQuery] = useState('');
  const [search, setSearch] = useState<{ q: string; attempt: number } | null>(null);
  const [offers, setOffers] = useState<Entity[] | null>(null);
  const [offer, setOffer] = useState<Entity | null>(null);
  const [offerLoading, setOfferLoading] = useState(false);
  const [offerError, setOfferError] = useState('');
  const [preview, setPreview] = useState<Preview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState('');
  const previewRequest = useRef<AbortController | null>(null);
  const heading = useRef<HTMLHeadingElement | null>(null);

  useEffect(() => { heading.current?.focus(); }, []);

  useEffect(() => {
    const controller = new AbortController();
    setSettings(null);
    setSettingsError('');
    request<Settings>('/api/creator/settings', { signal: controller.signal })
      .then(setSettings)
      .catch((error: Error) => { if (!controller.signal.aborted) setSettingsError(error.message); });
    return () => controller.abort();
  }, [retry]);

  useEffect(() => {
    const controller = new AbortController();
    setFinalName('');
    setNameError('');
    if (!name.trim()) return () => controller.abort();
    const timer = window.setTimeout(() => {
      request<{ name: string }>(`/api/creator/name?name=${encodeURIComponent(name)}`, { signal: controller.signal })
        .then(data => setFinalName(data.name))
        .catch((error: Error) => { if (!controller.signal.aborted) setNameError(error.message); });
    }, 200);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [name]);

  useEffect(() => {
    if (!search) return;
    const controller = new AbortController();
    setOfferLoading(true);
    setOfferError('');
    setOffers(null);
    request<Entity[]>(`/api/offers?q=${encodeURIComponent(search.q)}`, { signal: controller.signal })
      .then(setOffers)
      .catch((error: Error) => { if (!controller.signal.aborted) setOfferError(error.message); })
      .finally(() => { if (!controller.signal.aborted) setOfferLoading(false); });
    return () => controller.abort();
  }, [search]);

  useEffect(() => () => previewRequest.current?.abort(), []);

  function invalidate() {
    previewRequest.current?.abort();
    setPreview(null);
    setPreviewError('');
    setPreviewLoading(false);
  }

  async function showPreview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    invalidate();
    const controller = new AbortController();
    previewRequest.current = controller;
    setPreviewLoading(true);
    try {
      const data = await request<Preview>('/api/creator/preview', {
        method: 'POST', signal: controller.signal,
        headers: { 'Content-Type': 'application/json', 'X-Adrobot-Request': '1' },
        body: JSON.stringify({ name, geos: geo.split(/[\s,;]+/).filter(Boolean), offer_id: offer?.id }),
      });
      if (!controller.signal.aborted) setPreview(data);
    } catch (error) {
      if (!controller.signal.aborted) setPreviewError(error instanceof Error ? error.message : 'Ошибка подключения. Повторите проверку.');
    } finally {
      if (!controller.signal.aborted) setPreviewLoading(false);
    }
  }

  return <section className="creator" aria-labelledby="creator-heading">
    <h2 id="creator-heading" tabIndex={-1} ref={heading}>Новая кампания</h2>
    <p className="scope-note">Creator проверяет настройки и показывает будущие маршруты. Создание кампании пока недоступно: запросы ничего не записывают в Keitaro.</p>
    <h3>Настройки с сервера</h3>
    {settings ? <Configuration settings={settings} /> : settingsError
      ? <div className="notice error" role="alert"><p>{settingsError}</p><button onClick={() => { invalidate(); setRetry(n => n + 1); }}>Проверить настройки снова</button></div>
      : <p className="loading" role="status">Проверка домена, группы и источника в Keitaro…</p>}
    <form onSubmit={showPreview} className="creator-form">
      <div><label htmlFor="creator-name">Название кампании</label>
        <input id="creator-name" required maxLength={255} value={name} onChange={e => { invalidate(); setName(e.target.value); }} aria-describedby="final-name" />
        <p id="final-name" className="field-hint" aria-live="polite">Итоговое имя: <strong>{nameError || finalName || (name.trim() ? 'Проверяется…' : 'введите название')}</strong></p>
        {settings && <p className="field-hint">Префикс: {settings.name_prefix || 'не задан'}. Сервер добавляет его один раз.</p>}
      </div>
      <div><label htmlFor="creator-geos">Страны GEO</label>
        <input id="creator-geos" required value={geo} onChange={e => { invalidate(); setGeo(e.target.value); }} placeholder="DE, FR" aria-describedby="geo-help" />
        <p id="geo-help" className="field-hint">Двухбуквенные коды ISO 3166-1 через запятую или пробел. Например: DE, FR. Повторы убираются сервером.</p>
      </div>
      <fieldset className="offer-picker"><legend>Оффер для остальных посетителей</legend>
        <label htmlFor="creator-offer-search">Название или точный ID оффера</label>
        <div className="search-controls"><input id="creator-offer-search" type="search" value={offerQuery} onChange={e => setOfferQuery(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); setSearch({ q: offerQuery, attempt: Date.now() }); } }} /><button type="button" disabled={offerLoading} onClick={() => setSearch({ q: offerQuery, attempt: Date.now() })}>Найти оффер</button></div>
        {offerLoading && <p role="status" className="loading">Поиск офферов…</p>}
        {offerError && <p role="alert" className="notice error">{offerError} Повторите поиск.</p>}
        {offers && <div role="status" className="field-hint">{offers.length ? `Найдено офферов: ${offers.length}` : 'Офферы не найдены. Измените название или ID.'}</div>}
        {offers && offers.length > 0 && <ul className="campaign-list creator-offers">{offers.map(item => <li key={item.id}><button type="button" className="campaign-button" aria-pressed={offer?.id === item.id} onClick={() => { invalidate(); setOffer(item); }}>{item.name}<span className="entity-id">ID {item.id}{offer?.id === item.id && ' · Выбран'}</span></button></li>)}</ul>}
        <p className="selected-offer" aria-live="polite">{offer ? `Выбран: ${offer.name} · ID ${offer.id}` : 'Оффер ещё не выбран'}</p>
      </fieldset>
      <button className="preview-button" type="submit" disabled={!settings || !offer || !finalName || !!nameError || previewLoading}>{previewLoading ? 'Проверка резюме…' : 'Проверить и показать резюме'}</button>
    </form>
    {previewError && <div role="alert" className="notice error"><strong>Резюме не подтверждено</strong><p>{previewError}</p><p>Исправьте данные или повторите проверку. В Keitaro ничего не записано.</p></div>}
    {preview && <section className="creator-summary" aria-labelledby="summary-heading" aria-live="polite">
      <h3 id="summary-heading">Проверенное резюме</h3>
      <p className="summary-name">{preview.name}</p>
      <Configuration settings={preview.settings} />
      <p>Начальное состояние: выключена (disabled). Ротация: по позиции (position). Создание, проверка потоков и включение ещё не реализованы.</p>
      <ol className="route-summary">
        <li><strong>Обычный поток (Regular)</strong><p>Страны {preview.geos.join(', ')} → <span>https://google.com</span></p><p className="field-hint">Один фильтр Country · HTTP-редирект</p></li>
        <li><strong>По умолчанию (Default)</strong><p>Остальные посетители → {preview.default.offers[0].name} · ID {preview.default.offers[0].offer_id} · 100%</p><p className="field-hint">Активная привязка оффера · Распределение офферов</p></li>
      </ol>
      <p className="summary-result" role="status">Проверка пройдена. Кампания не создана; запись в Keitaro недоступна.</p>
    </section>}
  </section>;
}
