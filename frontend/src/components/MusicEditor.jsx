import { useEffect, useState } from 'react'
import { api, mediaUrl } from '../api'
import { useStore } from '../store'
import { Field, Select, Num, Slider, Toggle } from './ui'
import CropAudio from './CropAudio'
import LiveMusicPreview from './LiveMusicPreview'
import { musicOption, musicUnavailable } from '../musicOptions'

export default function MusicEditor({video,onApplied,onDirty}) {
  const {music,notify}=useStore()
  const spec=video.spec || {}
  const initial={music_track:spec.music_track || '',music_volume_pct:spec.music_volume_pct ?? 30,
    music_start_seconds:spec.music_start_seconds ?? 0,music_end_seconds:spec.music_end_seconds ?? 0,ducking:spec.ducking ?? true}
  const [form,setForm]=useState(initial)
  const [category,setCategory]=useState(music.tracks.find(t=>t.id===initial.music_track)?.category || '')
  const [busy,setBusy]=useState(false)
  const [changed,setChanged]=useState(false)
  useEffect(()=>{onDirty(changed);return ()=>onDirty(false)},[changed,onDirty])
  const tracks=music.tracks.filter(t=>!category || t.category===category)
  const unavailable=tracks.filter(t=>musicUnavailable(t,video.channel))
  const selected=music.tracks.find(t=>t.id===form.music_track)
  const update=patch=>{setForm(f=>({...f,...patch}));setChanged(true)}
  const choose=id=>{const t=music.tracks.find(t=>t.id===id);update({music_track:id,music_start_seconds:t?.trim_start || 0,
    music_end_seconds:t?.trim_end || t?.duration_seconds || 0,music_volume_pct:t?.default_volume_pct ?? form.music_volume_pct})}
  const apply=async()=>{setBusy(true);try{await api.regenerate(video.id,'music',form);setChanged(false);notify('Applying music. Nothing is uploaded.');await onApplied()}
    catch(e){notify(e.message,'error')}finally{setBusy(false)}}
  return <div className="card" style={{marginBottom:20}}><h3>Background music editor</h3>
    <p className="muted tiny">Reuses the finished visuals, captions and clean narration. Every applied version is saved. Upload and manage tracks in Music / BGM.</p>
    <div className="grid cols-2">
      <Field label="Category folder"><Select value={category} onChange={setCategory} options={[{value:'',label:'All folders'},...music.categories.map(c=>({value:c,label:`${c} (${music.tracks.filter(t=>t.category===c && !musicUnavailable(t,video.channel)).length} available)`}))]}/></Field>
      <Field label="Background music"><Select value={form.music_track} onChange={choose} options={[{value:'',label:'No background music'},...(selected && !tracks.some(t=>t.id===selected.id)?[{value:selected.id,label:`Current: ${selected.name} (${selected.category})`}]:[]),...tracks.map(t=>musicOption(t,video.channel))]}/></Field>
    </div>
    {!tracks.length && <p className="note info">This folder has no tracks. Upload music in Music / BGM.</p>}
    {!!unavailable.length && <div className="note info" style={{marginBottom:14}}>
      {unavailable.map(t=><p key={t.id}>{t.name}: {musicUnavailable(t,video.channel)}.</p>)}
      Manage missing audio or usage rights in Music / BGM. Channel preferences never restrict manual selection.
    </div>}
    {selected && <><CropAudio src={mediaUrl.music(selected.id)} start={form.music_start_seconds} end={form.music_end_seconds}/>
      <div className="grid cols-2"><Field label="Crop start (seconds)"><Num value={form.music_start_seconds} onChange={v=>update({music_start_seconds:v})} min={0} max={selected.duration_seconds} step={.1}/></Field>
      <Field label="Crop end (seconds)"><Num value={form.music_end_seconds} onChange={v=>update({music_end_seconds:v})} min={0} max={selected.duration_seconds} step={.1}/></Field></div></>}
    <Slider label="Music intensity" value={form.music_volume_pct} suffix="%" onChange={v=>update({music_volume_pct:v})}/>
    <p className="dim tiny">100% uses the configured safe music ceiling, not full narration volume.</p>
    <Toggle label="Lower music while narration speaks" value={form.ducking} onChange={v=>update({ducking:v})}/>
    <LiveMusicPreview video={video} track={selected} form={form} ceiling={music.max_intensity ?? .25}/>
    <div className="row"><button className="btn primary" disabled={busy || !changed} onClick={apply}>{busy?'Queuing…':'Apply soundtrack'}</button>
    <button className="btn" disabled={!changed || busy} onClick={()=>{setForm(initial);setChanged(false);setCategory('')}}>Discard changes</button></div>
    {changed && <p className="note warn">Apply or discard these changes before approving an upload.</p>}
    {!!video.options?.music_revisions?.length && <details style={{marginTop:14}}><summary>Previous soundtrack versions ({video.options.music_revisions.length})</summary>
      {video.options.music_revisions.map((r,i)=><p key={r.output}><a href={r.output} target="_blank" rel="noreferrer">Preview version {i+1} · {r.music?.music_name || 'No background music'}</a></p>)}
    </details>}
  </div>
}
