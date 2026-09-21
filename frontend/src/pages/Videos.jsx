import { useEffect, useState } from 'react'
import { api, moneyShort } from '../api'
import { useStore } from '../store.jsx'
import { Select, StatePill, Progress, Empty } from '../components/ui.jsx'

const STATES = ['', 'QUEUED', 'PREMISE', 'SCRIPT', 'QA', 'VOICE', 'NARRATION', 'ALIGNING', 'VISUALS',
  'IMAGES', 'EDITING', 'CAPTIONS', 'RENDERING', 'COPY', 'AWAITING_APPROVAL', 'READY', 'FAILED']

export default function Videos({ onOpenVideo }) {
  const { channels } = useStore()
  const [videos, setVideos] = useState(null)
  const [channel, setChannel] = useState('')
  const [state, setState] = useState('')

  const load = () => {
    const params = new URLSearchParams({ limit: '150', include_variants: 'true' })
    if (channel) params.set('channel', channel)
    if (state) params.set('state', state)
    api.videos(`?${params}`).then((r) => setVideos(r.videos)).catch(() => setVideos([]))
  }

  useEffect(() => { load() }, [channel, state])
  useEffect(() => {
    const interval = setInterval(load, 6000)
    return () => clearInterval(interval)
  }, [channel, state])

  return (
    <>
      <h1 className="page-title">Videos</h1>
      <p className="page-sub">Every video across all channels, including image-model comparisons and language variants.</p>

      <div className="row" style={{ marginBottom: 16 }}>
        <Select value={channel} onChange={setChannel}
          options={[{ value: '', label: 'All channels' }, ...channels.map((c) => ({ value: c.slug, label: c.name }))]} />
        <Select value={state} onChange={setState}
          options={STATES.map((s) => ({ value: s, label: s || 'All states' }))} />
      </div>

      <div className="card">
        {videos === null ? <Empty>Loading…</Empty> : videos.length === 0 ? (
          <Empty>No videos match these filters.</Empty>
        ) : (
          <table>
            <thead>
              <tr><th>Channel</th><th>Title</th><th>Image model</th><th>Languages</th><th>State</th><th>Duration</th><th>Cost</th><th>Created</th></tr>
            </thead>
            <tbody>
              {videos.map((v) => (
                <tr key={v.id} className="clickable" onClick={() => onOpenVideo(v.id)}>
                  <td className="nowrap">{v.channel}</td>
                  <td style={{ maxWidth: 280 }}>{v.title || v.topic || <span className="dim">untitled</span>}</td>
                  <td>{v.options?.image_model?.split('/').pop() || 'Original'}</td>
                  <td className="nowrap">
                    <span className="chip static">{v.language}</span>
                    {v.variants?.map((child) => (
                      <span className={`chip static ${child.has_output ? '' : 'dim'}`} key={child.id} title={child.state}>
                        {child.language}
                      </span>
                    ))}
                  </td>
                  <td style={{ minWidth: 150 }}>
                    <StatePill state={v.state} />
                    {!['READY', 'FAILED', 'AWAITING_APPROVAL'].includes(v.state) && <Progress value={v.progress} />}
                    {v.error ? <div className="tiny" style={{ color: 'var(--bad)', marginTop: 4 }}>{v.error.slice(0, 60)}</div> : null}
                  </td>
                  <td className="nowrap">{v.duration_seconds ? `${v.duration_seconds.toFixed(1)}s` : '—'}</td>
                  <td className="nowrap">{moneyShort(v.cost)}</td>
                  <td className="dim tiny nowrap">{v.created_at ? new Date(v.created_at).toLocaleString() : ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  )
}
