import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { Creator } from './Creator';

type Campaign = { id: number; name: string; keitaro_url: string };
type Binding = { offer_id: number; share: unknown; state: string; pinned?: boolean; name?: string };
type Stream = {
  id: number; name: string; type: string; schema: string; editable: boolean;
  offers: Binding[]; offer_names: Record<string, string>; read_only_reason: string | null;
  draft: { version: number; base: Binding[]; bindings: Binding[] } | null;
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

function Bindings({ bindings, names, label, showPins }: {
  bindings: Binding[]; names: Record<string, string>; label: string; showPins: boolean;
}) {
  if (!bindings.length) return <p className="empty-inline">В этом потоке нет привязок офферов.</p>;
  return <div className="table-scroll" tabIndex={0} role="region" aria-label={label}>
    <table>
      <caption className="sr-only">{label}</caption>
      <thead><tr><th scope="col">Оффер</th><th scope="col">Доля</th><th scope="col">Статус</th>{showPins && <th scope="col">Закрепление (Pin)</th>}</tr></thead>
      <tbody>{bindings.map((binding, index) => <tr key={`${binding.offer_id}-${index}`}>
        <th scope="row"><span className="offer-name">{binding.name ?? names[String(binding.offer_id)] ?? 'Оффер недоступен в каталоге'}</span><span className="entity-id">ID {binding.offer_id}</span></th>
        <td className="share">{displayShare(binding.share)}</td>
        <td><span className={`binding-state ${binding.state === 'active' ? 'active' : ''}`}>
          {binding.state === 'active' ? 'Активна' : binding.state === 'disabled' ? 'Отключена (disabled)' : `Неизвестный статус: ${binding.state}`}
        </span></td>
        {showPins && <td>{binding.pinned ? 'Закреплена' : 'Не закреплена'}</td>}
      </tr>)}</tbody>
    </table>
  </div>;
}

function StreamPanel({ stream }: { stream: Stream }) {
  const type = stream.type === 'regular' ? 'Обычный (Regular)' : stream.type === 'default' ? 'По умолчанию (Default)' : stream.type;
  return <section className="stream" aria-labelledby={`stream-${stream.id}`}>
    <div className="stream-heading">
      <div><h3 id={`stream-${stream.id}`}>{stream.name || `Поток ${stream.id}`}</h3><p className="stream-meta">ID {stream.id} · {type} · Схема: {stream.schema}</p></div>
      <span className="mode-label">{stream.editable ? 'Распределение офферов' : 'Только просмотр'}</span>
    </div>
    {stream.draft ? <>
      <p className="draft-note">Исходный черновик сохранён · Версия {stream.draft.version}</p>
      {stream.conflict && <div className="notice warning" role="status"><strong>Keitaro изменился относительно базы.</strong><p>Черновик и его база сохранены без изменений. Публикация заблокирована до разрешения конфликта.</p></div>}
      {stream.validation_errors.length > 0 && <div className="notice warning"><strong>Доли требуют исправления перед публикацией</strong><ul>{stream.validation_errors.map(error => <li key={error}>{error}</li>)}</ul><p>Исходные значения показаны без округления и перераспределения.</p></div>}
      <Bindings bindings={stream.draft.bindings} names={stream.offer_names} label={`Черновик потока ${stream.id}`} showPins />
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
    <header className="app-header"><span className="wordmark">Adrobot</span><span>Кампании Keitaro</span><span className="read-mode">Просмотр и исходные черновики</span></header>
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
          <p className="scope-note">Этот экран не изменяет Keitaro. Редактирование и публикация пока недоступны.</p>
          <div aria-busy={streamLoading}>
            {streamLoading && <p role="status" className="loading">Загрузка потоков… Черновики и база сохраняются.</p>}
            {streamError && <div className="notice error" role="alert"><strong>Не удалось загрузить потоки</strong><p>{streamError}</p>{streams !== null && <p>Ниже показаны последние загруженные данные, они могут быть устаревшими.</p>}<button onClick={() => setRefresh(n => n + 1)}>Повторить загрузку</button></div>}
            {streams !== null && (streams.length ? streams.map(stream => <StreamPanel key={stream.id} stream={stream} />) : <div className="empty-streams"><h3>В кампании пока нет потоков</h3><p>Проверьте конфигурацию в Keitaro или обновите список позже.</p></div>)}
          </div>
        </>}
      </main>
    </div>
  </>;
}
