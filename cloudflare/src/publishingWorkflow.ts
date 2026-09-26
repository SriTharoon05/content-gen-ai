import {WorkflowEntrypoint,type WorkflowEvent,type WorkflowStep} from 'cloudflare:workers';
import {publishRpc,accessToken,checked,socialFetch as fetch,type PublishingEnv} from './oauth';
import {trustedMedia,uploadUrl,publicationCopy,CHUNK_BYTES} from './publishing';
const terminal=new Set(['published','uncertain','failed','blocked','remote_failed']);
async function update(env:PublishingEnv,id:string,payload:Record<string,any>){await publishRpc(env,'update',id,payload);}
// One bounded I/O unit. Credentials and resumable URLs never enter Workflow history.
export async function publishUnit(env:PublishingEnv,id:string):Promise<{status:string;wait?:boolean}> {
 const loaded=await publishRpc(env,'claim',id),p=loaded.publication,v=loaded.video;
 if(terminal.has(p.status))return {status:p.status};
 if(p.status==='committing') {await update(env,id,{status:'uncertain',error:'Publish response interrupted; inspect remote account before retry'});return {status:'uncertain'};}
 const platform=p.platform as 'youtube'|'instagram';
 const media=trustedMedia(env,v.output);
 const c=await accessToken(env,v.channel,platform),headers={Authorization:`Bearer ${c.token}`};
 const copy=publicationCopy(v);
 if(!p.session_url) {
  if(!loaded.fresh){await update(env,id,{status:'uncertain',error:'Upload initialization interrupted; reconcile before retry'});return {status:'uncertain'};}
  if(platform==='youtube') {
   const head=await fetch(media,{method:'HEAD'}),size=Number(head.headers.get('Content-Length'));
   if(!head.ok||!Number.isSafeInteger(size)||size<=0)throw new Error('Media size unavailable');
   const response=await fetch('https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status',{method:'POST',headers:{...headers,'Content-Type':'application/json','X-Upload-Content-Length':String(size),'X-Upload-Content-Type':'video/mp4'},body:JSON.stringify({snippet:{title:copy.title,description:copy.description,tags:copy.tags,categoryId:'27'},status:{privacyStatus:p.requested_privacy,selfDeclaredMadeForKids:!!v.made_for_kids}})});
   if(!response.ok){await checked(response);}
   const session=uploadUrl(response.headers.get('Location')||'');await response.body?.cancel();
   await update(env,id,{session_url:session});
  } else {
   const created=await checked(await fetch(`https://graph.instagram.com/${env.META_API_VERSION||'v25.0'}/${c.account}/media`,{method:'POST',headers,body:new URLSearchParams({media_type:'REELS',video_url:media,caption:copy.caption,share_to_feed:'true'})}));
   if(!/^\d+$/.test(String(created.id)))throw new Error('Missing Instagram container');
   await update(env,id,{session_url:String(created.id)});
  }
  return {status:'uploading'};
 }
 if(platform==='youtube') {
  const session=uploadUrl(p.session_url);
  const head=await fetch(media,{method:'HEAD'}),size=Number(head.headers.get('Content-Length'));
  if(!head.ok||!Number.isSafeInteger(size)||size<=0)throw new Error('Media size unavailable');
  // Probe first on every replay. An acknowledged final PUT is never uploaded twice.
  const probe=await fetch(session,{method:'PUT',headers:{...headers,'Content-Length':'0','Content-Range':`bytes */${size}`},redirect:'manual'});
  if(probe.status===200||probe.status===201){const result=await checked(probe);if(!result.id)throw new Error('Missing YouTube video ID');await update(env,id,{status:'published',remote_id:result.id,error:'',actual_privacy:result.status?.privacyStatus||''});return {status:'published'};}
  if(probe.status!==308){await probe.body?.cancel();await update(env,id,{status:'uncertain',error:'YouTube resumable session unavailable; inspect account before retry'});return {status:'uncertain'};}
  const acknowledged=probe.headers.get('Range');await probe.body?.cancel();
  if(acknowledged&&!/^bytes=0-\d+$/.test(acknowledged))throw new Error('Invalid upload acknowledgement');
  const offset=acknowledged?Number(acknowledged.split('-')[1])+1:0,end=Math.min(size-1,offset+CHUNK_BYTES-1);
  if(offset>=size)throw new Error('Final upload acknowledgement pending');
  const source=await fetch(media,{headers:{Range:`bytes=${offset}-${end}`}});
  if(source.status!==206||source.headers.get('Content-Range')!==`bytes ${offset}-${end}/${size}`||!source.body){await source.body?.cancel();throw new Error('Media storage did not honor byte range');}
  // Workers derives Content-Length from FixedLengthStream (manual header alone is ignored).
  const fixed=new FixedLengthStream(end-offset+1);
  const pump=source.body.pipeTo(fixed.writable);
  const [result]=await Promise.all([fetch(session,{method:'PUT',headers:{...headers,'Content-Length':String(end-offset+1),'Content-Type':'video/mp4','Content-Range':`bytes ${offset}-${end}/${size}`},body:fixed.readable,redirect:'manual'}),pump]);
  if(result.status===308){await result.body?.cancel();return {status:'uploading'};}
  const body=await checked(result);if(!body.id)throw new Error('Missing YouTube result');
  await update(env,id,{status:'published',remote_id:body.id,error:'',actual_privacy:body.status?.privacyStatus||''});return {status:'published'};
 }
 if(!/^\d+$/.test(p.session_url))throw new Error('Invalid Instagram container');
 const base=`https://graph.instagram.com/${env.META_API_VERSION||'v25.0'}`;
 const state=await checked(await fetch(`${base}/${p.session_url}?fields=status_code,status`,{headers}));
 if(['ERROR','EXPIRED'].includes(state.status_code)){await update(env,id,{status:'failed',error:`Instagram container ${state.status_code}; inspect format and permissions`});return {status:'failed'};}
 if(state.status_code!=='FINISHED')return {status:'uploading',wait:true};
 await update(env,id,{status:'committing'});
 try {
  const result=await checked(await fetch(`${base}/${c.account}/media_publish`,{method:'POST',headers,body:new URLSearchParams({creation_id:p.session_url})}));
  if(!result.id)throw new Error('Missing published media ID');
  await update(env,id,{status:'published',remote_id:String(result.id),error:''});return {status:'published'};
 } catch {await update(env,id,{status:'uncertain',error:'Instagram publish response was not confirmed; inspect account before retry'});return {status:'uncertain'};}
}
export class PublishingWorkflow extends WorkflowEntrypoint<PublishingEnv,{publicationId:string;segment?:number}> {
 async run(event:WorkflowEvent<{publicationId:string;segment?:number}>,step:WorkflowStep) {
  const id=event.payload.publicationId,segment=event.payload.segment||0;
  // One unit with two retries: <=30 external requests including refresh, persistence and continuation.
  for(let i=0;i<1;i++) {
   let result:{status:string;wait?:boolean};
   try{result=await step.do(`publish-${i}`,{retries:{limit:2,delay:'10 seconds',backoff:'exponential'}},()=>publishUnit(this.env,id));}
   catch {await step.do('record-safe-failure',()=>update(this.env,id,{status:'uncertain',error:'Publishing interrupted; check connection and reconcile remote account before retry'}));return {status:'uncertain'};}
   if(terminal.has(result.status))return result;
   if(result.wait)await step.sleep(`processing-${i}`,'10 seconds');
  }
  if(segment>=119){await step.do('timeout',()=>update(this.env,id,{status:'uncertain',error:'Publishing deadline exceeded; reconcile remote account'}));return {status:'uncertain'};}
  await step.do('continue',async()=>{const next=`${id}-p${segment+1}`;try{await this.env.PUBLISH_WORKFLOW.create({id:next,params:{publicationId:id,segment:segment+1}});}catch{await(await this.env.PUBLISH_WORKFLOW.get(next)).status();}});
  return {status:'continued'};
 }
}
