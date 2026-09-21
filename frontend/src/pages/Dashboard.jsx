import { useEffect, useState } from 'react'
import { api, moneyShort } from '../api'
import { useStore } from '../store.jsx'
import { Stat, StatePill, Progress } from '../components/ui.jsx'

function Blockers({ health, channels }) {
  const notes = []
  if (!health?.ffmpeg) notes.push({ kind: 'err', text: 'FFmpeg was not found. Install it or set the path in Settings → Runtime.' })
  if (!health?.ffprobe) notes.push({ kind: 'err', text: 'FFprobe was not found. Same fix as FFmpeg.' })
  if (!health?.caption_font) notes.push({ kind: 'err', text: 'Caption font missing. Run: python scripts/setup_caption_assets.py' })
  if (!health?.paid_audio_key && !(health?.keys || []).some(k => ['gemini_free','groq'].includes(k.name) && k.healthy > 0)) notes.push({ kind: 'warn', text: 'Add a Gemini or Groq narration key in Settings.' })

  const textPool = (health?.keys || []).find((k) => k.active_for_text)
  if (textPool && textPool.healthy === 0) {
    notes.push({ kind: 'err', text: `No healthy ${health.text_tier} Gemini keys for text generation.` })
  }
  const imgPool = (health?.keys || []).find((k) => k.name === 'pollinations')
  if (imgPool && imgPool.healthy === 0) notes.push({ kind: 'err', text: 'No healthy Pollinations keys — images cannot generate.' })

  if (health?.alignment?.provider === 'groq' && !health?.alignment?.groq_keys) {
    notes.push({ kind: 'warn', text: 'Groq alignment selected but no Groq key configured — captions will fall back to estimated timing.' })
  } else if (health?.alignment?.provider !== 'groq' && !health?.alignment?.vosk_installed) {
    notes.push({ kind: 'warn', text: 'No word aligner installed — captions will use estimated timing.' })
  }
  if (!channels?.some((c) => c.enabled)) notes.push({ kind: 'warn', text: 'No channels are enabled.' })
  if (!notes.length) return null

  return (
    <div className="grid" style={{ marginBottom: 18 }}>
      {notes.map((n, i) => <div className={`note ${n.kind}`} key={i}>{n.text}</div>)}
    </div>
  )
}

export default function Dashboard({ onOpenVideo, onNavigate }) {
  const { health, costs, channels, refreshLive, notify } = useStore()
  const [recent, setRecent] = useState([])
  const [running, setRunning] = useState(false)

  useEffect(() => {
    api.videos('?limit=8&include_variants=true').then((r) => setRecent(r.videos)).catch(() => {})
  }, [])

  const runScheduled = async () => {
    setRunning(true)
    try {
      const result = await api.scheduleNow()
      notify(`Queued ${result.queued ?? 0} video(s) from the scheduled batch`)
      await refreshLive()
    } catch (error) {
      notify(error.message, 'error')
    } finally {
      setRunning(false)
    }
  }

  const textPool = (health?.keys || []).find((k) => k.active_for_text)
  const imagePool = (health?.keys || []).find((k) => k.name === 'pollinations')
  const groqPool = (health?.keys || []).find((k) => k.name === 'groq')

  return (
    <>
      <div className="eyebrow">FROM A SPARK TO A STORY</div>
      <h1 className="page-title">Make something worth watching.</h1>
      <p className="page-sub">Your channels, your stories, your next great idea. Everything in one place.</p>

      <Blockers health={health} channels={channels} />

      <div className="grid cols-4" style={{ marginBottom: 16 }}>
        <Stat label="Credits remaining" value={costs?.credits?.remaining ?? '—'}
          sub={`${costs?.credits?.images_affordable ?? 0} images left on ${costs?.credits?.model || ''}`} />
        <Stat label="Total spent" value={moneyShort(costs?.total?.spent)}
          sub={`${costs?.total?.videos ?? 0} videos rendered`} />
        <Stat label="Per video" value={moneyShort(costs?.total?.per_video)} sub="average, all providers" />
        <Stat label="Reuse savings" value={moneyShort({ usd: costs?.reuse_savings?.usd_saved, inr: costs?.reuse_savings?.inr_saved })}
          sub={`${costs?.reuse_savings?.reuses ?? 0} images reused within videos`} />
      </div>

      <div className="grid cols-2" style={{ marginBottom: 16 }}>
        <div className="card">
          <h3>Key pools</h3>
          <table>
            <thead><tr><th>Pool</th><th>Healthy</th><th>Cooling</th><th>Rejected</th><th></th></tr></thead>
            <tbody>
              {(health?.keys || []).map((k) => (
                <tr key={k.name}>
                  <td>{k.name}{k.active_for_text ? <span className="pill good" style={{ marginLeft: 6 }}>active for text</span> : null}</td>
                  <td>{k.healthy} / {k.total}</td>
                  <td>{k.cooling}</td>
                  <td>{k.rejected}</td>
                  <td>{k.total === 0 ? <span className="pill bad">none configured</span> : null}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted tiny" style={{ marginTop: 10 }}>
            Text tier: <b>{health?.text_tier}</b> · Image model: <b>{health?.image_model}</b> (
            {health?.credits_per_image} credits/image) · Runtime: <b>{health?.runtime}</b>
          </p>
        </div>

        <div className="card">
          <h3>Run the scheduled batch now</h3>
          <p className="muted tiny">
            {health?.scheduler?.enabled
              ? `Automatic daily run is scheduled for ${health.scheduler.run_at}.`
              : 'Automatic daily run is currently disabled in Settings → Schedule.'}
          </p>
          <button className="btn primary" onClick={runScheduled} disabled={running}>
            {running ? 'Queuing…' : 'Run scheduled batch now'}
          </button>
          <p className="dim tiny" style={{ marginTop: 10 }}>
            Uses each enabled channel's configured videos-per-run and the daily credit ceiling. Go to
            Channels & Runs for one-off runs with custom settings.
          </p>
        </div>
      </div>

      <div className="card">
        <div className="row between" style={{ marginBottom: 12 }}>
          <h3 style={{ margin: 0 }}>Recent videos</h3>
          <button className="btn small ghost" onClick={() => onNavigate('videos')}>View all</button>
        </div>
        {recent.length === 0 ? (
          <p className="muted">Nothing produced yet. Head to Channels & Runs to start one.</p>
        ) : (
          <table>
            <thead><tr><th>Channel</th><th>Title</th><th>Languages</th><th>State</th><th>Cost</th><th></th></tr></thead>
            <tbody>
              {recent.map((v) => (
                <tr key={v.id} className="clickable" onClick={() => onOpenVideo(v.id)}>
                  <td className="nowrap">{v.channel}</td>
                  <td>{v.title || v.topic || <span className="dim">untitled</span>}</td>
                  <td className="nowrap">
                    <span className="chip static">{v.language}</span>
                    {v.variants?.map((child) => <span className="chip static" key={child.id}>{child.language}</span>)}
                  </td>
                  <td style={{ minWidth: 140 }}>
                    <StatePill state={v.state} />
                    {!['READY', 'FAILED', 'AWAITING_APPROVAL'].includes(v.state) && <Progress value={v.progress} />}
                  </td>
                  <td className="nowrap">{moneyShort(v.cost)}</td>
                  <td className="dim">→</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  )
}
