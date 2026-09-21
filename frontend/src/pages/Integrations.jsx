import { useEffect, useState } from 'react'
import { api } from '../api'

export default function Integrations() {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [report, setReport] = useState(null)
  const [videos, setVideos] = useState([])
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const refresh = async () => {
    try {
      const [connections, list] = await Promise.all([api.integrations(), api.videos('?limit=150&include_variants=true')])
      setData(connections); setVideos((list.videos || []).filter(v => ['READY','AWAITING_APPROVAL'].includes(v.state)))
    } catch (e) { setError(e.message) }
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
      <div><span className="eyebrow">CURRENT WORKFLOW</span><h3>{!data ? 'Loading publishing settings…' : data.review_before_upload ? 'Review, approve, then upload' : 'Autonomous YouTube upload'}</h3>
      <span className="muted">Visibility: <b>{data?.youtube_privacy || '—'}</b> · Publishing after generation: {data?.enabled ? 'enabled' : 'disabled'}. Change these in Settings.</span></div>
      <button className="btn" disabled={busy} onClick={refresh}>Refresh connections</button>
    </div>
    {error && <div className="note err" role="alert">{error}</div>}
    {message && <div className="note info" role="status">{message}</div>}
    <div className="grid cols-3">
      {(data?.channels || []).map(c => <div className="card connection-card" key={c.channel}>
        <div className="row between"><h3>{c.channel}</h3><span className={`pill ${c.youtube ? 'good' : 'warn'}`}>{c.youtube ? 'Connected' : 'Needs connection'}</span></div>
        <p className="muted">{c.youtube_channel_name || 'Choose the YouTube account for this channel.'}</p>
        {c.youtube_channel_id && <p className="mono dim">{c.youtube_channel_id}</p>}
        {c.connection_error && <p className="note err">{c.connection_error}</p>}
        <div className="row"><button className="btn primary" disabled={busy || !c.youtube_oauth_configured} onClick={() => connect(c.channel)}>{c.youtube ? 'Reconnect YouTube' : 'Connect YouTube'}</button>
        <button className="btn" disabled={busy || !c.youtube} onClick={() => run(async () => setReport(await api.analytics(c.channel)))}>Analytics</button></div>
        <p className="dim tiny">Instagram: {c.instagram ? 'Configured · manual upload only' : 'Setup pending · no upload tests'}</p>
        {c.instagram_account_name && <p><b>@{c.instagram_account_name}</b> · {c.instagram_account_id}</p>}
        {c.meta_error && <p className="note err">{c.meta_error}</p>}
        <button className="btn" disabled={busy || !c.meta_oauth_configured} onClick={()=>connect(c.channel,'meta')}>{c.instagram?'Reconnect Instagram':'Connect Instagram'}</button>
        {!c.meta_oauth_configured && <p className="dim tiny">Set META_APP_ID and META_APP_SECRET on Render.</p>}
        {!!c.meta_accounts?.length && <div className="note info" style={{marginTop:12}}><b>Select the Instagram account for {c.channel}</b>
          {c.meta_accounts.map(a=><p key={a.id}><button className="btn" disabled={busy} onClick={()=>run(async()=>{
            if(!window.confirm(`Connect @${a.name} (${a.id}) to ${c.channel}?`))return
            await api.selectMeta(c.channel,a.id);await refresh();setMessage('Instagram connected. No content was published.')
          })}>@{a.name} · {a.page_name}</button></p>)}
        </div>}
      </div>)}
    </div>
    {report && <div className="card"><h3>YouTube daily analytics · last 28 days</h3>
      <div className="table-scroll"><table><thead><tr>{(report.columnHeaders || []).map(c => <th key={c.name}>{c.name}</th>)}</tr></thead>
        <tbody>{(report.rows || []).map((r,i) => <tr key={i}>{r.map((v,j) => <td key={j}>{v}</td>)}</tr>)}</tbody></table></div>
      {!report.rows?.length && <p>No metrics returned for this period.</p>}
    </div>}
    <div className="section-label">Finished stories</div>
    <div className="card"><h3>Your upload queue</h3><p className="muted tiny">Uses the existing finished video. No scripts, images or audio are regenerated.</p>
      {videos.map(v => {
        const connection = data?.channels.find(c => c.channel === v.channel)
        const publication = data?.publications.find(p => p.video_id === v.id && p.platform === 'youtube')
        const instagramPublication = data?.publications.find(p => p.video_id === v.id && p.platform === 'instagram')
        return <div className="upload-row" key={v.id}>
          <div><b>{v.title || 'Untitled video'}</b><div className="dim tiny">{v.channel} · {v.state.replaceAll('_',' ').toLowerCase()}{publication ? ` · YouTube ${publication.status}` : ''}</div></div>
          <div className="row">
            {!instagramPublication && <button className="btn" disabled={busy || !connection?.instagram} onClick={()=>run(async()=>{
              if(!window.confirm(`Publish this Reel to Instagram @${connection.instagram_account_name || connection.instagram_account_id || v.channel}?`))return
              await api.approveInstagram(v.id);await refresh();setMessage('Instagram Reel upload queued.')
            })}>Publish to Instagram</button>}
            {instagramPublication && <span className="pill">Instagram: {instagramPublication.status}</span>}
            {!publication && <button className="btn" disabled={busy || !connection?.youtube} onClick={() => run(async () => {
              if (!data?.review_before_upload && !window.confirm(`Upload this existing video to ${connection.youtube_channel_name || v.channel} as ${data.youtube_privacy}?`)) return
              const result = await api.uploadFlow(v.id); await refresh(); setMessage(result.status === 'awaiting_approval' ? 'Waiting for your review. Select Approve & upload when ready.' : `YouTube: ${result.status}`)
            })}>{data?.review_before_upload ? 'Send to review' : 'Upload automatically'}</button>}
            {!publication && data?.review_before_upload && <button className="btn primary" disabled={busy || !connection?.youtube} onClick={() => run(async () => {
              if (!window.confirm(`Approve this video and upload to ${connection.youtube_channel_name || v.channel} as ${data.youtube_privacy}?`)) return
              await api.approve(v.id); await refresh(); setMessage('Approved — YouTube upload queued.')
            })}>Approve & upload</button>}
          </div>
        </div>
      })}
      {!videos.length && <p className="muted">Finished videos will appear here.</p>}
    </div>
    <div className="section-label">Publication history</div>
    <div className="card">{(data?.publications || []).map((p,i) => <div className="upload-row" key={i}>
      <div><b>{p.platform} · {p.status}</b><p className="dim tiny">{p.video_id.slice(0,8)} · requested {p.requested_privacy} · actual {p.actual_privacy || 'not verified'}</p>
      {p.error && <p className="note warn">{p.error}</p>}</div>
      {p.remote_id && p.platform === 'youtube' && <div className="row"><a className="btn" href={`https://www.youtube.com/watch?v=${encodeURIComponent(p.remote_id)}`} target="_blank" rel="noreferrer">Open YouTube ↗</a>
      <button className="btn" disabled={busy} onClick={() => run(async () => { const r = await api.verifyYoutube(p.video_id); await refresh(); setMessage(`YouTube visibility: ${r.privacyStatus}; processing: ${r.processing || 'unknown'}`) })}>Check YouTube</button></div>}
    </div>)}{!data?.publications?.length && <p className="muted">No uploads yet. Your published links and verified visibility will appear here.</p>}</div>
  </>
}
