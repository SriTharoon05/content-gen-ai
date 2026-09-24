import {useEffect,useState} from 'react'
import {cloudflareRequest} from '../cloudflareApi'
import {Field,Select,Slider,Toggle,Num,Chips} from './ui'

export default function CloudflareSettings() {
  const [saved,setSaved]=useState(null),[draft,setDraft]=useState(null),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false),[keys,setKeys]=useState({})
  const [tracks,setTracks]=useState([])
  const load=async()=>{try{const r=await cloudflareRequest('/settings');setSaved(r.settings);setDraft(r.settings);setError('')}catch(e){setError(e.message)}}
  useEffect(()=>{load();cloudflareRequest('/music').then(r=>setTracks(r.tracks||[])).catch(e=>setError(`Music defaults: ${e.message}`))},[])
  const set=(section,key,value)=>{setDraft(d=>({...d,[section]:{...d[section],[key]:value}}));setNotice('')}
  const save=async()=>{
    setBusy(true);setError('');setNotice('')
    try{
      const patch={}
      for(const section of ['models','voice','video','music','publishing','runtime','pricing','align'])if(JSON.stringify(draft[section])!==JSON.stringify(saved[section]))patch[section]=draft[section]
      if(Object.keys(keys).length)patch.keys=Object.fromEntries(Object.entries(keys).map(([k,v])=>[k,k==='gemini_audio_paid'?v.trim():v.split(/[\n,]/).map(x=>x.trim()).filter(Boolean)]))
      const result=await cloudflareRequest('/settings',{method:'PUT',body:JSON.stringify({settings:patch})})
      setSaved(result.settings);setDraft(result.settings);setKeys({});setNotice('Settings saved. Existing runs keep their saved inputs; new runs use these defaults.')
    }catch(e){setError(e.message)}finally{setBusy(false)}
  }
  const d=draft
  return <>{error&&<p className="note err" role="alert">{error}</p>}{notice&&<p className="note info" role="status">{notice}</p>}
    {!d?<button className="btn" onClick={load}>Load settings</button>:<><div className="row between cf-toolbar"><p className="muted">Shared database settings. Scheduling is managed separately with its backend ownership guard.</p><button className="btn primary" disabled={busy||(JSON.stringify(saved)===JSON.stringify(draft)&&!Object.keys(keys).length)} onClick={save}>{busy?'Saving…':'Save settings'}</button><button className="btn" disabled={busy} onClick={()=>{setDraft(saved);setKeys({})}}>Discard edits</button></div>
    <fieldset disabled={busy} className="cf-fieldset"><div className="grid cols-2 cf-panels">
      <section className="card"><h3>Content & images</h3><p>Gemini 3.1 Flash-Lite → Groq text fallback. Individual keys rotate independently.</p>
        <Field label="Image model"><Select value={d.models.image_model} onChange={v=>set('models','image_model',v)} options={(d.models.image_catalog||[]).map(m=>({value:m.id,label:`${m.label||m.id} · ${m.credits} credits${m.rpm?` · ${m.rpm} RPM`:''}`}))}/></Field>
        <Field label="Thinking level"><Select value={d.models.thinking} onChange={v=>set('models','thinking',v)} options={['low','medium','high'].map(value=>({value,label:value}))}/></Field>
        <Toggle label="Allow paid text fallback" value={d.models.allow_paid_text_fallback} onChange={v=>set('models','allow_paid_text_fallback',v)}/>
        <p className="note info">Images and scripts are generated only for new content. Editing reuses saved assets.</p>
      </section>
      <section className="card"><h3>Narration & captions</h3>
        <Field label="Audio model"><Select value={d.models.tts} onChange={v=>set('models','tts',v)} options={['gemini-2.5-flash-preview-tts','gemini-3.1-flash-tts-preview','gemini-2.5-pro-preview-tts','canopylabs/orpheus-v1-english','canopylabs/orpheus-arabic-saudi'].map(value=>({value,label:value}))}/></Field>
        <Toggle label="Free Gemini TTS first, then Groq" value={d.voice.free_tts_first} onChange={v=>set('voice','free_tts_first',v)}/>
        <Field label="Gemini default voice"><input value={d.voice.default_voice||''} onChange={e=>set('voice','default_voice',e.target.value)}/></Field>
        <Field label="Groq English voice"><Select value={d.voice.groq_voice||'troy'} onChange={v=>set('voice','groq_voice',v)} options={['troy','austin','daniel','autumn','diana','hannah'].map(value=>({value,label:value}))}/></Field>
        <Slider label="Default speech tempo" min={.75} max={1.6} step={.01} value={d.voice.speech_tempo} suffix="×" onChange={v=>set('voice','speech_tempo',v)}/>
        <Field label="Performance direction"><textarea value={d.voice.pace_note||''} onChange={e=>set('voice','pace_note',e.target.value)}/></Field>
        <Field label="Caption alignment"><Select value={d.align.groq_model} onChange={v=>set('align','groq_model',v)} options={['whisper-large-v3','whisper-large-v3-turbo'].map(value=>({value,label:value}))}/></Field>
      </section>
      <section className="card"><h3>Publishing defaults</h3>
        <Toggle label="Require review before publishing" value={d.publishing.review_before_upload} onChange={v=>set('publishing','review_before_upload',v)}/>
        <Field label="Destinations"><Chips value={d.publishing.platforms||[]} onChange={v=>set('publishing','platforms',v)} options={[{value:'youtube',label:'YouTube'},{value:'instagram',label:'Instagram'}]}/></Field>
        <Field label="YouTube visibility"><Select value={d.publishing.youtube_privacy} onChange={v=>set('publishing','youtube_privacy',v)} options={['private','unlisted','public'].map(value=>({value,label:value}))}/></Field>
        <p className="note warn">Direct mode can publish new videos without further confirmation. These settings do not retroactively publish waiting videos.</p>
      </section>
      <section className="card"><h3>Music & budget</h3>
        <Field label="Default background track"><Select value={d.music.default_track||''} onChange={v=>set('music','default_track',v)} options={[{value:'',label:'No fixed track'},...(d.music.default_track&&!tracks.some(t=>t.id===d.music.default_track)?[{value:d.music.default_track,label:'Saved default · currently unavailable'}]:[]),...tracks.map(t=>({value:t.id,label:`${t.category} · ${t.name}`,disabled:!t.sha256||t.rights_cleared===false}))]}/></Field>
        <Toggle label="Use background music by default" value={d.music.enabled} onChange={v=>set('music','enabled',v)}/>
        <Toggle label="Duck music under narration" value={d.music.ducking} onChange={v=>set('music','ducking',v)}/>
        <Slider label="Default music intensity" value={d.music.default_volume_pct} suffix="%" onChange={v=>set('music','default_volume_pct',v)}/>
        <Field label="Mix ceiling" hint="Relative to narration at 100% intensity."><Num min={.001} max={.5} step={.01} value={d.music.max_intensity} onChange={v=>set('music','max_intensity',v)}/></Field>
        <Field label="Available image credits"><Num min={0} step={.001} value={d.pricing.credit_balance} onChange={v=>set('pricing','credit_balance',v)}/></Field>
      </section>
      <section className="card span-2"><h3>Provider credentials</h3><p className="muted">Existing secrets are never returned. Replace a pool only if needed. A blank replacement clears it; Cancel replacement preserves saved credentials.</p>
        <div className="grid cols-2">{[['gemini_free','Gemini free keys'],['groq','Groq text & caption keys'],['pollinations','Image generation keys'],['gemini_audio_paid','Paid Gemini audio/text key']].map(([key,label])=><div key={key} className="cf-key"><div className="row between"><strong>{label}</strong><span>{Array.isArray(saved.keys?.[key])?saved.keys[key].length:saved.keys?.[key]?1:0} configured</span></div>{Object.hasOwn(keys,key)?<><Field label={`Replace ${label}`} hint={key==='gemini_audio_paid'?'One key.':'One key per line or separated by commas.'}><textarea autoComplete="off" spellCheck={false} value={keys[key]} onChange={e=>setKeys(k=>({...k,[key]:e.target.value}))}/></Field><button className="btn small" onClick={()=>setKeys(k=>{const next={...k};delete next[key];return next})}>Cancel replacement</button></>:<button className="btn small" onClick={()=>setKeys(k=>({...k,[key]:''}))}>Replace credentials</button>}</div>)}</div>
      </section>
    </div></fieldset></>}
  </>
}
