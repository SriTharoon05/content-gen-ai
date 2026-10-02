import {useCallback,useEffect,useState} from 'react'
import {cloudflareRequest,postCloudflare} from '../cloudflareApi'
import {Field,Select,Toggle,Chips} from './ui'
import {languageOptions,geminiVoiceOptions} from '../cloudflareDashboard'
import {useDraftGuard} from '../cloudflareHooks'
import CloudflareAnalytics from './CloudflareAnalytics'

function ChannelForm({channel,onSaved,onCancel,onDirty,connections}) {
  const creating=!channel
  const values=()=>({slug:channel?.slug||'',name:channel?.name||'',niche:channel?.niche||'',tagline:channel?.tagline||'',instructions:channel?.instructions||'',enabled:channel?.enabled??true,conversation:!!channel?.strategy?.conversation,voice_name:channel?.strategy?.voice_name||'',topic_seeds:(channel?.strategy?.topic_seeds||[]).join('\n'),overrides:{...channel?.overrides}})
  const [form,setForm]=useState(values),[editing,setEditing]=useState(creating),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('')
  useDraftGuard(editing&&JSON.stringify(form)!==JSON.stringify(values()),onDirty)
  const update=patch=>setForm(f=>({...f,...patch})),override=patch=>setForm(f=>({...f,overrides:{...f.overrides,...patch}}))
  const save=async()=>{
    setBusy(true);setError('');setNotice('')
    try{
      const {slug,topic_seeds,...rest}=form
      const payload={...rest,topic_seeds:topic_seeds.split('\n').map(x=>x.trim()).filter(Boolean)}
      const result=creating?await postCloudflare('/channels',{slug,...payload}):await cloudflareRequest(`/channels/${channel.slug}`,{method:'PUT',body:JSON.stringify(payload)})
      await onSaved(result.channel?.slug||slug);setEditing(false);setNotice('Channel saved. New runs use this direction.')
    }catch(e){setError(e.message)}finally{setBusy(false)}
  }
  const connect=async provider=>{
    const popup=window.open('about:blank','_blank');if(popup)popup.opener=null
    setBusy(true);setError('')
    try{const d=await postCloudflare(`/channels/${channel.slug}/connect/${provider}`);const url=new URL(d.authorization_url||d.url);if(!['https:','http:'].includes(url.protocol))throw Error('Invalid connection URL');if(popup)popup.location.href=url.href;else window.location.assign(url.href);setNotice('Complete sign-in in the new tab, then refresh accounts below.')}
    catch(e){popup?.close();setError(e.message)}finally{setBusy(false)}
  }
  return <section className="channel-profile cf-panel"><div className="channel-profile-head"><div><p className="eyebrow">CREATIVE DIRECTION</p><h3>{creating?'Create channel':channel.name}</h3><p className="channel-profile-description">{editing?'Save your niche and instructions for future content.':'Saved instructions are read-only until you choose Edit.'}</p></div>{!editing&&<button className="btn" onClick={()=>{setForm(values());setEditing(true)}}>Edit channel</button>}</div>
    <div className="channel-profile-body"><fieldset disabled={busy} className="cf-fieldset">
      {creating&&<Field label="Channel ID" hint="Lowercase letters, numbers and hyphens; permanent once created."><input value={form.slug} onChange={e=>update({slug:e.target.value})} pattern="[a-z][a-z0-9-]{1,63}"/></Field>}
      <div className="grid cols-2"><Field label="Name"><input readOnly={!editing} value={form.name} onChange={e=>update({name:e.target.value})}/></Field><Field label="Tagline"><input readOnly={!editing} value={form.tagline} onChange={e=>update({tagline:e.target.value})}/></Field></div>
      <Field label="Niche"><textarea readOnly={!editing} value={form.niche} onChange={e=>update({niche:e.target.value})}/></Field>
      <div className="channel-direction-grid"><div className="channel-direction-panel"><Field label="Agent instructions"><textarea readOnly={!editing} value={form.instructions} onChange={e=>update({instructions:e.target.value})} placeholder="Audience, natural delivery, hooks, story style, visuals, and things to avoid…"/></Field></div><div className="channel-direction-panel"><Field label="Optional topic starting points" hint="One per line. Leave blank to let generation choose ideas within your niche."><textarea readOnly={!editing} value={form.topic_seeds} onChange={e=>update({topic_seeds:e.target.value})}/></Field></div></div>
      <div className="grid cols-2"><Field label="Default publishing mode"><Select disabled={!editing} value={form.overrides.publishing_mode||'settings'} onChange={v=>override({publishing_mode:v})} options={[{value:'settings',label:'Use global settings'},{value:'review',label:'Review before publishing'},{value:'direct',label:'Publish directly'}]}/></Field><Field label="Primary language" hint="One narration language per run. Captions remain English. Other languages require Gemini TTS; Groq speech fallback supports English and Arabic."><Select disabled={!editing} value={form.overrides.primary_language||''} onChange={v=>override({primary_language:v||undefined})} options={[{value:'',label:'Use global default'},...languageOptions]}/></Field></div>
      <Field label="Gemini narration voice" hint="An explicit channel voice overrides the global default for new runs."><Select disabled={!editing} value={form.overrides.voice||form.voice_name} onChange={v=>{update({voice_name:v});override({voice:v})}} options={[{value:'',label:'Use global default'},...geminiVoiceOptions]}/></Field>
      {editing&&<Field label="Default destinations" hint="Empty uses global publishing destinations."><Chips value={form.overrides.publish_platforms||[]} onChange={v=>override({publish_platforms:v})} options={[{value:'youtube',label:'YouTube'},{value:'instagram',label:'Instagram'}]}/></Field>}
      <Toggle label="Channel enabled" disabled={!editing} value={form.enabled} onChange={v=>update({enabled:v})}/>
      <Toggle label="Two-speaker conversation" disabled={!editing} value={form.conversation} onChange={v=>update({conversation:v})} hint="Alex and Sam use the existing two-speaker generation path. Off uses one narrator."/>
      {form.overrides.languages?.length>0&&<p className="note warn">Additional-language variants are not supported by Cloudflare. {editing&&<button className="btn small" onClick={()=>override({languages:[]})}>Use primary language only</button>}</p>}
      {channel?.supported===false&&<p className="note warn">{channel.unsupported_reason||'This channel’s format is not supported by the current Cloudflare generation path.'}</p>}
    </fieldset></div><div className="channel-profile-footer"><div className="row">
      {editing?<><button className="btn primary" disabled={busy||!form.name.trim()||!form.niche.trim()||(creating&&!/^[a-z][a-z0-9-]{1,63}$/.test(form.slug))} onClick={save}>{busy?'Saving…':creating?'Create channel':'Save channel'}</button><button className="btn" disabled={busy} onClick={()=>{if(creating)onCancel();else{setForm(values());setEditing(false)}}}>Cancel</button></>:<><button className="btn" disabled={busy||!connections?.google_configured} onClick={()=>connect('google')}>Connect YouTube</button><button className="btn" disabled={busy||!connections?.meta_configured} onClick={()=>connect('meta')}>Connect Instagram</button></>}
    </div>{error&&<p className="note err" role="alert">{error}</p>}{notice&&<p className="note info" role="status">{notice}</p>}</div>
  </section>
}

