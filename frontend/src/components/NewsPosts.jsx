import {useCallback,useEffect,useRef,useState} from 'react'
import {cloudflareRequest,postCloudflare,safeMediaUrl} from '../cloudflareApi'
import {useDraftGuard,usePolling} from '../cloudflareHooks'
import './NewsPosts.css'

const defaults={enabled:false,run_at:'09:00',timezone_offset_minutes:330,posts_per_day:10,fetch_budget:6,channel:'',brand:'',categories:[]}
const statuses={queued:'Queued',planning:'Planning',imaging:'Creating hero image',rendering:'Rendering slides',awaiting_approval:'Awaiting approval',approved:'Approved',publishing:'Publishing',published:'Published',failed:'Failed',uncertain:'Publication uncertain'}
const dateLabel=value=>{const date=new Date(value);return value&&!Number.isNaN(date.getTime())?date.toLocaleString():'Not available'}

function Carousel({post}) {
  const [index,setIndex]=useState(0)
  const slides=post.slides||[],current=Math.min(index,Math.max(0,slides.length-1)),url=safeMediaUrl(slides[current]?.url)
  return <div className="news-carousel">
    {url?<a href={url} target="_blank" rel="noreferrer"><img src={url} alt={`${post.title||'News carousel'} — slide ${current+1}`} loading="lazy"/></a>:<div className="news-placeholder">{slides.length?'Slide preview unavailable':'Slides will appear here when ready.'}</div>}
    {slides.length>0&&<div className="row between news-slide-controls"><button className="btn small" aria-label="Previous slide" disabled={current===0} onClick={()=>setIndex(current-1)}>Previous</button><span aria-live="polite">{current+1} / {slides.length}</span><button className="btn small" aria-label="Next slide" disabled={current===slides.length-1} onClick={()=>setIndex(current+1)}>Next</button></div>}
  </div>
}

