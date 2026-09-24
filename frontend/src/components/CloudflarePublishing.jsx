import { useEffect, useState } from 'react'
import { cloudflareRequest, postCloudflare, publicationLocked, safeMediaUrl } from '../cloudflareApi'
import { Field, Select } from './ui'

export default function CloudflarePublishing({ video, disabled, onUpdated }) {
  const [data, setData] = useState(null), [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const [platform, setPlatform] = useState('youtube'), [confirmed, setConfirmed] = useState(false)
  const [approved, setApproved] = useState(!!video.approved)
  const [queued, setQueued] = useState({})
  const load = async () => {
    try { setData(await cloudflareRequest('/connections')); setError('') }
    catch (e) { setError(e.message) }
  }
  useEffect(() => { load(); const timer = setInterval(load, 15000); return () => clearInterval(timer) }, [])
  useEffect(() => { setApproved(!!video.approved); setConfirmed(false) }, [video.id, video.output, video.approved])
  const connection = (data?.channels || data?.connections || []).find(c => (c.channel || c.channel_slug || c.slug) === video.channel)
  const connected = !!connection?.[platform]
  const publication = (data?.publications || []).find(p => p.video_id === video.id && p.platform === platform) || (video.publications || []).find(p => p.platform === platform)
  const status = publication?.status || queued[platform]
  const privacy = data?.youtube_privacy
  const destination = platform === 'youtube' ? connection?.youtube_channel_name : connection?.instagram_account_name
  const approve = async () => {
    setBusy(true); setError('')
    try { await postCloudflare(`/videos/${video.id}/approve`, {output:video.output}); setApproved(true); await onUpdated?.() }
    catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  const publish = async () => {
    setBusy(true); setError('')
    try {
      const result = await postCloudflare(`/videos/${video.id}/publish`, { platform })
      setQueued(q => ({ ...q, [platform]: result.publication?.status || result.status || 'pending' }))
      setConfirmed(false); await load(); await onUpdated?.()
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  return <section className="card cf-panel">
    <div className="row between"><h3>Review & publish</h3><span className={`pill ${approved ? 'good' : 'warn'}`}>{approved ? 'Approved' : 'Awaiting your review'}</span></div>
    <p className="muted">Approval saves your decision. Publishing sends this saved video to the selected account.</p>
    {disabled && <p className="note warn">Apply or discard your edits before approving or publishing. Rendering must finish first.</p>}
    <div className="row cf-actions">
      <button className="btn" disabled={busy || disabled || approved || !video.output} onClick={approve}>{approved ? 'Review approved' : 'Approve this version'}</button>
      <Field label="Publishing destination"><Select value={platform} onChange={v => { setPlatform(v); setConfirmed(false) }} options={['youtube','instagram'].map(v => ({ value: v, label: v === 'youtube' ? 'YouTube' : 'Instagram' }))} disabled={busy}/></Field>
      <button className="btn primary" disabled={busy || disabled || !approved || !connected || (platform==='youtube'&&!privacy) || publicationLocked(status)} onClick={() => setConfirmed(true)}>{busy ? 'Saving…' : publicationLocked(status) ? `${platform} · ${status}` : `Publish to ${platform === 'youtube' ? 'YouTube' : 'Instagram'}`}</button>
    </div>
    <p className="muted">{!data ? 'Connection information unavailable.' : connected ? `${destination || video.channel} · ${platform === 'youtube' ? privacy||'Visibility unavailable; refresh before publishing' : 'public Reel'}` : `Connect ${platform === 'youtube' ? 'YouTube' : 'Instagram'} in Channels & accounts first.`}</p>
    {status === 'uncertain' && <p className="note warn">The previous upload may have completed. Check the destination account; automatic duplicate uploads are blocked.</p>}
    {publication?.error && <p className="note err">{publication.error}</p>}
    {safeMediaUrl(publication?.url || publication?.permalink) && <a href={safeMediaUrl(publication.url || publication.permalink)} target="_blank" rel="noreferrer">Open published video</a>}
    {confirmed && <div className="note warn"><p>Send <strong>{video.title || video.id}</strong> to <strong>{destination || video.channel}</strong> on {platform}? {platform === 'youtube' ? `Visibility: ${privacy}.` : 'This is a public Reel.'}</p><div className="row"><button className="btn primary" disabled={busy || disabled || !approved} onClick={publish}>Confirm publication</button><button className="btn" disabled={busy} onClick={() => setConfirmed(false)}>Cancel</button></div></div>}
    {error && <p className="note err" role="alert">{error}</p>}
  </section>
}
