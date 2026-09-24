import { useEffect, useState } from 'react'
import { cloudflareRequest, postCloudflare } from '../cloudflareApi'
import { Field, Num, Select, Toggle, Chips } from './ui'

export default function CloudflareSchedule({ channels }) {
  const [saved, setSaved] = useState(null), [draft, setDraft] = useState(null), [error, setError] = useState(''), [busy, setBusy] = useState(false), [notice, setNotice] = useState('')
  const load = async () => { try { const d = await cloudflareRequest('/schedule'); setSaved(d); setDraft(d); setError('') } catch (e) { setError(e.message) } }
  useEffect(() => { load() }, [])
  const set = patch => { setDraft(d => ({ ...d, schedule: { ...d.schedule, ...patch } })); setNotice('') }
  const save = async () => {
    if (draft.owner !== saved.owner && !window.confirm(`Transfer automatic daily scheduling to ${draft.owner}? Existing jobs continue on their original backend.`)) return
    setBusy(true); setError(''); setNotice('')
    try { await postCloudflare('/schedule', { owner: draft.owner, schedule: draft.schedule }); await load(); setNotice('Schedule saved. Only its selected owner may claim the daily batch.') }
    catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  const s = draft?.schedule
  return <section className="card cf-panel"><h3>Daily schedule</h3>
    <p className="muted">Dashboard connection and scheduler ownership are separate. One backend owns the daily batch so both deployments cannot generate it twice.</p>
    {error && <p className="note err" role="alert">{error} <button className="btn small" onClick={load}>Reload schedule</button></p>}
    {s && <><div className="grid cols-2">
      <Field label="Scheduling backend"><Select value={draft.owner} onChange={owner => setDraft(d => ({ ...d, owner }))} options={[{value:'render',label:'Render'},{value:'cloudflare',label:'Cloudflare'}]}/></Field>
      <Field label="Run time"><input type="time" value={s.run_at || '10:00'} onChange={e => set({run_at:e.target.value})}/></Field>
      <Field label="UTC offset (minutes)" hint="330 = India Standard Time. Adjust manually for daylight saving."><Num min={-720} max={840} value={s.timezone_offset_minutes ?? 330} onChange={v => set({timezone_offset_minutes:v})}/></Field>
      <Field label="Videos per channel"><Num min={1} max={20} value={s.videos_per_channel || 1} onChange={v => set({videos_per_channel:v})}/></Field>
      <Field label="Daily image credit ceiling"><Num min={0} step={.01} value={s.daily_credit_ceiling ?? .2} onChange={v => set({daily_credit_ceiling:v})}/></Field>
    </div>
    <Toggle label="Enable daily generation" value={s.enabled} onChange={v => set({enabled:v})}/>
    <Toggle label="Publish automatically when ready" value={s.auto_publish} onChange={v => set({auto_publish:v})} hint="Requires connected accounts and a publishing policy that allows direct uploads. Otherwise videos wait for review."/>
    <Field label="Scheduled channels" hint="Leave empty for all enabled supported channels."><Chips value={s.channels || []} onChange={v => set({channels:v})} options={channels.filter(c => c.enabled && c.supported !== false).map(c => ({value:c.slug,label:c.name}))}/></Field>
    <div className="row"><button className="btn primary" disabled={busy || JSON.stringify(saved)===JSON.stringify(draft)} onClick={save}>{busy?'Saving…':'Save schedule'}</button><button className="btn" disabled={busy} onClick={() => {setDraft(saved);setNotice('')}}>Discard edits</button></div>
    <p className="note info">To test: select one channel and one video, choose a time a few minutes ahead in the selected timezone, save, then watch Videos. A batch already claimed today will not run twice.</p></>}
    {notice && <p className="note info" role="status">{notice}</p>}
  </section>
}
