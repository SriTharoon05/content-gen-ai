import { useEffect, useState } from 'react'
import { cloudflareRequest, postCloudflare, musicCropValid, safeMediaUrl } from '../cloudflareApi'
import { Field, Num, Select, Slider, Toggle } from './ui'
import LiveMusicPreview from './LiveMusicPreview'

export default function CloudflareEditor({video,onDirty,onUpdated}) {
  const initial=()=>({music_track:video.music?.music_track||'',music_volume_pct:video.music?.music_volume_pct??30,music_start_seconds:video.music?.music_start_seconds||0,music_end_seconds:video.music?.music_end_seconds||0,ducking:video.music?.ducking??true})
  const [library,setLibrary]=useState(null),[form,setForm]=useState(initial),[category,setCategory]=useState(''),[speed,setSpeed]=useState(1)
  const [dirty,setDirty]=useState(false),[error,setError]=useState(''),[busy,setBusy]=useState(false),[edit,setEdit]=useState(null)
  const [submission,setSubmission]=useState(null)
  useEffect(()=>{cloudflareRequest('/music').then(setLibrary).catch(e=>setError(e.message))},[])
  useEffect(()=>{onDirty(dirty||!!submission);return()=>onDirty(false)},[dirty,submission,onDirty])
  useEffect(()=>{setForm(initial());setDirty(false);setSpeed(1)},[video.output,video.preview_revision])
  const editId=edit?.id||video.active_edit_id
  useEffect(()=>{
    if(!editId)return
    let live=true
    const poll=async()=>{try{const result=await cloudflareRequest(`/edits/${editId}`);if(!live)return;setEdit(result);if(['succeeded','failed'].includes(result.status)){clearInterval(timer);await onUpdated?.()}}catch(e){if(live)setError(e.message)}}
    const timer=setInterval(poll,10000);poll();return()=>{live=false;clearInterval(timer)}
  },[editId])
  const track=library?.tracks?.find(t=>t.id===form.music_track)
  const tracks=(library?.tracks||[]).filter(t=>!category||t.category===category)
  const categories=[...new Set([...(library?.categories||[]),...(library?.tracks||[]).map(t=>t.category)])]
  const locked=busy||video.state==='CF_EDITING'||['queued','running'].includes(edit?.status)
  const update=patch=>{setForm(f=>({...f,...patch}));setDirty(true)}
  const choose=id=>{const t=library?.tracks?.find(t=>t.id===id);update({music_track:id,music_start_seconds:t?.trim_start||0,music_end_seconds:t?.trim_end||t?.duration_seconds||0,music_volume_pct:t?.default_volume_pct??30})}
  const submit=async operation=>{
    const payload=submission?.body||{operation,revision:Number(video.preview_revision??0),...(operation==='bgm'?form:operation==='speed'?{playback_rate:speed}:{})}
    const id=submission?.id||crypto.randomUUID();setSubmission({id,body:payload});setBusy(true);setError('')
    try{const result=await postCloudflare(`/videos/${video.id}/edits`,payload,{headers:{'Idempotency-Key':id}});setEdit(result);setSubmission(null);setDirty(false);await onUpdated?.()}
    catch(e){
      if(e.status>=400&&e.status<500&&e.status!==429){setSubmission(null);setError(e.message);if(e.status===409)await onUpdated?.()}
      else setError(`${e.message}. Retrying sends the same edit request; do not create another edit while its outcome is unknown.`)
    }finally{setBusy(false)}
  }
  const narration=safeMediaUrl(video.narration_url||video.narration_path)
  const calibrated=Number.isFinite(library?.max_intensity)&&library.max_intensity>0
  const cropValid=!track||musicCropValid(form.music_start_seconds,form.music_end_seconds,track.duration_seconds)
  return <section className="card cf-panel"><h3>Asset-only editor</h3><p className="muted">Reuse this video’s images and script. CircleCI renders applied changes; speech-speed changes also realign the English captions. No new images or script are generated.</p>
    {edit&&<p className={`note ${edit.status==='failed'?'err':'info'}`} role="status">Edit {edit.id?.slice(0,8)} · {edit.status}{edit.error?` · ${edit.error}`:''}</p>}
    {submission?<div className="note warn"><p>An edit submission has not been confirmed. Its inputs are locked to prevent duplicate work.</p><button className="btn primary" disabled={busy} onClick={()=>submit(submission.body.operation)}>Retry same edit submission</button></div>:<>
    <fieldset disabled={locked} className="cf-fieldset"><div className="grid cols-2">
      <Field label="Music category"><Select value={category} onChange={setCategory} options={[{value:'',label:'All folders'},...categories.map(c=>({value:c,label:`${c} (${library?.tracks?.filter(t=>t.category===c).length||0})`}))]}/></Field>
      <Field label="Background track"><Select value={form.music_track} onChange={choose} options={[{value:'',label:'No background music'},...(track&&!tracks.some(t=>t.id===track.id)?[{value:track.id,label:`Current: ${track.name}`}]:[]),...tracks.map(t=>({value:t.id,label:t.name,disabled:t.rights_cleared===false||!t.sha256}))]}/></Field>
    </div>
    {tracks.some(t=>!t.sha256)&&<p className="note warn">Older tracks without verified checksums need migration or re-upload before Cloudflare can apply them.</p>}
    {track&&<div className="grid cols-2"><Field label="Music crop start (seconds)"><Num min={0} max={track.duration_seconds} step={.1} value={form.music_start_seconds} onChange={v=>update({music_start_seconds:v})}/></Field><Field label="Music crop end (seconds)"><Num min={0} max={track.duration_seconds} step={.1} value={form.music_end_seconds} onChange={v=>update({music_end_seconds:v})}/></Field></div>}
    <Slider label="Music intensity" value={form.music_volume_pct} suffix="%" onChange={v=>update({music_volume_pct:v})}/><Toggle label="Lower music while narration speaks" value={form.ducking} onChange={v=>update({ducking:v})}/>
    {!cropValid&&<p className="note warn">Choose at least 3 seconds inside the selected track.</p>}
    {narration&&safeMediaUrl(video.output)&&calibrated?<LiveMusicPreview video={{...video,output_revision:video.preview_revision}} track={track} form={form} ceiling={library.max_intensity} urls={{video:safeMediaUrl(video.output),narration,music:safeMediaUrl(track?.url||track?.path)}}/>:<p className="note info">{!calibrated?'The saved mix ceiling is unavailable; browser mixing is disabled until it can be calibrated.':'Clean narration is not available for browser mixing on this saved video.'} You can still apply a soundtrack and preview the rendered result.</p>}
    <div className="row"><button className="btn primary" disabled={!dirty||!cropValid||!library} onClick={()=>submit('bgm')}>Apply soundtrack</button><button className="btn" disabled={!dirty} onClick={()=>{setForm(initial());setDirty(false)}}>Discard soundtrack edits</button></div>
    <hr/><div className="grid cols-2"><div><Slider label="Narration speed" value={speed} min={.75} max={1.25} step={.01} suffix="×" onChange={setSpeed}/><p className="muted">Below 1× slows narration. Output must stay between 45 and 90 seconds.</p><button className="btn" disabled={dirty||speed===1} onClick={()=>submit('speed')}>Apply speed & align captions</button></div><div><h3>Rebuild from saved assets</h3><p className="muted">Re-render the existing script, images, narration and captions without new generation calls.</p><button className="btn" disabled={dirty} onClick={()=>submit('rerender')}>Re-render existing assets</button></div></div>
    </fieldset></>}
    {error&&<p className="note err" role="alert">{error}</p>}
  </section>
}
