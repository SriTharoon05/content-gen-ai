import {useEffect,useState} from 'react'
import {cloudflareRequest,postCloudflare} from '../cloudflareApi'
import {Field,Select,Toggle,Chips} from './ui'

function ChannelForm({channel,onSaved,onCancel}) {
  const creating=!channel
  const values=()=>({slug:channel?.slug||'',name:channel?.name||'',niche:channel?.niche||'',tagline:channel?.tagline||'',instructions:channel?.instructions||'',enabled:channel?.enabled??true,conversation:!!channel?.strategy?.conversation,topic_seeds:(channel?.strategy?.topic_seeds||[]).join('\n'),overrides:{...channel?.overrides}})
  const [form,setForm]=useState(values),[editing,setEditing]=useState(creating),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('')
  const update=patch=>setForm(f=>({...f,...patch})),override=patch=>setForm(f=>({...f,overrides:{...f.overrides,...patch}}))
  const save=async()=>{
    setBusy(true);setError('');setNotice('')
    try{
      const {slug,topic_seeds,...rest}=form
      const payload={...rest,topic_seeds:topic_seeds.split('\n').map(x=>x.trim()).filter(Boolean)}
      if(creating)await postCloudflare('/channels',{slug,...payload})
      else await cloudflareRequest(`/channels/${channel.slug}`,{method:'PUT',body:JSON.stringify(payload)})
      await onSaved();setEditing(false);setNotice('Channel saved. New runs use this direction.')
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
      <div className="grid cols-2"><Field label="Default publishing mode"><Select disabled={!editing} value={form.overrides.publishing_mode||'settings'} onChange={v=>override({publishing_mode:v})} options={[{value:'settings',label:'Use global settings'},{value:'review',label:'Review before publishing'},{value:'direct',label:'Publish directly'}]}/></Field><Field label="Primary language"><Select disabled={!editing} value={form.overrides.primary_language||'en'} onChange={v=>override({primary_language:v})} options={[{value:'en',label:'English'},...((form.overrides.primary_language&&form.overrides.primary_language!=='en')?[{value:form.overrides.primary_language,label:`${form.overrides.primary_language} · Render only`}]:[])]}/></Field></div>
      {editing&&<Field label="Default destinations" hint="Empty uses global publishing destinations."><Chips value={form.overrides.publish_platforms||[]} onChange={v=>override({publish_platforms:v})} options={[{value:'youtube',label:'YouTube'},{value:'instagram',label:'Instagram'}]}/></Field>}
      <Toggle label="Channel enabled" disabled={!editing} value={form.enabled} onChange={v=>update({enabled:v})}/>
      {form.conversation&&<p className="note warn">This channel uses two speakers. Its saved format is preserved; Cloudflare generation must support it before a run can start.</p>}
      {channel?.supported===false&&<p className="note warn">{channel.unsupported_reason||'This channel’s format is not supported by the current Cloudflare generation path.'}</p>}
    </fieldset></div><div className="channel-profile-footer"><div className="row">
      {editing?<><button className="btn primary" disabled={busy||!form.name.trim()||!form.niche.trim()||(creating&&!/^[a-z][a-z0-9-]{1,63}$/.test(form.slug))} onClick={save}>{busy?'Saving…':creating?'Create channel':'Save channel'}</button><button className="btn" disabled={busy} onClick={()=>{if(creating)onCancel();else{setForm(values());setEditing(false)}}}>Cancel</button></>:<><button className="btn" disabled={busy} onClick={()=>connect('google')}>Connect YouTube</button><button className="btn" disabled={busy} onClick={()=>connect('meta')}>Connect Instagram</button></>}
    </div>{error&&<p className="note err" role="alert">{error}</p>}{notice&&<p className="note info" role="status">{notice}</p>}</div>
  </section>
}

export default function CloudflareChannels({channels,onUpdated}) {
  const [selected,setSelected]=useState(channels[0]?.slug||''),[creating,setCreating]=useState(false),[connections,setConnections]=useState(null),[error,setError]=useState('')
  const load=async()=>{try{setConnections(await cloudflareRequest('/connections'));setError('')}catch(e){setError(e.message)}}
  useEffect(()=>{load()},[])
  const channel=channels.find(c=>c.slug===selected)
  const rows=connections?.channels||connections?.connections||[]
  const connection=rows.find(c=>(c.channel||c.slug||c.channel_slug)===selected)
  return <><div className="row between cf-toolbar"><Field label="Channel"><Select value={selected} onChange={v=>{setSelected(v);setCreating(false)}} options={channels.map(c=>({value:c.slug,label:c.name}))}/></Field><button className="btn primary" onClick={()=>setCreating(true)}>Create new channel</button></div>
    {creating?<ChannelForm onCancel={()=>setCreating(false)} onSaved={async()=>{await onUpdated();setCreating(false)}}/>:channel&&<ChannelForm key={channel.slug+channel.updated_at} channel={channel} onSaved={onUpdated}/>}
    {!creating&&<section className="card cf-panel"><div className="row between"><h3>Connected accounts</h3><button className="btn small" onClick={load}>Refresh accounts</button></div><div className="grid cols-2">{['youtube','instagram'].map(p=><div key={p}><strong>{p==='youtube'?'YouTube':'Instagram'}</strong><p>{connection?.[p]?(p==='youtube'?connection.youtube_channel_name:connection.instagram_account_name)||'Connected':'Not connected'}</p></div>)}</div>{error&&<p className="note err" role="alert">{error}</p>}</section>}
  </>
}
