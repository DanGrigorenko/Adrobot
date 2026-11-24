import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';

type Entity = { id: number; name: string };
type Settings = { domain: Entity; group: Entity; traffic_source: Entity; name_prefix: string };
type Preview = {
  name: string; geos: string[]; settings: Settings; preview_token: string;
  default: { offers: { offer_id: number; name: string; share: number; state: string }[] };
};

type Operation = {
  id: string; alias: string; status: 'ready' | 'running' | 'unknown' | 'blocked' | 'succeeded';
  step: string; reason?: string; campaign_id: number | null;
  inputs: { name: string; geos: string[]; offer_id: number };
  editor_url?: string; keitaro_url?: string;
};
const statuses = { ready: 'Awaiting continuation', running: 'Review required', unknown: 'Creation result unknown', blocked: 'Creation stopped', succeeded: 'Campaign created' };
const mutationHeaders = { 'Content-Type': 'application/json', 'X-Adrobot-Request': '1' };

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { ...options, headers: { Accept: 'application/json', ...options.headers } });
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Could not verify the data. Try again.');
  return body;
}

function Configuration({ settings }: { settings: Settings }) {
  return <dl className="creator-settings">
    <div><dt>Domain</dt><dd>{settings.domain.name} <span className="entity-id">ID {settings.domain.id}</span></dd></div>
    <div><dt>Campaign group</dt><dd>{settings.group.name} <span className="entity-id">ID {settings.group.id}</span></dd></div>
    <div><dt>Traffic source</dt><dd>{settings.traffic_source.name} <span className="entity-id">ID {settings.traffic_source.id}</span></dd></div>
  </dl>;
}

