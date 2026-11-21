import { useEffect, useId, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { Creator } from './Creator';

type Campaign = { id: number; name: string; keitaro_url: string };
type Binding = { offer_id: number; share: unknown; state: string; pinned?: boolean; name?: string };
type Stream = {
  id: number; name: string; type: string; schema: string; editable: boolean;
  offers: Binding[]; offer_names: Record<string, string>; read_only_reason: string | null;
  draft: { version: number; base: Binding[]; bindings: Binding[]; removed: Binding[]; notices: string[] } | null;
  unavailable_offers: Record<string, string>;
  conflict: boolean; validation_errors: string[];
};

async function read<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(path, { signal, headers: { Accept: 'application/json' } });
  if (!response.ok) throw new Error('Не удалось прочитать данные. Проверьте подключение и настройки Keitaro на сервере, затем повторите запрос.');
  return response.json();
}

function displayShare(value: unknown): string {
  if (value === null || value === undefined) return 'Не задана';
  return `${typeof value === 'object' ? JSON.stringify(value) : String(value)}%`;
}

type EditAction = (action: 'share' | 'pin' | 'unpin' | 'add' | 'remove' | 'bring_back', offerId: number, share?: number) => void;

function ShareInput({ binding, disabled, onEdit }: { binding: Binding; disabled: boolean; onEdit: EditAction }) {
  const id = useId();
  const [value, setValue] = useState(String(binding.share ?? ''));
  useEffect(() => setValue(String(binding.share ?? '')), [binding.share]);
  return <form className="share-form" onSubmit={event => { event.preventDefault(); onEdit('share', binding.offer_id, Number(value)); }}>
    <label className="sr-only" htmlFor={id}>Доля оффера {binding.offer_id}, %</label>
    <input id={id} aria-label={`Доля оффера ${binding.offer_id}, %`} type="number" min="0" max="100" step="1" required value={value} disabled={disabled} onChange={event => setValue(event.target.value)} />
    <button disabled={disabled} type="submit" aria-label={`Применить долю оффера ${binding.offer_id}`}>Применить</button>
  </form>;
}

function Bindings({ bindings, names, label, showPins, onEdit, busy = false }: {
  bindings: Binding[]; names: Record<string, string>; label: string; showPins: boolean; onEdit?: EditAction; busy?: boolean;
}) {
  if (!bindings.length) return <p className="empty-inline">В этом потоке нет привязок офферов.</p>;
  return <div className="table-scroll" tabIndex={0} role="region" aria-label={label}>
    <table>
      <caption className="sr-only">{label}</caption>
      <thead><tr><th scope="col">Оффер</th><th scope="col">Доля</th><th scope="col">Статус</th>{showPins && <th scope="col">Закрепление (Pin)</th>}{onEdit && <th scope="col">Действие</th>}</tr></thead>
      <tbody>{bindings.map((binding, index) => <tr key={`${binding.offer_id}-${index}`}>
        <th scope="row"><span className="offer-name">{binding.name ?? names[String(binding.offer_id)] ?? 'Оффер недоступен в каталоге'}</span><span className="entity-id">ID {binding.offer_id}</span></th>
        <td className="share">{onEdit && binding.state === 'active' ? <ShareInput binding={binding} disabled={busy} onEdit={onEdit} /> : displayShare(binding.share)}</td>
        <td><span className={`binding-state ${binding.state === 'active' ? 'active' : ''}`}>
          {binding.state === 'active' ? 'Активна' : binding.state === 'disabled' ? 'Отключена (disabled)' : `Неизвестный статус: ${binding.state}`}
        </span></td>
        {showPins && <td><span>{binding.pinned ? 'Закреплена' : 'Не закреплена'}</span>{onEdit && binding.state === 'active' && <button className="pin-button" disabled={busy} aria-pressed={Boolean(binding.pinned)} onClick={() => onEdit(binding.pinned ? 'unpin' : 'pin', binding.offer_id)}>{binding.pinned ? 'Открепить (Unpin)' : 'Закрепить (Pin)'}</button>}</td>}
        {onEdit && <td><button disabled={busy} onClick={() => onEdit('remove', binding.offer_id)} aria-label={`Удалить оффер ${binding.offer_id}`}>Удалить (Remove)</button></td>}
      </tr>)}</tbody>
    </table>
  </div>;
}

