import { useEffect, useId, useRef, useState } from 'react';
import { Creator } from './Creator';

type Campaign = { id: number; name: string; keitaro_url: string };
type Binding = { offer_id: number; share: unknown; state: string; pinned?: boolean; name?: string };
type Publication = { id: string; status: 'unknown' | 'succeeded' | 'not_applied' | 'conflict'; locked: boolean; completed: boolean; base: Binding[]; sent: Binding[] };
type Stream = {
  id: number; name: string; type: string; schema: string; editable: boolean;
  offers: Binding[]; offer_names: Record<string, string>; read_only_reason: string | null;
  draft: { version: number; base: Binding[]; bindings: Binding[]; removed: Binding[]; notices: string[] } | null;
  unavailable_offers: Record<string, string>;
  conflict: boolean; validation_errors: string[]; publication: Publication | null;
};

async function read<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(path, { signal, headers: { Accept: 'application/json' } });
  if (!response.ok) throw new Error('Could not load data. Check the connection and server Keitaro settings, then try again.');
  return response.json();
}

function displayShare(value: unknown): string {
  if (value === null || value === undefined) return 'Not set';
  return `${JSON.stringify(value)}%`;
}

type EditAction = (action: 'share' | 'pin' | 'unpin' | 'add' | 'remove' | 'bring_back', offerId: number, share?: number) => void;

