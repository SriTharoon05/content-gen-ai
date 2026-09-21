import { useState } from 'react'
import { api } from '../api'

// A single upload control shared by the video editor and publishing queue.
export default function PublishActions({ video, connection, publications = [], privacy = 'private', disabled, onPublished }) {
  const [platform, setPlatform] = useState('youtube')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [queued, setQueued] = useState({})
  const publication = publications.find(p => p.video_id === video.id && p.platform === platform)
  const state = publication?.status || queued[platform]
  const connected = !!connection?.[platform]
  const retryable = state === 'blocked' || (platform === 'instagram' && state === 'failed')
  const locked = state && !retryable
  const name = platform === 'youtube' ? 'YouTube' : 'Instagram'
  const destination = platform === 'youtube' ? connection?.youtube_channel_name : connection?.instagram_account_name
  const upload = async () => {
    if (!window.confirm(`Approve and publish this finished video to ${name} ${destination || video.channel}?${platform === 'youtube' ? ` Visibility: ${privacy}.` : ' This is a public Reel.'}`)) return
    setBusy(true); setError('')
    try {
      const result = platform === 'youtube' ? await api.approve(video.id) : await api.approveInstagram(video.id)
      setQueued(q => ({...q, [platform]:result.publication?.status || result.status || 'pending'}))
      await onPublished?.()
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  return <div className="publish-control">
    <div className="row">
      <label className="publish-destination"><span className="tiny">Destination</span>
        <select aria-label={`Publishing destination for ${video.title || video.id}`} value={platform} disabled={busy} onChange={e => {setPlatform(e.target.value);setError('')}}>
          <option value="youtube">YouTube{connection?.youtube ? '' : ' · not connected'}</option>
          <option value="instagram">Instagram{connection?.instagram ? '' : ' · not connected'}</option>
        </select>
      </label>
      <button className="btn primary" disabled={busy || disabled || !connected || !!locked} onClick={upload}>
        {busy ? 'Queuing…' : locked ? `${name} · ${state.replaceAll('_',' ')}` : `${retryable ? 'Retry' : 'Approve & publish'} to ${name}`}
      </button>
    </div>
    <p className="dim tiny">{!connection ? 'Loading connection…' : !connected ? `Connect ${name} in Publishing → Accounts.` : `${destination || video.channel}${platform === 'youtube' ? ` · ${privacy}` : ' · public Reel'}`}</p>
    {disabled && <p className="note warn">Apply your pending edits before publishing.</p>}
    {state === 'uncertain' && <p className="note warn">Check the {name} account before retrying; the previous request may have published.</p>}
    {error && <p className="note err" role="alert">{error}</p>}
  </div>
}