export default function CloudflareChannels({channels,onUpdated,onDirty}) {
  const [selected,setSelected]=useState(channels[0]?.slug||''),[creating,setCreating]=useState(false),[connections,setConnections]=useState(null),[error,setError]=useState('')
  const [draftDirty,setDraftDirty]=useState(false)
  const markDirty=useCallback(value=>{setDraftDirty(value);onDirty?.(value)},[onDirty])
  const leave=action=>{if(draftDirty&&!window.confirm('Discard unsaved channel changes?'))return;action()}
  const load=async()=>{try{setConnections(await cloudflareRequest('/connections'));setError('')}catch(e){setError(e.message)}}
  useEffect(()=>{load()},[])
  useEffect(()=>{if(!channels.some(c=>c.slug===selected)&&channels.length)setSelected(channels[0].slug)},[channels,selected])
  const channel=channels.find(c=>c.slug===selected)
  const rows=connections?.channels||connections?.connections||[]
  const connection=rows.find(c=>(c.channel||c.slug||c.channel_slug)===selected)
  return <><div className="row between cf-toolbar"><Field label="Channel"><Select value={selected} onChange={v=>leave(()=>{setSelected(v);setCreating(false)})} options={channels.map(c=>({value:c.slug,label:c.name}))}/></Field><button className="btn primary" onClick={()=>leave(()=>setCreating(true))}>Create new channel</button></div>
    {creating?<ChannelForm key="new-channel" onDirty={markDirty} onCancel={()=>setCreating(false)} onSaved={async slug=>{await onUpdated();setSelected(slug);setCreating(false)}}/>:channel&&<ChannelForm key={channel.slug} channel={channel} connections={connections} onDirty={markDirty} onSaved={onUpdated}/>}
    {!creating&&<section className="card cf-panel"><div className="row between"><h3>Connected accounts</h3><button className="btn small" onClick={load}>Refresh accounts</button></div><div className="grid cols-2">{['youtube','instagram'].map(p=><div key={p}><strong>{p==='youtube'?'YouTube':'Instagram'}</strong><p>{connection?.[p]?(p==='youtube'?connection.youtube_channel_name:connection.instagram_account_name)||'Connected':'Not connected'}</p></div>)}</div>{connections&&!connections.google_configured&&<p className="note info">YouTube connection requires GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET on Cloudflare.</p>}{connections&&!connections.meta_configured&&<p className="note info">Instagram connection requires META_APP_ID and META_APP_SECRET on Cloudflare.</p>}{error&&<p className="note err" role="alert">{error}</p>}</section>}
    {!creating&&channel&&<CloudflareAnalytics key={channel.slug} channel={channel} connection={connection}/>}
  </>
}
