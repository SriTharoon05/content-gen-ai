import { useState } from 'react'
import { backendBase, backendToken, selectedBackend, saveBackend } from '../backendChoice'

export default function BackendSwitch() {
  const [backend, setBackend] = useState(selectedBackend())
  const [token, setToken] = useState(backendToken())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const save = async () => {
    if (!window.confirm('Switch backend? Unsaved dashboard edits will be discarded. Existing jobs continue on their original backend.')) return
    setBusy(true); setError('')
    try {
      const r = await fetch(`${backendBase(backend)}/channels`, { headers: { Authorization: `Bearer ${token}` }, signal: AbortSignal.timeout(60000) })
      if (!r.ok) throw new Error(`Cannot connect to ${backend}: HTTP ${r.status}. Check its admin token.`)
      const data = await r.json()
      if (!Array.isArray(data.channels)) throw new Error('Unexpected backend response')
      saveBackend(backend, token)
      window.location.reload()
    } catch (e) { setError(e.message); setBusy(false) }
  }
  return <details className="card" style={{ marginBottom: 16 }}>
    <summary><strong>Backend: {selectedBackend() === 'render' ? 'Render' : 'Cloudflare'}</strong> · Change connection</summary>
    <div className="grid cols-2" style={{ marginTop: 16 }}>
      <label>Backend<select value={backend} onChange={e => { setBackend(e.target.value); setToken(backendToken(e.target.value)) }}>
        <option value="render">Render</option><option value="cloudflare">Cloudflare</option>
      </select></label>
      <label>Admin token for {backend}<input type="password" autoComplete="off" value={token} onChange={e => setToken(e.target.value)} /></label>
    </div>
    <p className="muted tiny">Saved on this browser. Existing jobs stay on their original backend. Daily scheduling has a separate owner in Schedule; switching this connection does not transfer it.</p>
    {error && <p className="note err" role="alert">{error}</p>}
    <button className="btn primary" disabled={busy} onClick={save}>{busy ? 'Checking connection…' : 'Save backend & reconnect'}</button>
  </details>
}
