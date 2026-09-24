import { useEffect, useState } from 'react'
import BackendSwitch from '../components/BackendSwitch'
import GenerationTime from '../components/GenerationTime'
import { backendBase, backendToken } from '../backendChoice'

export default function CloudflareWorkspace() {
  const [channels,setChannels]=useState([]),[videos,setVideos]=useState([]),[channel,setChannel]=useState('lorehush')
  const [error,setError]=useState(''),[busy,setBusy]=useState(false),[selected,setSelected]=useState(null)
  const [requestId,setRequestId]=useState(null)
  const request=async(path,options={})=>{
    const r=await fetch(backendBase()+path,{...options,headers:{Authorization:`Bearer ${backendToken()}`,'Content-Type':'application/json',...options.headers}})
    const data=await r.json();if(!r.ok)throw new Error(data.detail||`HTTP ${r.status}`);return data
  }
  const load=async()=>{
    try{const [c,v]=await Promise.all([request('/channels'),request('/videos')]);setChannels(c.channels);setVideos(v.videos);setError('')}
    catch(e){setError(e.message)}
  }
  useEffect(()=>{load();const timer=setInterval(load,10000);return()=>clearInterval(timer)},[])
  const run=async()=>{
    const id=requestId||crypto.randomUUID().replaceAll('-','');setRequestId(id);setBusy(true)
    try{
      const result=await request(`/channels/${channel}/run`,{method:'POST',headers:{'Idempotency-Key':id},body:JSON.stringify({review_required:true})})
      setSelected(result.video_id);setRequestId(null);await load()
    }catch(e){setError(`${e.message}. Retry uses the same request ID to prevent duplicates.`)}finally{setBusy(false)}
  }
  const video=videos.find(v=>v.id===selected)
  return <div className="main" style={{maxWidth:1400,margin:'0 auto'}}>
    <header className="workspace-header"><b>Story Shorts · Cloudflare</b><span className="workspace-badge">Review-only generation</span></header>
    <BackendSwitch />
    <h1 className="page-title">Cloudflare generation workspace</h1>
    <p className="note info">Native Cloudflare generation, Supabase database, Cloudinary media and CircleCI rendering. Publishing, scheduling, channel editing and BGM editing are not migrated here yet. Switch to Render for those features. Switching does not move existing jobs or schedules.</p>
    {error&&<p className="note err" role="alert">{error}</p>}
    <section className="card">
      <h3>Generate one fresh video</h3><div className="row">
        <select aria-label="Generation channel" value={channel} disabled={busy||!!requestId} onChange={e=>setChannel(e.target.value)}>
          {channels.filter(c=>c.enabled&&c.supported).map(c=><option key={c.slug} value={c.slug}>{c.name}</option>)}
        </select>
        <button className="btn primary" disabled={busy||!channels.some(c=>c.slug===channel&&c.enabled&&c.supported)} onClick={run}>{busy?'Submitting…':requestId?'Retry submission':'Generate fresh video — review only'}</button>
      </div><p className="muted">Uses provider quota and image credits. Nothing will be published automatically.</p>
    </section>
    {video&&<section className="card"><h3>{video.title||video.channel}</h3>
      <p>{video.state} · {video.stage_detail}</p><p><strong>Full generation time: <GenerationTime timing={video.generation_timing}/></strong></p>
      {video.output&&<video controls preload="metadata" src={video.output} style={{maxHeight:640,maxWidth:'100%'}}/>}
      {video.error&&<p className="note err">{video.error}</p>}
    </section>}
    <section className="card table-scroll"><table><thead><tr><th>Video / channel</th><th>Stage</th><th>Generation time</th><th>Video length</th><th>Open</th></tr></thead>
      <tbody>{videos.map(v=><tr key={v.id}><td>{v.title||v.channel}<div className="muted tiny">{v.id.slice(0,8)}</div></td><td>{v.state}<div>{v.stage_detail}</div></td><td><GenerationTime timing={v.generation_timing}/></td><td>{v.duration_seconds?`${v.duration_seconds.toFixed(1)}s`:'—'}</td><td><button className="btn small" onClick={()=>setSelected(v.id)}>View progress / preview</button></td></tr>)}</tbody></table></section>
  </div>
}
