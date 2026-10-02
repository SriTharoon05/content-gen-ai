import {useState} from 'react'
import {cloudflareRequest} from '../cloudflareApi'
import {analyticsTable} from '../cloudflareDashboard'
import {Field,Select} from './ui'

export default function CloudflareAnalytics({channel,connection}) {
  const [platform,setPlatform]=useState('youtube'),[days,setDays]=useState('28')
  const [report,setReport]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState('')
  const load=async()=>{
    if(busy||!connection?.[platform])return
    setBusy(true);setError('');setReport(null)
    try{const data=await cloudflareRequest(`/channels/${encodeURIComponent(channel.slug)}/analytics/${platform}?days=${days}`);setReport({platform,days,data})}
    catch(e){setError(e.message)}finally{setBusy(false)}
  }
  const table=report?analyticsTable(report.platform,report.data):null
  return <section className="card cf-panel"><h3>Account analytics</h3><p className="muted">Loads insights only when requested, using the connected account’s analytics access.</p>
    <div className="row cf-actions"><Field label="Platform"><Select disabled={busy} value={platform} onChange={value=>{setPlatform(value);setReport(null);setError('')}} options={[{value:'youtube',label:'YouTube'},{value:'instagram',label:'Instagram'}]}/></Field>
      <Field label="Reporting period"><Select disabled={busy} value={days} onChange={setDays} options={['7','14','28'].map(value=>({value,label:`Last ${value} days`}))}/></Field>
      <button className="btn primary" disabled={busy||!connection?.[platform]} onClick={load}>{busy?'Loading analytics…':'Load analytics'}</button>
    </div>
    {!connection?.[platform]&&<p className="note info">Connect {platform==='youtube'?'YouTube':'Instagram'} above to view its metrics.</p>}
    {error&&<p className="note err" role="alert">{error}</p>}
    {report&&<><p className="muted">{report.platform==='youtube'?'YouTube daily metrics':'Instagram period totals'} · last {report.days} days, through yesterday. Recent metrics may be delayed by the platform.</p>{table.rows.length?<div className="table-scroll"><table><thead><tr>{table.columns.map((name,i)=><th key={i}>{name}</th>)}</tr></thead><tbody>{table.rows.map((row,i)=><tr key={i}>{row.map((value,j)=><td key={j}>{value??'Unavailable'}</td>)}</tr>)}</tbody></table></div>:<p className="muted">No metrics returned for this account and period.</p>}</>}
  </section>
}
