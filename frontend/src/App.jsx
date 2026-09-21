import { useEffect, useState } from 'react'
import { useStore } from './store.jsx'
import Dashboard from './pages/Dashboard.jsx'
import Channels from './pages/Channels.jsx'
import Videos from './pages/Videos.jsx'
import VideoDetail from './pages/VideoDetail.jsx'
import Music from './pages/Music.jsx'
import Costs from './pages/Costs.jsx'
import Jobs from './pages/Jobs.jsx'
import Settings from './pages/Settings.jsx'
import Integrations from './pages/Integrations.jsx'
import { setToken } from './api'

const PAGES = [
  { key: 'dashboard', label: 'Dashboard', icon: '◆' },
  { key: 'channels', label: 'Channels & Runs', icon: '▶' },
  { key: 'videos', label: 'Videos', icon: '▤' },
  { key: 'music', label: 'Music / BGM', icon: '♪' },
  { key: 'costs', label: 'Costs', icon: '$' },
  { key: 'jobs', label: 'Jobs', icon: '⟳' },
  { key: 'settings', label: 'Settings', icon: '⚙' },
  { key: 'integrations', label: 'Publishing & analytics', icon: '↗' },
]

function HealthDot({ ok, warn }) {
  return <span className={`dot ${ok ? '' : warn ? 'warn' : 'off'}`} />
}

function Sidebar({ page, setPage, openVideo }) {
  const { health, dirty } = useStore()
  const dirtyBySection = {}
  for (const d of dirty) dirtyBySection[d.kind] = (dirtyBySection[d.kind] || 0) + 1

  return (
    <div className="sidebar">
      <div className="brand">
        <div className="mark">S</div>
        <div>
          <b>Story Shorts</b>
          <span>Multi-channel video studio</span>
        </div>
      </div>
      <div className="nav-caption">CREATOR WORKSPACE</div>
      <div className="nav">
        {PAGES.map((p) => (
          <button
            key={p.key}
            className={page === p.key && !openVideo ? 'active' : ''}
            onClick={() => setPage(p.key)}
          >
            <span>{p.icon}</span>
            {p.label}
            {p.key === 'settings' && dirty.length > 0 ? <span className="badge">{dirty.length}</span> : null}
          </button>
        ))}
      </div>
      <details className="health"><summary>System status</summary>
        <div className="line"><HealthDot ok={health?.ffmpeg} /> FFmpeg {health?.ffmpeg ? 'ready' : 'missing'}</div>
        <div className="line"><HealthDot ok={health?.caption_font} /> Caption font</div>
        <div className="line"><HealthDot ok={health?.paid_audio_key || (health?.keys || []).some(k=>['gemini_free','groq'].includes(k.name) && k.healthy>0)} /> Narration keys</div>
        <div className="line">
          <HealthDot ok={health?.alignment?.groq_keys > 0} warn={health?.alignment?.vosk_installed} />
          Alignment: {health?.alignment?.provider === 'groq'
            ? (health?.alignment?.groq_keys > 0 ? 'Groq ready' : 'Groq — no keys')
            : health?.alignment?.provider}
        </div>
        <div className="line">
          <HealthDot ok={(health?.keys || []).some((k) => k.active_for_text && k.healthy > 0)} />
          Text keys ({health?.text_tier})
        </div>
        <div className="line">
          <HealthDot ok={(health?.keys || []).find((k) => k.name === 'pollinations')?.healthy > 0} />
          Image keys
        </div>
      </details>
    </div>
  )
}

function SaveBar() {
  const { dirty, saving, savedAt, saveAll, discardAll } = useStore()
  if (!dirty.length && !savedAt) return null
  const labels = [...new Set(dirty.map((d) => d.label))]

  return (
    <div className="savebar">
      {dirty.length > 0 ? (
        <>
          <div style={{ flex: 1 }}>
            <b>{dirty.length} unsaved change{dirty.length === 1 ? '' : 's'}</b>
            <div className="dim tiny" style={{ marginTop: 2 }}>
              {labels.slice(0, 5).join(' · ')}{labels.length > 5 ? ` +${labels.length - 5} more` : ''}
            </div>
          </div>
          <button className="btn" onClick={discardAll} disabled={saving}>Discard</button>
          <button className="btn primary" onClick={saveAll} disabled={saving}>
            {saving ? 'Saving…' : 'Save All'}
          </button>
        </>
      ) : (
        <div className="muted tiny">
          Saved{savedAt ? ` at ${savedAt.toLocaleTimeString()}` : ''} — nothing pending
        </div>
      )}
    </div>
  )
}

function Toast() {
  const { toast } = useStore()
  if (!toast) return null
  return <div className={`toast ${toast.kind}`}>{toast.message}</div>
}

export default function App() {
  const { loading, bootError, loadAll, refreshLive } = useStore()
  const [page, setPage] = useState('dashboard')
  const [openVideo, setOpenVideo] = useState(null)

  useEffect(() => {
    const interval = setInterval(refreshLive, 8000)
    return () => clearInterval(interval)
  }, [refreshLive])

  const goToVideo = (id) => setOpenVideo(id)
  const closeVideo = () => setOpenVideo(null)
  const navigate = (p) => { setPage(p); setOpenVideo(null) }

  if (loading) {
    return (
      <div className="app">
        <div className="main" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '100vh' }}>
          <p className="muted">Loading dashboard…</p>
        </div>
      </div>
    )
  }

  if (bootError) {
    return (
      <div className="app">
        <div className="main" style={{ maxWidth: 520, margin: '80px auto' }}>
          <div className="note err">
            <b>Cannot reach the backend.</b>
            <p style={{ margin: '8px 0 0' }}>{bootError}</p>
          </div>
          <button className="btn primary" style={{ marginTop: 14 }} onClick={loadAll}>Retry</button>
          <p className="muted">For a protected deployment, enter your dashboard admin token and retry.</p>
          <input type="password" placeholder="Admin token" aria-label="Dashboard admin token" onChange={e => setToken(e.target.value)} />
        </div>
      </div>
    )
  }

  return (
    <div className="app">
      <Sidebar page={page} setPage={navigate} openVideo={openVideo} />
      <div className="main">
        <header className="workspace-header"><div>Workspace <span>/</span> <b>{openVideo ? 'Video workspace' : PAGES.find(p => p.key === page)?.label}</b></div><span className="workspace-badge">● Creator studio</span></header>
        <nav className="mobile-nav" aria-label="Workspace navigation">{PAGES.map(p => <button key={p.key} className={page === p.key ? 'active' : ''} onClick={() => navigate(p.key)}>{p.label}</button>)}</nav>
        {openVideo ? (
          <VideoDetail key={openVideo} videoId={openVideo} onClose={closeVideo} onOpenVideo={goToVideo} />
        ) : (
          <>
            {page === 'dashboard' && <Dashboard onOpenVideo={goToVideo} onNavigate={navigate} />}
            {page === 'channels' && <Channels onOpenVideo={goToVideo} />}
            {page === 'videos' && <Videos onOpenVideo={goToVideo} />}
            {page === 'music' && <Music />}
            {page === 'costs' && <Costs />}
            {page === 'jobs' && <Jobs onOpenVideo={goToVideo} />}
            {page === 'settings' && <Settings />}
            {page === 'integrations' && <Integrations onOpenVideo={goToVideo} />}
          </>
        )}
      </div>
      <SaveBar />
      <Toast />
    </div>
  )
}
