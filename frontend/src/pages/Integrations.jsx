import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import PublishActions from '../components/PublishActions'

export default function Integrations({ onOpenVideo }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [report, setReport] = useState(null)
  const [videos, setVideos] = useState([])
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [tab, setTab] = useState('accounts')
  const [search, setSearch] = useState('')
  const [verifiedLinks, setVerifiedLinks] = useState({})
  const refreshing = useRef(false)
  const refresh = async () => {
    if (refreshing.current) return
    refreshing.current = true
    try {
      const [connections, list] = await Promise.all([api.integrations(), api.videos('?limit=150&include_variants=true')])
      setData(connections); setVideos((list.videos || []).filter(v => ['READY','AWAITING_APPROVAL'].includes(v.state))); setError('')
    } catch (e) { setError(e.message) } finally { refreshing.current = false }
  }
  useEffect(() => { refresh(); const timer = setInterval(refresh, 10000); return () => clearInterval(timer) }, [])
  const run = async (action) => {
    setBusy(true); setError(''); setMessage('')
    try { await action() } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  const connect = async (slug, platform='youtube') => {
    const popup = window.open('about:blank', '_blank')
    if (popup) popup.opener = null
    await run(async () => {
      try {
        const result = await (platform === 'meta' ? api.connectMeta(slug) : api.connectYoutube(slug))
        if (popup) popup.location.href = result.url
        else window.location.assign(result.url)
      } catch (e) { popup?.close(); throw e }
    })
  }
  return <>
    <div className="eyebrow">YOUR STORIES, OUT IN THE WORLD</div>
    <h1 className="page-title">Publishing studio</h1>
    <p className="page-sub">Connect your channels, give each story a final look, and share when it is ready.</p>
    <div className="card publishing-mode row between">
      <div><span className="eyebrow">CURRENT WORKFLOW</span><h3>{!data ? 'Loading publishing settings…' : data.review_before_upload ? 'Review before publishing' : 'Automatic publishing'}</h3>
      <span className="muted">Destinations: {(data?.platforms || ['youtube']).map(p=>p==='youtube'?'YouTube':'Instagram').join(' + ')} · After generation: {data?.enabled ? 'enabled' : 'disabled'}. Configure in Settings.</span></div>
      <button className="btn" disabled={busy} onClick={refresh}>Refresh connections</button>
    </div>
    {error && <div className="note err" role="alert">{error}</div>}
    {message && <div className="note info" role="status">{message}</div>}
    <div className="workspace-tabs" role="tablist" aria-label="Publishing sections">{[['accounts','Accounts & insights'],['queue','Review & publish'],['history','Publication history']].map(([value,label])=><button key={value} role="tab" aria-selected={tab===value} className={tab===value?'on':''} onClick={()=>setTab(value)}>{label}</button>)}</div>
    {!data && <p className="muted" role="status">Loading connections and publications…</p>}
    {tab==='accounts' && <div className="grid cols-3">
      {(data?.channels || []).map(c => <div className="card connection-card" key={c.channel}>
        <div className="row between"><h3>{c.channel}</h3><span className="pill">{Number(c.youtube)+Number(c.instagram)}/2 connected</span></div>
        <div className="platform-section"><div className="row between"><b>YouTube</b><span className={`pill ${c.youtube?'good':'warn'}`}>{c.youtube?'Connected':'Not connected'}</span></div>
        <p className="muted">{c.youtube_channel_name || 'Choose the YouTube account for this channel.'}</p>
        {c.youtube_channel_id && <p className="mono dim">{c.youtube_channel_id}</p>}
        {c.connection_error && <p className="note err">{c.connection_error}</p>}
        <div className="row"><button className="btn primary" disabled={busy || !c.youtube_oauth_configured} onClick={() => connect(c.channel)}>{c.youtube ? 'Reconnect YouTube' : 'Connect YouTube'}</button>
        <button className="btn" disabled={busy || !c.youtube} onClick={() => run(async () => setReport(await api.analytics(c.channel)))}>Analytics</button></div>
        </div><div className="platform-section"><div className="row between"><b>Instagram</b><span className={`pill ${c.instagram?'good':'warn'}`}>{c.instagram?'Connected':'Not connected'}</span></div>
        {c.instagram_account_name && <p><b>@{c.instagram_account_name}</b> · {c.instagram_account_id}</p>}
        {c.meta_error && <p className="note err">{c.meta_error}</p>}
        <button className="btn" disabled={busy || !c.meta_oauth_configured} onClick={()=>connect(c.channel,'meta')}>{c.instagram?'Reconnect Instagram':'Connect Instagram directly'}</button>
        <button className="btn" disabled={busy || !c.instagram} onClick={()=>run(async()=>setReport(await api.instagramAnalytics(c.channel)))}>Instagram insights</button>
        <p className="dim tiny">Direct Instagram Login · professional account · no Facebook Page required.</p>
        {!c.meta_oauth_configured && <p className="dim tiny">Set META_APP_ID and META_APP_SECRET on Render.</p>}
        </div>
      </div>)}
    </div>}
    {tab==='accounts' && report?.platform === 'instagram' && <div className="card analytics-report"><h3>Instagram insights · {report.channel}</h3>
      <p className="dim">{report.start} to {report.end} (end exclusive)</p>
      {Object.entries(report.summary || {}).map(([key,value])=><p key={key}><b>{key.replaceAll('_',' ')}:</b> {value ?? 'Unavailable'}</p>)}
      {!Object.keys(report.summary || {}).length && <p>No insights returned for this account/date range.</p>}
    </div>}
    {tab==='accounts' && report && report.platform !== 'instagram' && <div className="card analytics-report"><h3>YouTube daily analytics · last 28 days</h3>
      <div className="table-scroll"><table><thead><tr>{(report.columnHeaders || []).map(c => <th key={c.name}>{c.name}</th>)}</tr></thead>
        <tbody>{(report.rows || []).map((r,i) => <tr key={i}>{r.map((v,j) => <td key={j}>{v}</td>)}</tr>)}</tbody></table></div>
      {!report.rows?.length && <p>No metrics returned for this period.</p>}
    </div>}
    {tab==='queue' && <div className="card"><h3>Ready for your audience</h3><p className="muted">Review a finished video, then choose where to publish. Existing media is reused.</p>
      <input type="search" aria-label="Search finished videos" placeholder="Search by title or channel…" value={search} onChange={e=>setSearch(e.target.value)}/>
      {videos.filter(v=>`${v.title} ${v.channel}`.toLowerCase().includes(search.toLowerCase())).map(v => {
        const connection = data?.channels.find(c => c.channel === v.channel)
        return <div className="upload-row" key={v.id}>
          <div><b>{v.title || 'Untitled video'}</b><div className="dim tiny">{v.channel} · {v.state.replaceAll('_',' ').toLowerCase()}</div><button className="btn small ghost" onClick={()=>onOpenVideo(v.id)}>Review video & soundtrack →</button></div>
          <PublishActions video={v} connection={connection} publications={data?.publications} privacy={data?.youtube_privacy} onPublished={refresh}/>
        </div>
      })}
      {!!data && !videos.length && <p className="muted">Finished videos will appear here.</p>}
    </div>}
    {tab==='history' && <div className="card"><h3>Publication history</h3>{(data?.publications || []).map((p,i) => <div className="upload-row" key={i}>
      <div><b>{p.platform==='youtube'?'YouTube':'Instagram'} · {p.status}</b><p className="dim tiny">{videos.find(v=>v.id===p.video_id)?.title || p.video_id.slice(0,8)}{p.platform==='youtube'?` · requested ${p.requested_privacy} · actual ${p.actual_privacy || 'not verified'}`:''}</p>
      {p.error && <p className="note warn">{p.error}</p>}</div>
      {p.remote_id && p.platform === 'youtube' && <div className="row"><a className="btn" href={`https://www.youtube.com/watch?v=${encodeURIComponent(p.remote_id)}`} target="_blank" rel="noreferrer">Open YouTube ↗</a>
      <button className="btn" disabled={busy} onClick={() => run(async () => { const r = await api.verifyYoutube(p.video_id); await refresh(); setMessage(`YouTube visibility: ${r.privacyStatus}; processing: ${r.processing || 'unknown'}`) })}>Check YouTube</button></div>}
      {p.remote_id && p.platform === 'instagram' && <div className="row">{verifiedLinks[p.video_id]?<a className="btn" href={verifiedLinks[p.video_id]} target="_blank" rel="noreferrer">Open Instagram ↗</a>:<button className="btn" disabled={busy} onClick={()=>run(async()=>{const r=await api.verifyInstagram(p.video_id);setVerifiedLinks(links=>({...links,[p.video_id]:r.url}));setMessage('Instagram Reel verified. Use Open Instagram to view it.')})}>Check Instagram</button>}</div>}
    </div>)}{!!data && !data.publications?.length && <p className="muted">No uploads yet. Your publishing results will appear here.</p>}</div>}
  </>
}