function OfferSearch({ draft, disabled, onEdit }: { draft: NonNullable<Stream['draft']>; disabled: boolean; onEdit: EditAction }) {
  const id = useId();
  const [input, setInput] = useState('');
  const [query, setQuery] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [results, setResults] = useState<{ id: number; name: string }[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    if (query === null) return;
    const controller = new AbortController();
    setLoading(true); setError('');
    read<{ id: number; name: string }[]>(`/api/offers?q=${encodeURIComponent(query)}`, controller.signal)
      .then(setResults)
      .catch((failure: Error) => { if (!controller.signal.aborted) setError(failure.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [query, attempt]);
  return <div className="offer-search">
    <form onSubmit={event => { event.preventDefault(); setQuery(input.trim()); setAttempt(n => n + 1); }}>
      <label htmlFor={id}>Добавить существующий оффер — название или точный ID</label>
      <div className="search-controls"><input id={id} type="search" value={input} onChange={event => setInput(event.target.value)} /><button disabled={disabled || loading}>Найти оффер</button></div>
    </form>
    {loading && <p role="status">Поиск офферов…</p>}
    {error && <p role="alert">{error} Повторите поиск.</p>}
    {query !== null && !loading && !error && <><p role="status">{results.length ? `Найдено офферов: ${results.length}` : 'Офферы не найдены. Измените название или ID.'}</p>
      <ul className="offer-results">{results.map(offer => {
        const exists = draft.bindings.some(b => b.offer_id === offer.id);
        const removed = draft.removed.some(b => b.offer_id === offer.id);
        return <li key={offer.id}><span>{offer.name} <span className="entity-id">ID {offer.id}</span></span><button disabled={disabled || exists} onClick={() => onEdit(removed ? 'bring_back' : 'add', offer.id)}>{exists ? 'Уже в потоке (включая disabled)' : removed ? 'Вернуть (Bring Back)' : 'Добавить (Add)'}</button></li>;
      })}</ul></>}
  </div>;
}

function StreamPanel({ stream, campaignId, onRefresh, disabled }: { stream: Stream; campaignId: number; onRefresh: () => void; disabled: boolean }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const inFlight = useRef(false);
  async function edit(action: 'share' | 'pin' | 'unpin' | 'add' | 'remove' | 'bring_back', offerId: number, share?: number) {
    if (!stream.draft || !stream.editable || disabled || inFlight.current) return;
    inFlight.current = true;
    setBusy(true); setError(''); setMessage('');
    try {
      const response = await fetch(`/api/campaigns/${campaignId}/streams/${stream.id}/actions`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Adrobot-Request': '1' },
        body: JSON.stringify({ version: stream.draft.version, action, offer_id: offerId, ...(action === 'share' ? { share } : {}) }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Недопустимое действие. Перечитайте потоки и проверьте введённые данные.');
      setMessage('Черновик сохранён. Keitaro не изменён.');
      onRefresh();
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Не удалось сохранить. Обновите потоки перед повтором.'); }
    finally { setBusy(false); inFlight.current = false; }
  }
  async function cancelDraft() {
    if (!stream.draft || !stream.editable || disabled || inFlight.current) return;
    if (!window.confirm('Отбросить неопубликованные изменения этого потока и загрузить актуальное состояние Keitaro? Изменения долей, Remove/Bring Back и неподтверждённые Pin будут отменены. Keitaro не изменится.')) return;
    inFlight.current = true;
    setBusy(true); setError(''); setMessage('');
    try {
      const response = await fetch(`/api/campaigns/${campaignId}/streams/${stream.id}/cancel`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Adrobot-Request': '1' },
        body: JSON.stringify({ version: stream.draft.version, confirmed: true }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Не удалось отменить изменения. Перечитайте потоки.');
      setMessage('Неопубликованные изменения отброшены. Загружено актуальное состояние; Keitaro не изменён.');
      onRefresh();
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Не удалось прочитать Keitaro. Перечитайте потоки: результат отмены не подтверждён.'); }
    finally { setBusy(false); inFlight.current = false; }
  }
  const blocked = busy || disabled || !stream.editable;
  const type = stream.type === 'regular' ? 'Обычный (Regular)' : stream.type === 'default' ? 'По умолчанию (Default)' : stream.type;
  return <section className="stream" aria-labelledby={`stream-${stream.id}`}>
    <div className="stream-heading">
      <div><h3 id={`stream-${stream.id}`}>{stream.name || `Поток ${stream.id}`}</h3><p className="stream-meta">ID {stream.id} · {type} · Схема: {stream.schema}</p></div>
      <span className="mode-label">{stream.editable ? 'Распределение офферов' : 'Только просмотр'}</span>
    </div>
    {stream.draft ? <>
      <p className="draft-note">Черновик сохранён · Версия {stream.draft.version}</p>
      {!stream.editable && <p className="readonly-note" role="status">{stream.read_only_reason}. Локальные изменения сохранены; действия заблокированы.</p>}
      <div className="draft-note"><button disabled={blocked} onClick={cancelDraft}>{stream.conflict ? 'Отбросить черновик и загрузить актуальное' : 'Отменить изменения (Cancel)'}</button><p>Потребуется подтверждение. Сначала читаем Keitaro; при ошибке чтения черновик сохраняется.</p></div>
      {stream.draft.notices.length > 0 && <div className="notice warning" role="status"><ul>{stream.draft.notices.map(notice => <li key={notice}>{notice}</li>)}</ul></div>}
      {stream.conflict && <div className="notice warning" role="status"><strong>Keitaro изменился относительно базы.</strong><p>Черновик и его база сохранены без изменений. Публикация заблокирована до разрешения конфликта.</p></div>}
      {stream.validation_errors.length > 0 && <div className="notice warning"><strong>Доли требуют исправления перед публикацией</strong><ul>{stream.validation_errors.map(error => <li key={error}>{error}</li>)}</ul><p>Исходные значения показаны без округления и перераспределения.</p></div>}
      <p className="draft-note">Целые проценты 0–100. «Применить» закрепляет долю и распределяет остаток; Unpin пересчитывает. Pin сохраняет текущую долю.</p>
      {busy && <p className="draft-note" role="status">Сохранение черновика…</p>}
      {message && <p className="draft-note" role="status">{message}</p>}
      {error && <div className="notice error" role="alert">{error} <button onClick={onRefresh}>Перечитать потоки</button></div>}
      <Bindings bindings={stream.draft.bindings} names={stream.offer_names} label={`Черновик потока ${stream.id}`} showPins onEdit={stream.editable ? edit : undefined} busy={blocked} />
      {stream.editable && <OfferSearch draft={stream.draft} disabled={blocked} onEdit={edit} />}
      <details className="comparison removed-history">
        <summary>Удалённые (Removed) · {stream.draft.removed.length}</summary>
        <p className="history-note">Прежние доля, статус и Pin сохранены. Раскрытие и скрытие не изменяют черновик.</p>
        {stream.draft.removed.length ? <ul className="offer-results">{stream.draft.removed.map(binding => <li key={binding.offer_id}>
          <div><strong>{stream.offer_names[String(binding.offer_id)]}</strong><span className="entity-id">ID {binding.offer_id}</span><p>{displayShare(binding.share)} · {binding.state === 'disabled' ? 'Отключена (disabled)' : 'Активна'} · {binding.pinned ? 'Закреплена (Pin)' : 'Не закреплена'}</p>
            {stream.unavailable_offers[String(binding.offer_id)] && <p>{stream.unavailable_offers[String(binding.offer_id)]}</p>}</div>
          <button disabled={blocked || Boolean(stream.unavailable_offers[String(binding.offer_id)])} onClick={() => edit('bring_back', binding.offer_id)} aria-label={`Вернуть оффер ${binding.offer_id}`}>Вернуть (Bring Back)</button>
        </li>)}</ul> : <p className="history-note">Удалённых привязок пока нет.</p>}
      </details>
      {stream.conflict && <details className="comparison"><summary>Сравнить базу и текущее состояние Keitaro</summary><h4>База черновика</h4><Bindings bindings={stream.draft.base} names={stream.offer_names} label={`База потока ${stream.id}`} showPins={false} /><h4>Сейчас в Keitaro</h4><Bindings bindings={stream.offers} names={stream.offer_names} label={`Текущее состояние потока ${stream.id}`} showPins={false} /></details>}
    </> : <>
      <p className="readonly-note">{stream.read_only_reason}. Черновик не создаётся; маршрутизация не меняется.</p>
      <Bindings bindings={stream.offers} names={stream.offer_names} label={`Привязки потока ${stream.id}`} showPins={false} />
    </>}
  </section>;
}

export function App() {
  const [creatorOpen, setCreatorOpen] = useState(false);
  const [input, setInput] = useState('');
  const [query, setQuery] = useState('');
  const [searchAttempt, setSearchAttempt] = useState(0);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [campaignLoading, setCampaignLoading] = useState(true);
  const [campaignError, setCampaignError] = useState('');
  const [selected, setSelected] = useState<Campaign | null>(null);
  const [streams, setStreams] = useState<Stream[] | null>(null);
  const [streamLoading, setStreamLoading] = useState(false);
  const [streamError, setStreamError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [syncedAt, setSyncedAt] = useState('');
  const restored = useRef(false);
  const previousCampaign = useRef<number | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setCampaignLoading(true);
    setCampaignError('');
    read<Campaign[]>(`/api/campaigns?q=${encodeURIComponent(query)}`, controller.signal)
      .then(data => {
        setCampaigns(data);
        if (!restored.current) {
          const id = new URLSearchParams(window.location.search).get('campaign');
          setSelected(data.find(c => String(c.id) === id) ?? null);
          restored.current = true;
        }
      })
      .catch((error: Error) => { if (!controller.signal.aborted) setCampaignError(error.message); })
      .finally(() => { if (!controller.signal.aborted) setCampaignLoading(false); });
    return () => controller.abort();
  }, [query, searchAttempt]);

  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    if (previousCampaign.current !== selected.id) {
      setStreams(null);
      setSyncedAt('');
      previousCampaign.current = selected.id;
    }
    setStreamLoading(true);
    setStreamError('');
    read<Stream[]>(`/api/campaigns/${selected.id}/streams`, controller.signal)
      .then(data => {
        setStreams(data);
        setSyncedAt(new Date().toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' }));
      })
      .catch((error: Error) => { if (!controller.signal.aborted) setStreamError(error.message); })
      .finally(() => { if (!controller.signal.aborted) setStreamLoading(false); });
    return () => controller.abort();
  }, [selected?.id, refresh]);

  function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setQuery(input.trim());
    setSearchAttempt(n => n + 1);
  }

  function choose(campaign: Campaign) {
    setCreatorOpen(false);
    const url = new URL(window.location.href);
    url.searchParams.set('campaign', String(campaign.id));
    window.history.replaceState(null, '', url);
    setSelected(campaign);
  }

  return <>
    <a className="skip-link" href="#workspace">Перейти к потокам</a>
    <header className="app-header"><span className="wordmark">Adrobot</span><span>Кампании Keitaro</span><span className="read-mode">Редактор черновиков</span></header>
    <div className="app-layout">
      <aside className="campaigns" aria-labelledby="campaigns-heading">
        <h1 id="campaigns-heading">Кампании</h1>
        <button className="creator-open" aria-pressed={creatorOpen} onClick={() => setCreatorOpen(open => !open)}>{creatorOpen ? 'Вернуться к потокам' : 'Новая кампания (Creator)'}</button>
        <form onSubmit={search} role="search">
          <label htmlFor="campaign-search">Название или точный ID</label>
          <div className="search-controls"><input id="campaign-search" type="search" value={input} onChange={event => setInput(event.target.value)} placeholder="Найти кампанию" /><button type="submit" disabled={campaignLoading}>Найти</button></div>
        </form>
        <div className="campaign-results" aria-busy={campaignLoading}>
          {campaignLoading && <p role="status" className="loading">Загрузка кампаний…</p>}
          {campaignError ? <div className="notice error" role="alert"><p>{campaignError}</p><button onClick={() => setSearchAttempt(n => n + 1)}>Повторить поиск</button></div>
            : !campaignLoading && <><p className="result-count" role="status">{campaigns.length ? `Найдено кампаний: ${campaigns.length}` : 'Кампании не найдены. Попробуйте другое название или ID.'}</p><ul className="campaign-list">{campaigns.map(c => <li key={c.id}><button className="campaign-button" aria-pressed={selected?.id === c.id} onClick={() => choose(c)}><span>{c.name || 'Без названия'}</span><span className="entity-id">ID {c.id}</span></button></li>)}</ul></>}
        </div>
      </aside>
      <main id="workspace" className="workspace" tabIndex={-1}>
        {creatorOpen ? <Creator /> : !selected ? <div className="start-state"><h2>Выберите кампанию</h2><p>Найдите её по названию или точному ID, чтобы открыть потоки и привязки офферов.</p><p>При первом открытии поддерживаемого потока исходные доли сохраняются в локальный черновик. Повторное чтение его не заменяет.</p></div> : <>
          <div className="workspace-heading"><div><h2>{selected.name || 'Без названия'}</h2><p className="campaign-meta">Кампания ID {selected.id}{syncedAt && ` · Прочитано в ${syncedAt}`}</p></div><div className="workspace-actions"><a href={selected.keitaro_url} target="_blank" rel="noopener noreferrer">Открыть Keitaro <span className="sr-only">(в новой вкладке)</span></a><button onClick={() => setRefresh(n => n + 1)} disabled={streamLoading}>Обновить потоки</button></div></div>
          <p className="scope-note">Доли и закрепления сохраняются в локальном черновике. Этот экран не изменяет Keitaro; публикация пока недоступна.</p>
          <div aria-busy={streamLoading}>
            {streamLoading && <p role="status" className="loading">Загрузка потоков… Черновики и база сохраняются.</p>}
            {streamError && <div className="notice error" role="alert"><strong>Не удалось загрузить потоки</strong><p>{streamError}</p>{streams !== null && <p>Ниже показаны последние загруженные данные, они могут быть устаревшими.</p>}<button onClick={() => setRefresh(n => n + 1)}>Повторить загрузку</button></div>}
            {streams !== null && (streams.length ? streams.map(stream => <StreamPanel key={`${selected.id}-${stream.id}`} stream={stream} campaignId={selected.id} onRefresh={() => setRefresh(n => n + 1)} disabled={streamLoading || Boolean(streamError)} />) : <div className="empty-streams"><h3>В кампании пока нет потоков</h3><p>Проверьте конфигурацию в Keitaro или обновите список позже.</p></div>)}
          </div>
        </>}
      </main>
    </div>
  </>;
}
