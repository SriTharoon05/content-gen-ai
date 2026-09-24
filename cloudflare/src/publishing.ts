import {ApiError} from './types';
import {publishRpc,accessToken,checked,socialFetch,type PublishingEnv} from './oauth';
export const CHUNK_BYTES=4*1024*1024;
export function trustedMedia(env:PublishingEnv,value:string) {
 const u=new URL(value),supa=new URL(env.SUPABASE_URL);
 if(u.protocol!=='https:'||u.username||u.password||!((u.hostname==='res.cloudinary.com'&&u.pathname.startsWith('/'+env.CLOUDINARY_CLOUD_NAME+'/'))||(u.origin===supa.origin&&u.pathname.startsWith('/storage/v1/object/'))))throw new ApiError(400,'Untrusted persisted media URL');
 return u.href;
}
export function uploadUrl(value:string) {const u=new URL(value);if(u.protocol!=='https:'||u.hostname!=='www.googleapis.com'||!u.pathname.startsWith('/upload/youtube/'))throw new ApiError(502,'Invalid YouTube upload session');return u.href;}
export function publicationCopy(v:any) {
 const generic=new Set(['viral','fyp','foryou','foryoupage','trending','explorepage','followforfollow','likeforlike']);
 const tags=[...new Set((v.hashtags||[]).map((s:string)=>s.normalize('NFC').replace(/^#/,'').replace(/[^\p{L}\p{N}\p{M}_]/gu,'').slice(0,40)).filter((s:string)=>s&&!generic.has(s.toLowerCase())&&!/^\d+$/.test(s)))] as string[];
 const footer=(text:string,n:number,limit:number)=>{const f=tags.slice(0,n).map(t=>'#'+t).join(' ');return [String(text||'').replace(/#[^\s#.,!?;:()[\]{}]+/gu,'').trim().slice(0,limit-f.length-(f?2:0)),f].filter(Boolean).join('\n\n');};
 return {title:String(v.title||'').replace(/ \[(flux\.1-schnell|dreamshaper-8-lcm|z-image-turbo)\]$/,'').slice(0,100),description:footer(v.description,3,5000),tags:tags.slice(0,5),caption:footer(v.instagram_caption,5,2200)};
}
export async function startPublication(env:PublishingEnv,videoId:string,platform:string) {
 const row=await publishRpc(env,'request',videoId,{platform});
 if(row.status==='pending'||row.status==='uploading') {
  try {await env.PUBLISH_WORKFLOW.create({id:row.id,params:{publicationId:row.id}});}catch {await (await env.PUBLISH_WORKFLOW.get(row.id)).status();}
 }
 return row;
}
export async function routeCompletedVideo(env:PublishingEnv,videoId:string) {
 const p=await publishRpc(env,'policy',videoId),o=p.options||{},mode=o.publishing_mode||'settings';
 if(o.auto_publish===false||(mode==='settings'&&!p.schedule?.auto_publish))return {status:'disabled'};
 if(!p.approved&&(o.force_review||mode==='review'||(mode!=='direct'&&(p.publishing?.review_before_upload??true))))return {status:'awaiting_approval'};
 const platforms=o.publish_platforms||p.publishing?.platforms||['youtube'];const results:Record<string,any>={};
 for(const platform of [...new Set(platforms)] as string[])if(['youtube','instagram'].includes(platform)) {
  try{results[platform]=await startPublication(env,videoId,platform);}catch {results[platform]={status:'blocked',message:'Connect destination or check publishing policy'};}
 }
 return {status:Object.values(results).some(x=>x.status==='blocked')?'blocked':'routed',destinations:results};
}
// Durable generation outbox. One video per tick bounds free-tier request usage.
export async function reconcilePublishing(env:PublishingEnv) {
 const pending=await publishRpc(env,'pending_dispatch');const results=[];
 for(const id of pending.videos||[]) {
  try {
   const result=await routeCompletedVideo(env,id);
   await publishRpc(env,'dispatched',id,{clear:result.status!=='blocked',error:result.status==='blocked'?'Publishing blocked: connect selected destinations and verify policy':''});
   results.push({id,...result});
  } catch {
   await publishRpc(env,'dispatched',id,{clear:false,error:'Publishing dispatch interrupted; automatic retry pending'});
   results.push({id,status:'blocked'});
  }
 }
 return {results};
}
// All routes here require the host's existing admin authorization guard.
export async function publishingRoute(request:Request,env:PublishingEnv):Promise<Response|null> {
 const url=new URL(request.url);
 if(url.pathname==='/api/connections'&&request.method==='GET') {
  const data=await publishRpc(env,'connections');
  return Response.json({...data,google_configured:!!(env.GOOGLE_CLIENT_ID&&env.GOOGLE_CLIENT_SECRET),meta_configured:!!(env.META_APP_ID&&env.META_APP_SECRET)});
 }
 const match=url.pathname.match(/^\/api\/videos\/([a-f0-9]{32})\/(approve|publish|publications)$/);
 if(match) {
  if(match[2]==='publications'&&request.method==='GET')return Response.json(await publishRpc(env,'status',match[1]));
  if(request.method!=='POST')throw new ApiError(405,'POST required');
  const body=await request.json() as any;
  if(match[2]==='approve')return Response.json(await publishRpc(env,'approve',match[1],{output:body.output}));
  if(match[2]==='publish')return Response.json(await startPublication(env,match[1],body.platform));
 }
 const analytics=url.pathname.match(/^\/api\/channels\/([^/]+)\/analytics\/(youtube|instagram)$/);
 if(analytics&&request.method==='GET') {
  const platform=analytics[2] as 'youtube'|'instagram',c=await accessToken(env,decodeURIComponent(analytics[1]),platform);
  const days=Math.max(1,Math.min(platform==='youtube'?90:30,Number(url.searchParams.get('days'))||28));
  const end=new Date(Date.now()-86400000);end.setUTCHours(0,0,0,0);const start=new Date(end.getTime()-(days-1)*86400000);
  const endpoint=platform==='youtube'?'https://youtubeanalytics.googleapis.com/v2/reports?'+new URLSearchParams({ids:'channel==MINE',startDate:start.toISOString().slice(0,10),endDate:end.toISOString().slice(0,10),metrics:'views,estimatedMinutesWatched,averageViewDuration,subscribersGained',dimensions:'day',sort:'day'}):`https://graph.instagram.com/${env.META_API_VERSION||'v25.0'}/${c.account}/insights?`+new URLSearchParams({metric:'views,reach,accounts_engaged,total_interactions',metric_type:'total_value',period:'day',since:String(start.getTime()/1000),until:String(end.getTime()/1000)});
  return Response.json(await checked(await socialFetch(endpoint,{headers:{Authorization:`Bearer ${c.token}`}})));
 }
 return null;
}
