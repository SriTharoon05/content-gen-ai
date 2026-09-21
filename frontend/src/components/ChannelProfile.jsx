import { useEffect, useState } from 'react'
import { api } from '../api'
import { useStore } from '../store'
import { Field, Text, Toggle } from './ui'

export default function ChannelProfile({channel,onCreated,onEditing}) {
  const {notify,refreshChannels}=useStore()
  const creating=!channel
  const values=()=>({slug:channel?.slug||'',name:channel?.name||'',tagline:channel?.tagline||'',niche:channel?.niche||'',
    instructions:channel?.instructions||'',topics:(channel?.strategy?.topic_seeds||[]).join('\n'),conversation:!!channel?.strategy?.conversation})
  const [editing,setEditing]=useState(creating),[form,setForm]=useState(values),[busy,setBusy]=useState(false)
  useEffect(()=>{onEditing?.(editing);return ()=>onEditing?.(false)},[editing,onEditing])
  const displayed=editing?form:values()
  const change=(k,v)=>setForm(f=>({...f,[k]:v}))
  const save=async()=>{
    setBusy(true)
    try{
      const {topics,slug,...fields}=form
      const body={...fields,topic_seeds:topics.split('\n').map(s=>s.trim()).filter(Boolean)}
      if(creating)await api.createChannel({slug,...body});else await api.patchChannel(channel.slug,body)
      await refreshChannels();setEditing(false);notify(creating?'Channel created. Connect your publishing accounts next.':'Channel instructions saved');onCreated?.()
    }catch(e){notify(e.message,'error')}finally{setBusy(false)}
  }
  const connect=async(platform)=>{
    const popup=window.open('about:blank','_blank');if(popup)popup.opener=null
    try{const r=await (platform==='instagram'?api.connectMeta(channel.slug):api.connectYoutube(channel.slug));if(popup)popup.location.href=r.url;else window.location.assign(r.url)}
    catch(e){popup?.close();notify(e.message,'error')}
  }
  return <section className={`channel-profile ${editing?'is-editing':''}`}>
    <div className="channel-profile-head">
      <div><p className="eyebrow">CREATIVE DIRECTION</p><h3>{creating?'Create a channel':'Channel profile & agent instructions'}</h3>
        <p className="channel-profile-description">{editing?'Shape your channel’s identity, voice and next stories.':'Your saved direction for every new story. Choose Edit channel to make changes.'}</p></div>
      <div className="row"><span className={`pill ${editing?'busy':''}`}>{editing?'Editing':'Read only'}</span>
        {!editing && <button className="btn" onClick={()=>{setForm(values());setEditing(true)}}>Edit channel</button>}</div>
    </div>
    <div className="channel-profile-body">
    {creating && <Field label="Unique channel ID" hint="Lowercase letters, numbers and hyphens; cannot change later"><Text value={form.slug} onChange={v=>change('slug',v)}/></Field>}
    <div className="grid cols-2">
      {['name','tagline'].map(k=><Field key={k} label={k[0].toUpperCase()+k.slice(1)}>
        <input type="text" readOnly={!editing} value={displayed[k]} onChange={e=>change(k,e.target.value)}/></Field>)}
    </div>
    <Field label="Channel niche" hint="The topic or category your channel explores."><input type="text" readOnly={!editing} value={displayed.niche} onChange={e=>change('niche',e.target.value)}/></Field>
    <div className="channel-direction-grid">
    <div className="channel-direction-panel">
    <Field label="Agent instructions" hint="Your saved creative direction is used for ideas, scripts, voice, visuals and publishing copy. Generation limits and safety rules still apply.">
      <textarea readOnly={!editing} value={displayed.instructions} onChange={e=>change('instructions',e.target.value)} placeholder="Define tone, storytelling, audience, visuals, voice performance and things to avoid…"/>
    </Field>
    </div>
    <div className="channel-direction-panel">
    <Field label="Topic ideas — one per line" hint="A reusable starting point for future stories."><textarea readOnly={!editing} value={displayed.topics} onChange={e=>change('topics',e.target.value)} placeholder="Add the topics you want to explore…"/></Field>
    </div>
    </div>
    <div className="channel-format">
    {editing?<Toggle label="Two-speaker conversation (Alex & Sam)" value={form.conversation} onChange={v=>change('conversation',v)}/>:<p className="dim tiny">Format: {displayed.conversation?'Two-speaker conversation':'Single-narrator storytelling'}</p>}
    </div>
    </div>
    <div className="channel-profile-footer"><div className="row">{editing && <><button className="btn primary" disabled={busy || !form.name.trim() || !form.niche.trim()} onClick={save}>{busy?'Saving…':creating?'Create channel':'Save channel'}</button>
      {!creating && <button className="btn" disabled={busy} onClick={()=>setEditing(false)}>Cancel edits</button>}</>}
      {!creating && <><button className="btn" disabled={editing} onClick={()=>connect('youtube')}>Connect YouTube</button><button className="btn" disabled={editing} onClick={()=>connect('instagram')}>Connect Instagram</button></>}
    </div>
    {editing && <p className="dim tiny">Save this channel before starting a run. Changes affect subsequent generation, not existing rendered videos.</p>}
    </div>
  </section>
}