export default function NewsPosts({active=true,onDirty}) {
  const [data,setData]=useState(null),[form,setForm]=useState(defaults),[dirty,setDirty]=useState(false)
  const [error,setError]=useState(''),[loadError,setLoadError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState('')
  const [pending,setPending]=useState(null),[publishLocks,setPublishLocks]=useState({})
  const [confirmation,setConfirmation]=useState(null)
  const dirtyRef=useRef(false),sequence=useRef(0),actionLock=useRef(false),pendingRef=useRef(null)
  const load=useCallback(async()=>{
    const request=++sequence.current
    try {
      const result=await cloudflareRequest('/news')
      if(request!==sequence.current)return
      setData(result);setLoadError('')
      if(!dirtyRef.current)setForm({...defaults,...result.config})
    }catch(e){if(request===sequence.current)setLoadError(e.message)}
  },[])
  usePolling(load,10000,active)
  useDraftGuard(dirty||!!pending,active?onDirty:undefined)
  useEffect(()=>()=>{sequence.current++},[active])
  useEffect(()=>{const guard=e=>{if(dirtyRef.current){e.preventDefault();e.returnValue=''}};window.addEventListener('beforeunload',guard);return()=>window.removeEventListener('beforeunload',guard)},[])
  const update=patch=>{setConfirmation(null);dirtyRef.current=true;setDirty(true);setForm(f=>({...f,...patch}))}
  const act=async(key,operation)=>{
    if(actionLock.current)return
    actionLock.current=true;setBusy(key);setError('');setNotice('')
    try{await operation()}catch(e){setError(e.message)}finally{await load();actionLock.current=false;setBusy('')}
  }
  const save=e=>{
    e.preventDefault()
    act('save',async()=>{
      if(form.brand.length>35)throw new Error('Brand must be 35 characters or fewer.')
      await postCloudflare('/news/config',form)
      dirtyRef.current=false;setDirty(false);setNotice('News configuration saved.')
    })
  }
  const connect=()=>{
    if(actionLock.current||!data?.channels?.some(c=>c.slug===form.channel))return
    const channel=form.channel,popup=window.open('about:blank','_blank')
    if(popup)popup.opener=null
    act('connect',async()=>{
      try{
        const result=await postCloudflare(`/channels/${encodeURIComponent(channel)}/connect/meta`)
        const url=new URL(result.authorization_url)
        if(!['https:','http:'].includes(url.protocol))throw new Error('Invalid connection URL')
        if(popup)popup.location.href=url.href
        else window.location.assign(url.href)
        setNotice('Complete Instagram sign-in in the new tab, then refresh news to update the connection.')
      }catch(e){popup?.close();throw e}
    })
  }
  const run=()=>{
    if(!pendingRef.current&&(confirmation?.type!=='generate'||confirmation.channel!==form.channel||dirty))return
    setConfirmation(null)
    act('run',async()=>{
      const request=pendingRef.current||{id:crypto.randomUUID().replaceAll('-',''),channel:form.channel}
      pendingRef.current=request;setPending(request)
      try {
        const result=await postCloudflare('/news/run',{channel:request.channel},{headers:{'Idempotency-Key':request.id}})
        pendingRef.current=null;setPending(null);setNotice(`Generation requested: ${result.id}. Review the carousel when ready.`)
      }catch(e){throw new Error(`${e.message}. Retry uses the same request ID and channel to avoid duplicate generation.`)}
    })
  }
  const publish=post=>{
    if(confirmation?.type!=='publish'||confirmation.id!==post.id||post.status!=='approved'||publishLocks[post.id]||!data?.channels?.some(c=>c.slug===post.channel&&c.instagram_connected))return
    setConfirmation(null)
    act(`publish-${post.id}`,async()=>{
      setPublishLocks(locks=>({...locks,[post.id]:true}))
      try{await postCloudflare(`/news/${encodeURIComponent(post.id)}/publish`,{});setNotice('Instagram publication requested. Check the updated status below.')}
      catch(e){throw new Error(`${e.message}. Publication may have reached Instagram. Verify the account before taking further action; repeat publishing is locked here.`)}
    })
  }
  const channels=data?.channels||[],posts=data?.posts||[]
  return <div className="news-posts">
    <section className="card cf-panel">
      <div className="row between"><div><p className="eyebrow">INSTAGRAM</p><h1 className="page-title">News carousels</h1><p className="muted">Review sourced stories, approve the slides, then publish to Instagram.</p></div><button className="btn" disabled={!!busy} onClick={load}>Refresh news</button></div>
      <p className="note warn">The free news feed has a 12-hour delay. This is a daily news digest, not a live breaking-news service.</p>
      <p className="muted">Each carousel slide gets its own scene and separately generated image. A 1–3 slide post uses 1–3 paid image generations. Layout-only edits reuse each slide’s saved image.</p>
      <p className="muted">Provider secrets are managed on the server and are read-only here. API keys are never entered in this tab.</p>
      <p>Feed requests today: <strong>{data?.usage?.requests_today??'—'}</strong> · Saved daily fetch budget: <strong>{data?.config?.fetch_budget??6}</strong></p>
      {loadError&&<p className="note err" role="alert">News refresh failed: {loadError}. <button className="btn small" onClick={load}>Retry</button></p>}
      {error&&<p className="note err" role="alert">{error}</p>}
      {notice&&<p className="note info" role="status">{notice}</p>}
      {!data&&<p className="muted">{loadError?'News is unavailable until a successful refresh.':'Loading news…'}</p>}
    </section>
    {data&&<>
      <form className="card cf-panel" onSubmit={save}>
        <h2>Daily news configuration</h2>
        <fieldset className="cf-fieldset" disabled={!!busy}>
          <div className="news-config-grid">
            <label>Instagram channel<select required value={form.channel} onChange={e=>update({channel:e.target.value})}><option value="">Select channel</option>{channels.map(c=><option key={c.slug} value={c.slug}>{c.name} · {c.instagram_connected?'Instagram connected':'Instagram not connected'}</option>)}</select></label>
            <label>Brand (max 35 characters)<input maxLength={35} value={form.brand} onChange={e=>update({brand:e.target.value})}/></label>
            <label>Posts per day (10–20)<input required type="number" min="10" max="20" step="1" value={form.posts_per_day} onChange={e=>update({posts_per_day:e.target.value===''?'':Number(e.target.value)})}/></label>
            <label>Daily feed fetch budget (1–200)<input required type="number" min="1" max="200" step="1" value={form.fetch_budget} onChange={e=>update({fetch_budget:e.target.value===''?'':Number(e.target.value)})}/></label>
            <label>Daily run time<input required type="time" value={form.run_at} onChange={e=>update({run_at:e.target.value})}/></label>
            <label>UTC offset in minutes<input required type="number" min="-720" max="840" step="1" value={form.timezone_offset_minutes} onChange={e=>update({timezone_offset_minutes:e.target.value===''?'':Number(e.target.value)})}/><span className="muted">Positive is east of UTC; India is +330. Fixed offset, no daylight-saving adjustment.</span></label>
          </div>
          <p><button className="btn" type="button" disabled={!channels.some(c=>c.slug===form.channel)} onClick={connect}>{busy==='connect'?'Connecting…':'Connect Instagram'}</button></p>
          <label className="news-categories">Categories (comma separated)<input value={(form.categories||[]).join(',')} onChange={e=>update({categories:e.target.value.split(',')})} onBlur={()=>update({categories:(form.categories||[]).map(c=>c.trim()).filter(Boolean)})}/></label>
          <label className="news-toggle"><input type="checkbox" checked={form.enabled} onChange={e=>update({enabled:e.target.checked})}/> Enable daily news schedule</label>
          <p className="muted">Scheduling is disabled by default. Publishing requires approval and a separate manual Instagram confirmation.</p>
          <button className="btn primary" type="submit" disabled={!dirty}>{busy==='save'?'Saving…':'Save configuration'}</button>
          {dirty&&<span className="muted news-unsaved">Unsaved changes</span>}
        </fieldset>
      </form>
      <section className="card cf-panel"><h2>Prepare news</h2><div className="row news-actions">
        <button className="btn" disabled={!!busy} onClick={()=>act('fetch',async()=>{await postCloudflare('/news/fetch',{});setNotice('News feed fetched and cached.')} )}>{busy==='fetch'?'Fetching…':'Fetch & cache news'}</button>
        <button className="btn primary" disabled={!!busy||(!pending&&(dirty||!channels.some(c=>c.slug===form.channel)))} onClick={()=>pending?run():setConfirmation({type:'generate',channel:form.channel})}>{busy==='run'?'Requesting…':pending?'Retry same generation':'Generate one news post'}</button>
      </div>{confirmation?.type==='generate'&&<div className="note warn" role="status"><p>Generate one Instagram news carousel for {confirmation.channel}? Each of its 1–3 slides uses a separate paid image generation.</p><div className="row news-actions"><button className="btn primary" disabled={!!busy||dirty||confirmation.channel!==form.channel} onClick={run}>Confirm & generate</button><button className="btn" disabled={!!busy} onClick={()=>setConfirmation(null)}>Cancel generation</button></div></div>}{dirty&&<p className="muted">Save configuration before starting a new generation.</p>}{pending&&<p className="note warn">Unresolved generation for {pending.channel}. Retry reuses its original request ID.</p>}</section>
      <section aria-label="News posts" className="news-post-grid">
        {!posts.length&&<div className="card"><h2>No news posts yet</h2><p className="muted">Save your configuration, cache the feed, and generate a carousel to review.</p></div>}
        {posts.map(post=>{const connected=channels.some(c=>c.slug===post.channel&&c.instagram_connected),sourceUrl=safeMediaUrl(post.source?.url);return <article className="card news-post" key={post.id}>
          <div className="row between"><span className={`news-status news-status-${post.status}`} role="status">{statuses[post.status]||post.status}</span><span className="muted">{post.channel}</span></div>
          <h2>{post.title||'Untitled news post'}</h2><p className="muted">Created {dateLabel(post.created_at)}</p>
          <Carousel post={post}/>
          <h3>Instagram caption</h3><p className="news-caption">{post.caption||'Caption is not ready yet.'}</p>
          <div className="news-source"><h3>Source</h3>{sourceUrl?<a href={sourceUrl} target="_blank" rel="noreferrer">{post.source?.title||sourceUrl}</a>:<p>{post.source?.title||'Source is not available yet.'}</p>}<p className="muted">{post.source?.source||'Unknown publisher'} · {dateLabel(post.source?.published_at)}</p></div>
          {post.error&&<p className="note err" role="alert">{post.error}</p>}
          {post.status==='uncertain'&&<p className="note warn">Publication outcome is uncertain. Check Instagram before attempting any recovery.</p>}
          {publishLocks[post.id]&&!['published','publishing','uncertain'].includes(post.status)&&<p className="note warn">A publication request was submitted. Repeat publishing is locked here; verify Instagram and refresh status.</p>}
          <div className="row news-actions">
            {post.status==='awaiting_approval'&&<button className="btn" disabled={!!busy} onClick={()=>act(`approve-${post.id}`,async()=>{await postCloudflare(`/news/${encodeURIComponent(post.id)}/approve`,{revision:1});setNotice('Carousel approved. Publish separately after confirming Instagram.')} )}>Approve carousel</button>}
            {post.status==='approved'&&<button className="btn primary" disabled={!!busy||!connected||!!publishLocks[post.id]} onClick={()=>setConfirmation({type:'publish',id:post.id})}>Publish to Instagram…</button>}
          </div>
          {confirmation?.type==='publish'&&confirmation.id===post.id&&post.status==='approved'&&!publishLocks[post.id]&&<div className="note warn" role="status"><p>Publish “{post.title||post.id}” to Instagram for {post.channel}? This makes the approved carousel public.</p><div className="row news-actions"><button className="btn primary" disabled={!!busy||!connected} onClick={()=>publish(post)}>Confirm & publish to Instagram</button><button className="btn" disabled={!!busy} onClick={()=>setConfirmation(null)}>Cancel publishing</button></div></div>}
          {post.status==='approved'&&!connected&&<p className="note warn">Select this channel above and connect Instagram before publishing.</p>}
        </article>})}
      </section>
    </>}
  </div>
}
