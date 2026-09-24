import {useCallback,useEffect,useRef,useState} from 'react'
import BackendSwitch from '../components/BackendSwitch'
import GenerationTime from '../components/GenerationTime'
import CloudflarePublishing from '../components/CloudflarePublishing'
import CloudflareSchedule from '../components/CloudflareSchedule'
import CloudflareEditor from '../components/CloudflareEditor'
import CloudflareMusic from '../components/CloudflareMusic'
import CloudflareChannels from '../components/CloudflareChannels'
import CloudflareSettings from '../components/CloudflareSettings'
import {Field,Select,Chips,StatePill} from '../components/ui'
import {cloudflareRequest,postCloudflare,safeMediaUrl} from '../cloudflareApi'
import './CloudflareWorkspace.css'

const tabs=[['videos','Videos & review'],['channels','Channels & accounts'],['schedule','Schedule'],['music','Music library'],['settings','Settings']]

export default function CloudflareWorkspace() {
  const [channels,setChannels]=useState([]),[videos,setVideos]=useState([]),[channel,setChannel]=useState('lorehush')
  const [error,setError]=useState(''),[busy,setBusy]=useState(false),[selected,setSelected]=useState(null),[tab,setTab]=useState('videos')
  const [loadError,setLoadError]=useState(''),loading=useRef(false)
  const [submission,setSubmission]=useState(null),[dirty,setDirty]=useState(false),[confirm,setConfirm]=useState(false)
  const [mode,setMode]=useState('review'),[platforms,setPlatforms]=useState(['youtube']),[topic,setTopic]=useState('')
  const load=useCallback(async()=>{
    if(loading.current)return;loading.current=true
    try{const[c,v]=await Promise.all([cloudflareRequest('/channels'),cloudflareRequest('/videos')]);setChannels(c.channels||[]);setVideos(v.videos||[]);setLoadError('')}
    catch(e){setLoadError(e.message)}finally{loading.current=false}
  },[])
  useEffect(()=>{load();const timer=setInterval(load,10000);return()=>clearInterval(timer)},[load])
  const leave=action=>{if(dirty&&!window.confirm('Discard unapplied soundtrack changes?'))return;setDirty(false);action()}
  useEffect(()=>{const guard=e=>{if(dirty){e.preventDefault();e.returnValue=''}};window.addEventListener('beforeunload',guard);return()=>window.removeEventListener('beforeunload',guard)},[dirty])
  const run=async()=>{
    const request=submission||{id:crypto.randomUUID().replaceAll('-',''),channel,body:{review_required:mode!=='direct',publishing_mode:mode,publish_platforms:platforms,...(topic.trim()?{topic:topic.trim()}:{})}}
    setSubmission(request);setBusy(true);setError('')
    try{const result=await postCloudflare(`/channels/${request.channel}/run`,request.body,{headers:{'Idempotency-Key':request.id}});setSelected(result.video_id);setSubmission(null);setConfirm(false);await load()}
    catch(e){if(e.status>=400&&e.status<500&&e.status!==429){setSubmission(null);setConfirm(false);setError(e.message)}else setError(`${e.message}. Retry uses the same request ID and saved inputs to prevent duplicates.`)}finally{setBusy(false)}
  }
  const video=videos.find(v=>v.id===selected)
  const renderBusy=video&&(['CF_GENERATING','CF_EDITING'].includes(video.state)||!!video.active_edit_id)
  const selectable=channels.filter(c=>c.enabled&&c.supported!==false)
  const selectedChannel=channels.find(c=>c.slug===channel)
  return <div className="cf-workspace main">
    <header className="workspace-header"><div><p className="eyebrow">STORY SHORTS</p><strong>Cloudflare control room</strong></div><span className="workspace-badge">Cloudinary · CircleCI · Supabase</span></header>
    <BackendSwitch/>
    <nav className="cf-tabs" aria-label="Cloudflare workspace">{tabs.map(([id,label])=><button key={id} className={`btn ${tab===id?'primary':''}`} aria-current={tab===id?'page':undefined} onClick={()=>leave(()=>setTab(id))}>{label}</button>)}</nav>
    {error&&<p className="note err" role="alert">{error} <button className="btn small" onClick={load}>Refresh</button></p>}
    {loadError&&<p className="note err" role="alert">Status refresh failed: {loadError} <button className="btn small" onClick={load}>Retry</button></p>}
    {tab==='videos'&&<>
      <section className="card cf-panel"><div className="row between"><div><h1 className="page-title">Videos & review</h1><p className="muted">Generate, refine, then publish to your connected accounts.</p></div><button className="btn" onClick={load}>Refresh status</button></div>
        <fieldset disabled={busy||!!submission} className="cf-fieldset"><div className="grid cols-3">
          <Field label="Channel"><Select value={channel} onChange={setChannel} options={selectable.map(c=>({value:c.slug,label:c.name}))}/></Field>
          <Field label="After generation"><Select value={mode} onChange={setMode} options={[{value:'review',label:'Review and approve'},{value:'direct',label:'Publish directly'},{value:'settings',label:'Use saved publishing policy'}]}/></Field>
          <Field label="Topic for this run (optional)"><input value={topic} onChange={e=>setTopic(e.target.value)} placeholder="Leave blank for an original idea"/></Field>
        </div><Field label="Publishing destinations"><Chips value={platforms} onChange={setPlatforms} options={[{value:'youtube',label:'YouTube'},{value:'instagram',label:'Instagram'}]}/></Field></fieldset>
        {selectedChannel?.supported===false&&<p className="note warn">{selectedChannel.unsupported_reason||'This channel format is not supported by the current Cloudflare generation path.'}</p>}
        {mode==='direct'&&<p className="note warn">Direct mode uploads the finished video automatically to the selected connected accounts. Use review mode to inspect it first.</p>}
        {submission?<button className="btn primary" disabled={busy} onClick={run}>{busy?'Submitting…':'Retry same submission'}</button>:confirm?<div className="note warn"><p>Create one new video for {selectedChannel?.name||channel}? This uses provider quota and paid image credits.{mode==='direct'?' It will publish automatically.':''}</p><div className="row"><button className="btn primary" disabled={busy} onClick={run}>Confirm & generate</button><button className="btn" onClick={()=>setConfirm(false)}>Cancel</button></div></div>:<button className="btn primary" disabled={busy||!selectable.some(c=>c.slug===channel)||!platforms.length||dirty} onClick={()=>setConfirm(true)}>Generate one fresh video</button>}
      </section>
      {video&&<div className="cf-panel"><section className="card"><div className="row between"><div><h2>{video.title||video.channel}</h2><p>{video.channel} · <StatePill state={video.state}/></p></div><button className="btn" onClick={()=>leave(()=>setSelected(null))}>Close detail</button></div>
        <p>{video.stage_detail}</p><p><strong>Full generation time: <GenerationTime timing={video.generation_timing}/></strong></p>
        {safeMediaUrl(video.output)&&<video className="cf-video" key={video.output} controls playsInline preload="metadata" src={safeMediaUrl(video.output)}/>}
        {video.error&&<p className="note err" role="alert">{video.error}</p>}
        {video.publish_error&&<p className="note err" role="alert">Publishing: {video.publish_error}</p>}
        {(video.description||video.instagram_caption)&&<details className="cf-copy"><summary>Publishing captions & descriptions</summary>{video.description&&<><h3>YouTube</h3><p>{video.description}</p></>}{video.instagram_caption&&<><h3>Instagram</h3><p>{video.instagram_caption}</p></>}{video.hashtags?.length>0&&<p>{video.hashtags.join(' ')}</p>}</details>}
      </section>
      {video.output&&<><CloudflareEditor key={video.id} video={video} onDirty={setDirty} onUpdated={load}/><CloudflarePublishing key={video.id} video={video} disabled={dirty||renderBusy} onUpdated={load}/></>}
      </div>}
      <section className="card table-scroll cf-panel"><table><thead><tr><th>Video / channel</th><th>Status</th><th>Total generation</th><th>Length</th><th>Details</th></tr></thead><tbody>{videos.map(v=><tr key={v.id}><td><strong>{v.title||v.channel}</strong><div className="muted">{v.channel} · {v.id.slice(0,8)}</div></td><td><StatePill state={v.state}/><div className="muted">{v.stage_detail}</div></td><td><GenerationTime timing={v.generation_timing}/></td><td>{v.duration_seconds?`${v.duration_seconds.toFixed(1)}s`:'—'}</td><td><button className="btn small" onClick={()=>leave(()=>setSelected(v.id))}>{v.output?'Review / edit':'View progress'}</button></td></tr>)}</tbody></table>{!videos.length&&<p className="muted">No Cloudflare videos yet. Existing Render jobs remain in the Render workspace.</p>}</section>
    </>}
    {tab==='channels'&&<CloudflareChannels channels={channels} onUpdated={load}/>}
    {tab==='schedule'&&<CloudflareSchedule channels={channels}/>}
    {tab==='music'&&<CloudflareMusic/>}
    {tab==='settings'&&<CloudflareSettings/>}
  </div>
}
