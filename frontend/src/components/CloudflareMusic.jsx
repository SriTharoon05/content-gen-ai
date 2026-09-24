import { useEffect, useState } from 'react'
import { cloudflareRequest, postCloudflare, musicCropValid, safeMediaUrl } from '../cloudflareApi'
import { Field, Num, Slider, Toggle, Select } from './ui'
import CropAudio from './CropAudio'

export default function CloudflareMusic() {
  const [library, setLibrary] = useState({tracks:[],categories:[]}), [loaded,setLoaded]=useState(false)
  const [error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false)
  const [file,setFile]=useState(null),[url,setUrl]=useState(''),[duration,setDuration]=useState(0)
  const [form,setForm]=useState({name:'',category:'drama',trim_start:0,trim_end:0,default_volume_pct:30,rights_cleared:false})
  const [category,setCategory]=useState('')
  const load=async()=>{try{setLibrary(await cloudflareRequest('/music'));setLoaded(true)}catch(e){setError(e.message)}}
  useEffect(()=>{load()},[])
  useEffect(()=>{if(!file){setUrl('');return}const next=URL.createObjectURL(file);setUrl(next);return()=>URL.revokeObjectURL(next)},[file])
  const update=patch=>setForm(f=>({...f,...patch}))
  const selectFile=e=>{
    const chosen=e.target.files?.[0];setError('');setNotice('');setDuration(0)
    if(chosen&&chosen.size>25*1024*1024){setError('Choose an audio file up to 25 MB.');e.target.value='';return}
    setFile(chosen||null);update({name:chosen?.name.replace(/\.[^.]+$/,'')||'',trim_start:0,trim_end:0})
  }
  const upload=async()=>{
    setBusy(true);setError('');setNotice('')
    try{
      const extension=file.name.split('.').pop().toLowerCase()
      if(!['mp3','wav','m4a','ogg','flac','aac'].includes(extension))throw Error('Choose MP3, WAV, M4A, OGG, FLAC or AAC audio.')
      const hash=await crypto.subtle.digest('SHA-256',await file.arrayBuffer())
      const sha256=Array.from(new Uint8Array(hash),b=>b.toString(16).padStart(2,'0')).join('')
      const capability=await postCloudflare('/music/upload',{sha256,extension})
      const target=new URL(capability.url)
      if(target.protocol!=='https:'||target.hostname!=='api.cloudinary.com')throw Error('Unexpected media upload destination')
      const body=new FormData();Object.entries(capability.fields).forEach(([k,v])=>body.append(k,v));body.append('file',file)
      const r=await fetch(target.href,{method:'POST',body,signal:AbortSignal.timeout(180000)})
      if(!r.ok)throw Error(`Music upload failed (HTTP ${r.status}); the library has not changed.`)
      await postCloudflare('/music',{...form,sha256,extension})
      setNotice('Music saved to the shared library. Its crop is reused when you apply it to a video.');setFile(null);await load()
    }catch(e){setError(e.message)}finally{setBusy(false)}
  }
  const remove=async track=>{
    if(!window.confirm(`Remove “${track.name}” from the music library? Existing finished videos are unchanged.`))return
    setBusy(true);setError('')
    try{await cloudflareRequest(`/music/${track.id}`,{method:'DELETE'});await load();setNotice('Track removed from the selectable library.')}
    catch(e){setError(e.message)}finally{setBusy(false)}
  }
  const tracks=library.tracks||[]
  const categories=[...new Set(['emotion','drama','suspense','thriller',...(library.categories||[]),...tracks.map(t=>t.category)])]
  return <div className="grid cols-2 cf-panels">
    <section className="card"><h3>Add background music</h3><p className="muted">Shared by all channels. Audio goes directly to Cloudinary; previewing and choosing a crop happens in your browser.</p>
      <Field label="Audio file · maximum 25 MB"><input type="file" accept=".mp3,.wav,.m4a,.ogg,.flac,.aac,audio/*" disabled={busy} onChange={selectFile}/></Field>
      {url&&<><audio src={url} preload="metadata" onLoadedMetadata={e=>{const d=e.currentTarget.duration;if(Number.isFinite(d)){setDuration(d);update({trim_end:d})}}} onError={()=>setError('Your browser cannot decode this file. Try MP3 or WAV.')} hidden/>
        <CropAudio src={url} start={form.trim_start} end={form.trim_end}/></>}
      <Field label="Track name"><input value={form.name} onChange={e=>update({name:e.target.value})} maxLength={160}/></Field>
      <Field label="Category folder" hint="Lowercase letters, numbers, hyphens or underscores."><input list="cf-music-categories" value={form.category} onChange={e=>update({category:e.target.value.toLowerCase()})}/><datalist id="cf-music-categories">{categories.map(c=><option key={c} value={c}/>)}</datalist></Field>
      <div className="grid cols-2"><Field label="Crop start (seconds)"><Num min={0} max={duration} step={.1} value={form.trim_start} onChange={v=>update({trim_start:v})}/></Field><Field label="Crop end (seconds)"><Num min={0} max={duration} step={.1} value={form.trim_end} onChange={v=>update({trim_end:v})}/></Field></div>
      <Slider label="Default music intensity" value={form.default_volume_pct} suffix="%" onChange={v=>update({default_volume_pct:v})}/>
      <Toggle label="I have permission to use this music in published videos" value={form.rights_cleared} onChange={v=>update({rights_cleared:v})}/>
      <button className="btn primary" disabled={!loaded||busy||!file||!form.name.trim()||!(/^[a-z0-9_-]{1,48}$/).test(form.category)||!form.rights_cleared||!musicCropValid(form.trim_start,form.trim_end,duration)} onClick={upload}>{busy?'Saving…':'Upload to shared library'}</button>
      {file&&!musicCropValid(form.trim_start,form.trim_end,duration)&&<p className="note warn">Choose a crop of at least 3 seconds within the audio.</p>}
      {error&&<p className="note err" role="alert">{error}</p>}{notice&&<p className="note info" role="status">{notice}</p>}
    </section>
    <section className="card"><div className="row between"><h3>Music library</h3><button className="btn small" disabled={busy} onClick={load}>Refresh</button></div>
      <Field label="Category"><Select value={category} onChange={setCategory} options={[{value:'',label:'All categories'},...categories.map(c=>({value:c,label:`${c} (${tracks.filter(t=>t.category===c).length})`}))]}/></Field>
      {!tracks.filter(t=>!category||t.category===category).length&&<p className="muted">No tracks in this category.</p>}
      {tracks.filter(t=>!category||t.category===category).map(t=><article className="cf-track" key={t.id}><div className="row between"><div><strong>{t.name}</strong><p className="muted">{t.category} · {Number(t.duration_seconds||0).toFixed(1)}s</p></div><button className="btn small" disabled={busy} onClick={()=>remove(t)}>Remove</button></div>{safeMediaUrl(t.url||t.path)&&<CropAudio src={safeMediaUrl(t.url||t.path)} start={t.trim_start||0} end={t.trim_end||t.duration_seconds}/>}</article>)}
    </section>
  </div>
}