function Icon({ name }: { name: 'pin' | 'trash' | 'check' }) {
  return <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {name === 'pin' ? <><path d="m16 3 5 5-4 1-3 5v3l-7-7h3l5-3 1-4Z" /><path d="m10 14-6 6" /></> : name === 'trash' ? <><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v5M14 11v5" /></> : <path d="m5 12 4 4L19 6" />}
  </svg>;
}

function ShareInput({ binding, disabled, onEdit }: { binding: Binding; disabled: boolean; onEdit: EditAction }) {
  const id = useId();
  const [value, setValue] = useState(typeof binding.share === 'number' ? String(binding.share) : '');
  useEffect(() => setValue(typeof binding.share === 'number' ? String(binding.share) : ''), [binding.share]);
  const invalid = typeof binding.share !== 'number' || !Number.isInteger(binding.share) || binding.share < 0 || binding.share > 100;
  return <form className="share-form" onSubmit={event => { event.preventDefault(); onEdit('share', binding.offer_id, Number(value)); }}>
    <div className="share-controls">
      <label className="sr-only" htmlFor={id}>Offer share {binding.offer_id}, %</label>
      <div className="percent-input"><input id={id} aria-label={`Offer share ${binding.offer_id}, %`} aria-describedby={invalid ? `${id}-raw` : undefined} type="number" min="0" max="100" step="1" required value={value} disabled={disabled} onChange={event => setValue(event.target.value)} /><span aria-hidden="true">%</span></div>
      <button className="icon-button apply-button" disabled={disabled} type="submit" aria-label={`Apply share for offer ${binding.offer_id}`} title="Apply share"><Icon name="check" /></button>
    </div>
    {invalid && <span className="raw-share" id={`${id}-raw`}>Saved: {displayShare(binding.share)}</span>}
  </form>;
}

function Bindings({ bindings, names, label, showPins, onEdit, busy = false, base, removed = [], unavailable = {} }: {
  bindings: Binding[]; names: Record<string, string>; label: string; showPins: boolean; onEdit?: EditAction; busy?: boolean; base?: Binding[]; removed?: Binding[]; unavailable?: Record<string, string>;
}) {
  if (!bindings.length && !removed.length) return <p className="empty-inline">This stream has no offer bindings.</p>;
  return <div className="table-scroll" tabIndex={0} role="region" aria-label={label}>
    <table>
      <caption className="sr-only">{label}</caption>
      <thead><tr><th scope="col">Offer</th><th scope="col">Share, %</th>{(showPins || onEdit) && <th scope="col" className="actions-heading"><span className="sr-only">Actions</span></th>}</tr></thead>
      <tbody>{[...bindings.map(binding => ({ binding, removed: false })), ...removed.map(binding => ({ binding, removed: true }))].map(({ binding, removed: isRemoved }, index) => {
        const original = base?.find(item => item.offer_id === binding.offer_id);
        const changed = base && (!original || JSON.stringify(original.share) !== JSON.stringify(binding.share) || original.state !== binding.state);
        return <tr key={`${binding.offer_id}-${index}`} className={isRemoved ? "binding-removed" : changed ? "binding-changed" : undefined}>
        <th scope="row"><span className="offer-name">{binding.name ?? names[String(binding.offer_id)] ?? 'Offer unavailable in the catalog'}</span><span className="offer-meta"><span className="entity-id">ID {binding.offer_id}</span><span className={`binding-state ${binding.state === 'active' ? 'active' : ''}`}>
          {isRemoved ? 'Removed' : binding.state === 'active' ? 'Active' : binding.state === 'disabled' ? 'Disabled' : `Unknown status: ${binding.state}`}
        </span></span></th>
        <td className="share">{onEdit && (isRemoved || binding.state === 'active') ? <ShareInput binding={isRemoved ? { ...binding, share: 0 } : binding} disabled={busy || isRemoved} onEdit={onEdit} /> : displayShare(isRemoved ? 0 : binding.share)}</td>
        {(showPins || onEdit) && <td className="row-actions"><div>
          {!isRemoved && showPins && (onEdit && binding.state === 'active' ? <button className="icon-button pin-button" disabled={busy} aria-label={`${binding.pinned ? 'Unpin' : 'Pin'} offer ${binding.offer_id}`} title={binding.pinned ? 'Unpin share and recalculate' : 'Pin current share'} aria-pressed={Boolean(binding.pinned)} onClick={() => onEdit(binding.pinned ? 'unpin' : 'pin', binding.offer_id)}><Icon name="pin" /></button> : <span className="pin-status" title={binding.pinned ? 'Share pinned' : 'Share not pinned'}><span className="sr-only">{binding.pinned ? 'Pinned' : 'Not pinned'}</span>{binding.pinned && <Icon name="pin" />}</span>)}
          {isRemoved && onEdit && <button className="icon-button pin-button" disabled aria-label={`Pin offer ${binding.offer_id}`} title="Pin current share" aria-pressed={false}><Icon name="pin" /></button>}
          {isRemoved && onEdit && <button disabled={busy || Boolean(unavailable[String(binding.offer_id)])} title={unavailable[String(binding.offer_id)] || undefined} onClick={() => onEdit('bring_back', binding.offer_id)} aria-label={`Restore offer ${binding.offer_id}`}>Restore</button>}
          {!isRemoved && onEdit && <button className="icon-button delete-button" disabled={busy} onClick={() => onEdit('remove', binding.offer_id)} aria-label={`Remove offer ${binding.offer_id}`} title="Remove offer"><Icon name="trash" /></button>}
        </div></td>}
      </tr>})}</tbody>
    </table>
  </div>;
}

function OfferSearch({ draft, disabled, onEdit }: { draft: NonNullable<Stream['draft']>; disabled: boolean; onEdit: EditAction }) {
  const id = useId();
  const [input, setInput] = useState('');
  const [results, setResults] = useState<{ id: number; name: string }[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    const query = input.trim();
    if (!query) { setLoading(false); return; }
    const controller = new AbortController();
    setLoading(true); setError('');
    const timer = window.setTimeout(() => {
      read<{ id: number; name: string }[]>(`/api/offers?q=${encodeURIComponent(query)}`, controller.signal)
        .then(setResults)
        .catch((failure: Error) => { if (!controller.signal.aborted) setError(failure.message); })
        .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }, 200);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [input]);
  return <div className="offer-search">
    <label htmlFor={id}>Add offer</label>
    <div className="search-controls"><input id={id} type="search" placeholder="Name or beginning of ID" value={input} onChange={event => { setResults([]); setError(''); setInput(event.target.value); }} /></div>
    {input.trim() && loading && <p role="status">Searching offers…</p>}
    {input.trim() && error && <p role="alert">{error} Change your query to search again.</p>}
    {input.trim() && !loading && !error && <><p role="status">{results.length ? `Offers found: ${results.length}` : 'No offers found. Change the name or ID.'}</p>
      <ul className="offer-results">{results.map(offer => {
        const exists = draft.bindings.some(b => b.offer_id === offer.id);
        const removed = draft.removed.some(b => b.offer_id === offer.id);
        return <li key={offer.id}><span>{offer.name} <span className="entity-id">ID {offer.id}</span></span><button disabled={disabled || exists} onClick={() => onEdit(removed ? 'bring_back' : 'add', offer.id)}>{exists ? 'Already in stream (including disabled)' : removed ? 'Restore (Bring Back)' : 'Add'}</button></li>;
      })}</ul></>}
  </div>;
}

function StreamPanel({ stream: loaded, campaignId, onRefresh, disabled }: { stream: Stream; campaignId: number; onRefresh: () => void; disabled: boolean }) {
  const [stream, setStream] = useState(loaded);
  useEffect(() => setStream(previous => (loaded.draft?.version ?? 0) >= (previous.draft?.version ?? 0) ? loaded : previous), [loaded]);
  const [busy, setBusy] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [error, setError] = useState('');
  const inFlight = useRef(false);
  async function edit(action: 'share' | 'pin' | 'unpin' | 'add' | 'remove' | 'bring_back', offerId: number, share?: number) {
    if (!stream.draft || !stream.editable || stream.publication?.locked || disabled || inFlight.current) return;
    inFlight.current = true;
    setBusy(true); setError('');
    try {
      const response = await fetch(`/api/campaigns/${campaignId}/streams/${stream.id}/actions`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Adrobot-Request': '1' },
        body: JSON.stringify({ version: stream.draft.version, action, offer_id: offerId, ...(action === 'share' ? { share } : {}) }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Invalid action. Reload the streams and check your input.');
      onRefresh();
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Could not save. Refresh the streams before trying again.'); }
    finally { setBusy(false); inFlight.current = false; }
  }
  async function cancelDraft() {
    if (!stream.draft || !stream.editable || stream.publication?.locked || disabled || inFlight.current) return;
    setConfirmCancel(false);
    inFlight.current = true;
    setBusy(true); setError('');
    try {
      const response = await fetch(`/api/campaigns/${campaignId}/streams/${stream.id}/cancel`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Adrobot-Request': '1' },
        body: JSON.stringify({ version: stream.draft.version, confirmed: true }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not discard changes. Reload the streams.');
      onRefresh();
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Could not read Keitaro. Reload the streams; discarding changes was not confirmed.'); }
    finally { setBusy(false); inFlight.current = false; }
  }
  async function publication(check: boolean) {
    if (!stream.draft || inFlight.current) return;
    if (!check && (disabled || !stream.editable || stream.publication?.locked)) return;
    if (!check && !window.confirm('Publish only this stream’s offer bindings to Keitaro? Cancel does not undo publication. Full protection against concurrent external changes is not guaranteed.')) return;
    inFlight.current = true;
    setBusy(true); setError('');
    try {
      const response = await fetch(`/api/campaigns/${campaignId}/streams/${stream.id}/${check ? 'check-publication' : 'push'}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Adrobot-Request': '1' },
        body: JSON.stringify({ version: stream.draft.version }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Reload the streams before the next action.');
      setStream(previous => ({ ...previous, draft: data.draft, publication: data.publication }));
    } catch (failure) {
      setError(failure instanceof TypeError || !(failure instanceof Error) ? 'The application response was lost. The publication result is unknown. Recheck the saved operation; do not retry Push blindly.' : failure.message);
    } finally {
      setBusy(false); inFlight.current = false; onRefresh();
    }
  }
  const blocked = busy || disabled || !stream.editable || Boolean(stream.publication?.locked);
  const type = stream.type === 'regular' ? 'Regular' : stream.type === 'default' ? 'Default' : stream.type;
  if (!stream.offers.length && !stream.draft && !stream.publication && !stream.conflict) return <section className="stream" aria-label={stream.name || `Stream ${stream.id}`}>
    <div className="stream-heading"><div><h3>{stream.name || `Stream ${stream.id}`}</h3><p className="stream-meta">ID {stream.id} · {type}</p></div><span className="mode-label">No offers</span></div>
  </section>;
  return <section className="stream" aria-labelledby={`stream-${stream.id}`}>
    <div className="stream-heading">
      <div><h3 id={`stream-${stream.id}`}>{stream.name || `Stream ${stream.id}`}</h3><p className="stream-meta">ID {stream.id} · {type} · Schema: {stream.schema}</p></div>
      <span className="mode-label">{stream.editable ? 'Offer distribution' : 'Read only'}</span>
    </div>
    {stream.draft ? <>
      <div className="stream-toolbar"><span className="draft-status">Draft · v{stream.draft.version}</span><div className="stream-actions"><button disabled={blocked || confirmCancel} onClick={() => setConfirmCancel(true)}>Cancel</button><button className="primary-button" disabled={blocked || stream.conflict || stream.validation_errors.length > 0} onClick={() => publication(false)}>Publish stream</button></div></div>
      {confirmCancel && <div className="discard-confirmation" role="group" aria-label="Confirm discarding changes" onKeyDown={event => { if (event.key === 'Escape') setConfirmCancel(false); }}>
        <div><strong>Discard changes?</strong><p>Unpublished edits will be replaced with the current Keitaro state. Keitaro will not be changed.</p></div>
        <div className="stream-actions"><button onClick={() => setConfirmCancel(false)}>Keep editing</button><button className="discard-button" disabled={blocked} onClick={() => void cancelDraft()}>Discard changes</button></div>
      </div>}
      {stream.publication && stream.publication.status !== 'succeeded' && <div className="notice warning" role="status">
        <strong>{stream.publication.status === 'not_applied' ? 'Publication was not applied: the previous state was confirmed and the previous request has completed.' : stream.publication.status === 'conflict' ? 'Conflict: Keitaro matches neither the baseline nor the submitted state.' : 'Publication result is unknown.'}</strong>
        {stream.publication.locked ? <><p>Edits, Cancel, and a new Push are blocked. Checking only reads Keitaro; restarting does not repeat the write.</p><button disabled={busy} onClick={() => publication(true)}>Check result</button>{!stream.publication.completed && <p>The previous write may still be running. A timeout and a single read of the previous baseline do not prove completion; there is no automatic unlock.</p>}</> : stream.publication.status === 'not_applied' ? <p>The draft is saved. Only an explicit new Push with the usual checks is possible.</p> : stream.publication.status === 'conflict' ? <p>Discard the draft and load the current state. Force overwrite is not available.</p> : <p>Pins and removal history are confirmed. Cancel does not undo published changes.</p>}
      </div>}
      {!stream.editable && <p className="readonly-note" role="status">{stream.read_only_reason}. Local changes are saved; actions are blocked.</p>}
      {stream.draft.notices.length > 0 && <div className="notice warning" role="status"><ul>{stream.draft.notices.map(notice => <li key={notice}>{notice}</li>)}</ul></div>}
      {(stream.conflict || stream.publication?.status === 'conflict') && !stream.publication?.locked && <div className="notice warning" role="status"><strong>Keitaro has changed since the baseline.</strong><p>To discard the draft and load the current state, select Cancel.</p></div>}
      {stream.validation_errors.length > 0 && <div className="notice warning"><strong>Shares must be corrected before publishing</strong><ul>{stream.validation_errors.map(error => <li key={error}>{error}</li>)}</ul><p>Original values are shown without rounding or redistribution.</p></div>}
      {busy && <p className="draft-note" role="status">Action in progress… Wait for confirmation.</p>}
      {error && <div className="notice error" role="alert">{error} <button onClick={onRefresh}>Reload streams</button></div>}
      <Bindings bindings={stream.draft.bindings} names={stream.offer_names} label={`Draft for stream ${stream.id}`} showPins onEdit={stream.editable ? edit : undefined} busy={blocked} base={stream.draft.base} removed={stream.draft.removed} unavailable={stream.unavailable_offers} />
      {stream.editable && <OfferSearch draft={stream.draft} disabled={blocked} onEdit={edit} />}
      {stream.conflict && <details className="comparison"><summary>Compare baseline with current Keitaro state</summary><h4>Draft baseline</h4><Bindings bindings={stream.draft.base} names={stream.offer_names} label={`Baseline for stream ${stream.id}`} showPins={false} /><h4>Current state in Keitaro</h4><Bindings bindings={stream.offers} names={stream.offer_names} label={`Current state of stream ${stream.id}`} showPins={false} /></details>}
    </> : <>
      <p className="readonly-note">{stream.read_only_reason}. No draft is created; routing is unchanged.</p>
      <Bindings bindings={stream.offers} names={stream.offer_names} label={`Bindings for stream ${stream.id}`} showPins={false} />
    </>}
  </section>;
}

export function App() {
  const [creatorOpen, setCreatorOpen] = useState(false);
  const [input, setInput] = useState('');
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
    read<Campaign[]>('/api/campaigns', controller.signal)
      .then(data => {
        setCampaigns(data);
        if (!restored.current) {
          const id = new URLSearchParams(window.location.search).get('campaign');
          setSelected(data.find(c => String(c.id) === id) ?? null);
          restored.current = true;
        }
      })
      .catch((error: Error) => {
        if (controller.signal.aborted) return;
        setCampaignError(error.message);
        if (!restored.current) {
          const id = Number(new URLSearchParams(window.location.search).get('campaign'));
          if (Number.isSafeInteger(id) && id > 0) setSelected({ id, name: `Campaign ${id} — saved data`, keitaro_url: '' });
          restored.current = true;
        }
      })
      .finally(() => { if (!controller.signal.aborted) setCampaignLoading(false); });
    return () => controller.abort();
  }, [searchAttempt]);

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
      .catch(async (error: Error) => {
        if (controller.signal.aborted) return;
        setStreamError(error.message);
        try {
          const saved = await read<Stream[]>(`/api/campaigns/${selected.id}/drafts`, controller.signal);
          if (!controller.signal.aborted && saved.length) setStreams(saved);
        } catch { /* Keep the last loaded state if local recovery is also unavailable. */ }
      })
      .finally(() => { if (!controller.signal.aborted) setStreamLoading(false); });
    return () => controller.abort();
  }, [selected?.id, refresh]);

  const query = input.trim().toLocaleLowerCase();
  const matchingCampaigns = campaigns.filter(campaign =>
    campaign.name.toLocaleLowerCase().includes(query) || String(campaign.id).startsWith(query));

  function choose(campaign: Campaign) {
    setCreatorOpen(false);
    const url = new URL(window.location.href);
    url.searchParams.set('campaign', String(campaign.id));
    window.history.replaceState(null, '', url);
    setSelected(campaign);
  }

  return <>
    <a className="skip-link" href="#workspace">Skip to streams</a>
    <header className="app-header"><span className="wordmark">Adrobot</span><span>Offer editor</span><span className="read-mode">Keitaro</span></header>
    <div className="app-layout">
      <aside className="campaigns" aria-labelledby="campaigns-heading">
        <div className="campaign-picker-heading"><h1 id="campaigns-heading">Campaigns</h1><button className="creator-open" aria-pressed={creatorOpen} onClick={() => { if (creatorOpen) setSearchAttempt(n => n + 1); setCreatorOpen(open => !open); }}>{creatorOpen ? 'Back to editor' : 'New campaign'}</button></div>
        <div role="search">
          <label className="sr-only" htmlFor="campaign-search">Campaign name or beginning of ID</label>
          <input id="campaign-search" type="search" value={input} onChange={event => setInput(event.target.value)} placeholder="Find a campaign" />
        </div>
        <div className="campaign-results" aria-busy={campaignLoading}>
          {campaignLoading && <p role="status" className="loading">Loading campaigns…</p>}
          {campaignError ? <div className="notice error" role="alert"><p>{campaignError}</p><button onClick={() => setSearchAttempt(n => n + 1)}>Retry search</button></div>
            : !campaignLoading && <><p className="result-count" role="status">{matchingCampaigns.length ? `Campaigns found: ${matchingCampaigns.length}` : 'No campaigns found. Try another name or ID.'}</p><ul className="campaign-list">{matchingCampaigns.map(c => <li key={c.id}><button className="campaign-button" aria-pressed={selected?.id === c.id} onClick={() => choose(c)}><span>{c.name || 'Untitled'}</span><span className="entity-id">ID {c.id}</span></button></li>)}</ul></>}
        </div>
      </aside>
      <main id="workspace" className="workspace" tabIndex={-1}>
        {creatorOpen ? <Creator onCampaignsChanged={() => setSearchAttempt(n => n + 1)} /> : !selected ? <div className="start-state"><h2>Select a campaign</h2><p>Find a campaign above to configure offer shares.</p></div> : <>
          <div className="workspace-heading"><div><h2>{selected.name || 'Untitled'}</h2><p className="campaign-meta">Campaign ID {selected.id}{syncedAt && ` · Read at ${syncedAt}`}</p></div><div className="workspace-actions">{selected.keitaro_url && <a href={selected.keitaro_url} target="_blank" rel="noopener noreferrer">Open Keitaro <span className="sr-only">(opens in a new tab)</span></a>}<button onClick={() => setRefresh(n => n + 1)} disabled={streamLoading}>Fetch</button></div></div>

          <div aria-busy={streamLoading}>
            {streamLoading && <p role="status" className="loading">Loading streams… Drafts and baseline are preserved.</p>}
            {streamError && <div className="notice error" role="alert"><strong>Could not load streams</strong><p>{streamError}</p>{streams !== null && <p>The last loaded data is shown below and may be out of date.</p>}<button onClick={() => setRefresh(n => n + 1)}>Retry loading</button></div>}
            {streams !== null && (streams.length ? streams.map(stream => <StreamPanel key={`${selected.id}-${stream.id}`} stream={stream} campaignId={selected.id} onRefresh={() => setRefresh(n => n + 1)} disabled={streamLoading || Boolean(streamError)} />) : <div className="empty-streams"><h3>This campaign has no streams</h3><p>Refresh the list to check for changes.</p></div>)}
          </div>
        </>}
      </main>
    </div>
  </>;
}