export function Creator({ onCampaignsChanged }: { onCampaignsChanged: () => void }) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [settingsError, setSettingsError] = useState('');
  const [retry, setRetry] = useState(0);
  const [name, setName] = useState('');
  const [finalName, setFinalName] = useState('');
  const [nameError, setNameError] = useState('');
  const [geo, setGeo] = useState('');
  const [offerQuery, setOfferQuery] = useState('');
  const [offers, setOffers] = useState<Entity[] | null>(null);
  const [offer, setOffer] = useState<Entity | null>(null);
  const [offerLoading, setOfferLoading] = useState(false);
  const [offerError, setOfferError] = useState('');
  const [preview, setPreview] = useState<Preview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState('');
  const previewRequest = useRef<AbortController | null>(null);
  const heading = useRef<HTMLHeadingElement | null>(null);
  const [operations, setOperations] = useState<Operation[] | null>(null);
  const [currentOperation, setCurrentOperation] = useState<string | null>(null);
  const [operationError, setOperationError] = useState('');
  const [busyOperation, setBusyOperation] = useState<string | null>(null);
  const confirmationId = useRef<string | null>(null);
  const creating = useRef(false);

  async function loadOperations() {
    try {
      setOperations(await request<Operation[]>('/api/creator/operations'));
      setOperationError('');
    } catch { setOperationError('Could not verify the creation data. Try again.'); }
  }

  useEffect(() => { void loadOperations(); }, []);

  function remember(operation: Operation) {
    setOperations(current => [...(current ?? []).filter(item => item.id !== operation.id), operation]);
  }

  async function continueOperation(id: string) {
    if (creating.current) return;
    creating.current = true;
    setCurrentOperation(id); setBusyOperation(id); setOperationError('');
    try {
      const operation = await request<Operation>(`/api/creator/operations/${id}/continue`, { method: 'POST', headers: mutationHeaders });
      remember(operation);
      if (operation.status === 'succeeded' && confirmationId.current === id) {
        confirmationId.current = null;
        setName(''); setGeo(''); setOfferQuery(''); setOffer(null); setOffers(null);
        setPreview(null); setPreviewError(''); setOfferError('');
      }
      onCampaignsChanged();
    } catch (error) {
      setOperationError(error instanceof Error ? error.message : 'No response received. Recheck the saved operation; do not create a new one.');
    } finally { creating.current = false; setBusyOperation(null); }
  }

  async function confirmCreation() {
    if (!preview || creating.current) return;
    const id = confirmationId.current ?? crypto.randomUUID();
    confirmationId.current = id;
    creating.current = true;
    setCurrentOperation(id); setBusyOperation(id); setOperationError('');
    try {
      const operation = await request<Operation>('/api/creator/operations', {
        method: 'POST', headers: mutationHeaders,
        body: JSON.stringify({ operation_id: id, name: preview.name, geos: preview.geos, offer_id: preview.default.offers[0].offer_id, preview_token: preview.preview_token }),
      });
      remember(operation);
      setPreview(null);
      creating.current = false;
      await continueOperation(id);
    } catch {
      setOperationError('Confirmation was not received. Check the result before retrying to avoid creating a duplicate.');
    } finally { creating.current = false; setBusyOperation(null); }
  }

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
    const query = offerQuery.trim();
    if (!query) { setOfferLoading(false); return; }
    const controller = new AbortController();
    setOfferLoading(true);
    setOfferError('');
    const timer = window.setTimeout(() => {
      request<Entity[]>(`/api/offers?q=${encodeURIComponent(query)}`, { signal: controller.signal })
        .then(setOffers)
        .catch((error: Error) => { if (!controller.signal.aborted) setOfferError(error.message); })
        .finally(() => { if (!controller.signal.aborted) setOfferLoading(false); });
    }, 200);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [offerQuery]);

  useEffect(() => () => previewRequest.current?.abort(), []);

  function invalidate() {
    previewRequest.current?.abort();
    setPreview(null);
    confirmationId.current = null;
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
      if (!controller.signal.aborted) setPreviewError(error instanceof Error ? error.message : 'Connection error. Try again.');
    } finally {
      if (!controller.signal.aborted) setPreviewLoading(false);
    }
  }

  const current = operations?.find(operation => operation.id === currentOperation);
  const renderOperation = (operation: Operation) => <article className="creator-summary" key={operation.id}>
    <h4 className="summary-name">{operation.inputs.name}</h4>
    <p role="status"><strong>{statuses[operation.status]}</strong></p>
    {operation.reason && <p className="notice warning">{operation.reason}</p>}
    {operation.status === 'succeeded'
      ? <p className="workspace-actions"><a href={operation.editor_url}>Open editor</a><a href={operation.keitaro_url} target="_blank" rel="noreferrer">Open in Keitaro</a></p>
      : <button disabled={!!busyOperation} onClick={() => void continueOperation(operation.id)}>{busyOperation === operation.id ? 'Checking…' : 'Check and continue'}</button>}
  </article>;

  return <section className="creator" aria-labelledby="creator-heading">
    <h2 id="creator-heading" tabIndex={-1} ref={heading}>New campaign</h2>
    <p className="scope-note">Enter the details and review them before creating the campaign.</p>
    {operationError && <div className="notice error" role="alert"><p>{operationError}</p><button disabled={!!busyOperation} onClick={() => void loadOperations()}>Check saved operations</button></div>}
    {busyOperation && <p role="status">Creating campaign… Do not close this page.</p>}
    {current && renderOperation(current)}
    <h3>Campaign settings</h3>
    {settings ? <Configuration settings={settings} /> : settingsError
      ? <div className="notice error" role="alert"><p>{settingsError}</p><button onClick={() => { invalidate(); setRetry(n => n + 1); }}>Check settings again</button></div>
      : <p className="loading" role="status">Checking domain, group, and traffic source in Keitaro…</p>}
    <form onSubmit={showPreview} className="creator-form">
      <div><label htmlFor="creator-name">Campaign name</label>
        <input id="creator-name" required maxLength={255} value={name} onChange={e => { invalidate(); setName(e.target.value); }} aria-describedby="final-name" />
        <p id="final-name" className="field-hint" aria-live="polite">Final name: <strong>{nameError || finalName || (name.trim() ? 'Checking…' : 'enter a name')}</strong></p>
        {settings && <p className="field-hint">Prefix: {settings.name_prefix || 'not set'}. The server adds it once.</p>}
      </div>
      <div><label htmlFor="creator-geos">GEO countries</label>
        <input id="creator-geos" required value={geo} onChange={e => { invalidate(); setGeo(e.target.value); }} placeholder="DE, FR" aria-describedby="geo-help" />
        <p id="geo-help" className="field-hint">ISO codes separated by commas or spaces: DE, FR. Duplicates are removed.</p>
      </div>
      <fieldset className="offer-picker"><legend>Offer for all other visitors</legend>
        <label htmlFor="creator-offer-search">Offer name or beginning of ID</label>
        <div className="search-controls"><input id="creator-offer-search" type="search" value={offerQuery} onChange={e => { setOffers(null); setOfferError(''); setOfferQuery(e.target.value); }} onKeyDown={e => { if (e.key === 'Enter') e.preventDefault(); }} /></div>
        {offerLoading && <p role="status" className="loading">Searching offers…</p>}
        {offerError && <p role="alert" className="notice error">{offerError} Try searching again.</p>}
        {offers && <div role="status" className="field-hint">{offers.length ? `Offers found: ${offers.length}` : 'No offers found. Change the name or ID.'}</div>}
        {offers && offers.length > 0 && <ul className="campaign-list creator-offers">{offers.map(item => <li key={item.id}><button type="button" className="campaign-button" aria-pressed={offer?.id === item.id} onClick={() => { invalidate(); setOffer(item); }}>{item.name}<span className="entity-id">ID {item.id}{offer?.id === item.id && ' · Selected'}</span></button></li>)}</ul>}
        <p className="selected-offer" aria-live="polite">{offer ? `Selected: ${offer.name} · ID ${offer.id}` : 'No offer selected yet'}</p>
      </fieldset>
      <button className="preview-button" type="submit" disabled={!settings || !offer || !finalName || !!nameError || previewLoading}>{previewLoading ? 'Checking…' : 'Review summary'}</button>
    </form>
    {previewError && <div role="alert" className="notice error"><strong>Summary not confirmed</strong><p>{previewError}</p><p>Correct the details or try again. Nothing has been written to Keitaro.</p></div>}
    {preview && <section className="creator-summary" aria-labelledby="summary-heading" aria-live="polite">
      <h3 id="summary-heading">Review before creating</h3>
      <p className="summary-name">{preview.name}</p>
      <Configuration settings={preview.settings} />
      <p>Initial state: disabled. Rotation: by position. After checking both streams, the server enables the campaign and confirms it is active by reading it back.</p>
      <ol className="route-summary">
        <li><strong>Regular stream</strong><p>Countries {preview.geos.join(', ')} → <span>https://google.com</span></p><p className="field-hint">One Country filter · HTTP redirect</p></li>
        <li><strong>Default</strong><p>All other visitors → {preview.default.offers[0].name} · ID {preview.default.offers[0].offer_id} · 100%</p><p className="field-hint">Active offer binding · Offer distribution</p></li>
      </ol>
      <p className="summary-result" role="status">Summary verified. Confirming will create the campaign and two streams in Keitaro.</p>
      <button className="preview-button" disabled={!!busyOperation || !operations || !!operationError} onClick={() => void confirmCreation()}>Confirm creation and enable</button>
    </section>}
  </section>;
}
