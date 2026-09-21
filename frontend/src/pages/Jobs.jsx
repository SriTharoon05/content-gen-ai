import { useEffect, useState } from 'react'
import { api } from '../api'
import { useStore } from '../store.jsx'
import { Select, Empty } from '../components/ui.jsx'

const TONE = { queued: '', running: 'busy', done: 'good', failed: 'bad' }

export default function Jobs({ onOpenVideo }) {
  const { notify } = useStore()
  const [jobs, setJobs] = useState(null)
  const [status, setStatus] = useState('')

  const load = () => {
    const q = status ? `?status=${status}` : ''
    api.jobs(q).then((r) => setJobs(r.jobs)).catch(() => setJobs([]))
  }
  useEffect(() => { load() }, [status])
  useEffect(() => {
    const interval = setInterval(load, 5000)
    return () => clearInterval(interval)
  }, [status])

  const retry = async (id) => {
    try { await api.retryJob(id); notify('Job requeued'); load() } catch (error) { notify(error.message, 'error') }
  }
  const cancel = async id => {
    if (!window.confirm('Cancel this queued or expired job? Saved media will be retained.')) return
    try {await api.cancelJob(id);notify('Job cancelled; media retained');load()} catch(error) {notify(error.message,'error')}
  }

  return (
    <>
      <h1 className="page-title">Jobs</h1>
      <p className="page-sub">The processing queue: every generation and regeneration that has run or is running.</p>

      <div className="row" style={{ marginBottom: 14 }}>
        <Select value={status} onChange={setStatus} options={[
          { value: '', label: 'All statuses' }, { value: 'queued', label: 'Queued' },
          { value: 'running', label: 'Running' }, { value: 'done', label: 'Done' }, { value: 'failed', label: 'Failed' },
        ]} />
      </div>

      <div className="card">
        {jobs === null ? <Empty>Loading…</Empty> : jobs.length === 0 ? <Empty>No jobs match this filter.</Empty> : (
          <table>
            <thead><tr><th>Stage</th><th>Status</th><th>Attempts</th><th>Video</th><th>Error</th><th></th></tr></thead>
            <tbody>
              {jobs.map((j) => (
                <tr key={j.id}>
                  <td className="nowrap">{j.stage}</td>
                  <td><span className={`pill ${TONE[j.status] || ''}`}>{j.status}</span></td>
                  <td className="tiny">{j.attempts}/{j.max_attempts}</td>
                  <td className="mono tiny clickable" onClick={() => onOpenVideo(j.video_id)}>{j.video_id.slice(0, 8)}</td>
                  <td className="tiny" style={{ maxWidth: 320, color: j.error ? 'var(--bad)' : undefined }}>{j.error}</td>
                  <td>{['failed','cancelled'].includes(j.status) && <button className="btn small" onClick={() => retry(j.id)}>Retry</button>}
                  {['queued','running'].includes(j.status) && j.stage !== 'publish' && <button className="btn small" onClick={()=>cancel(j.id)}>Cancel queued / expired</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  )
}
