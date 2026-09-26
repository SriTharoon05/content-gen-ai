import {ApiError, taskId, type Env, type Manifest} from './types';
import {claim,createTask,rpc,heartbeat,finish,loadTask,owned,generation} from './db';
import {assetUpload,digest,resolveAssets,signedUpload,validateManifest,verifyOutput} from './storage';
import {tick,saveSchedule} from './scheduler';
import {handleDashboardAdmin} from './dashboardAdmin';
import {handleEditing} from './editing';
import {publishingRoute,reconcilePublishing} from './publishing';
import {oauthRoute} from './oauth';
export {MediaWorkflow} from './workflow';
export {GenerationWorkflow} from './generation';
export {GenerationStageWorkflow} from './stages';
export {EditingWorkflow} from './editing';
export {PublishingWorkflow} from './publishingWorkflow';

async function authorize(request: Request, secret: string) {
  const supplied = request.headers.get('Authorization') || '';
  if (!secret || await digest(supplied) !== await digest('Bearer '+secret)) throw new ApiError(401,'Invalid credentials');
}
async function body(request: Request): Promise<any> {
  const limit=512*1024;
  if(Number(request.headers.get('Content-Length'))>limit)throw new ApiError(413,'Send asset references, not media bytes');
  const reader=request.body?.getReader();
  if(!reader)throw new ApiError(400,'JSON body required');
  const chunks:Uint8Array[]=[];let size=0;
  while(true){const {done,value}=await reader.read();if(done)break;size+=value.byteLength;
    if(size>limit){await reader.cancel();throw new ApiError(413,'Send asset references, not media bytes');}chunks.push(value);}
  const bytes=new Uint8Array(size);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}
  try {return JSON.parse(new TextDecoder().decode(bytes));} catch {throw new ApiError(400,'Invalid JSON');}
}

export default {
  async scheduled(_event:ScheduledController,env:Env,ctx:ExecutionContext){
    ctx.waitUntil((async()=>{await tick(env);await reconcilePublishing(env);})());
  },
  async fetch(request: Request, env: Env): Promise<Response> {
    const origin = request.headers.get('Origin') || '';
    const headers: Record<string,string> = {'Content-Type':'application/json','Cache-Control':'no-store','Vary':'Origin'};
    if (env.CORS_ORIGINS.split(',').map(x=>x.trim()).includes(origin)) {
      headers['Access-Control-Allow-Origin']=origin;
      headers['Access-Control-Allow-Headers']='Authorization, Content-Type, Idempotency-Key';
      headers['Access-Control-Allow-Methods']='GET, POST, PUT, DELETE, OPTIONS';
    }
    const reply = (data: unknown,status=200)=>new Response(JSON.stringify(data),{status,headers});
    try {
      const path = new URL(request.url).pathname;
      if (request.method==='OPTIONS') return new Response(null,{status:204,headers});
      if (path==='/health') return reply({ok:true,mode:'cloudflare-native',production_ready:false});
      if (env.ENABLE_MEDIA_PILOT !== 'true') throw new ApiError(503,'Media pilot is disabled');
      const wrap=(response:Response)=>{
        const merged=new Headers(response.headers);
        for(const [key,value] of Object.entries(headers))if(key!=='Content-Type')merged.set(key,value);
        return new Response(response.body,{status:response.status,headers:merged});
      };
      if(path.startsWith('/auth/')){
        const response=await oauthRoute(request,env);if(response)return wrap(response);
      }
      if(path.startsWith('/api/')){
        await authorize(request,env.ADMIN_TOKEN);
        for(const handler of [handleDashboardAdmin,handleEditing,publishingRoute,oauthRoute]){
          const response=await handler(request,env);if(response)return wrap(response);
        }
      }
      if(path==='/api/schedule'&&['GET','POST'].includes(request.method)){
        await authorize(request,env.ADMIN_TOKEN);
        return reply(request.method==='GET'?await rpc(env,'get','',{},'cf_schedule'):await saveSchedule(env,await body(request)));
      }
      if(['/api/channels','/api/videos'].includes(path)&&request.method==='GET'){
        await authorize(request,env.ADMIN_TOKEN);
        return reply(await rpc(env,path.split('/').pop()!,'',{},'cf_dashboard'));
      }
      if(path==='/api/health'&&request.method==='GET'){
        await authorize(request,env.ADMIN_TOKEN);
        return reply({ok:true,backend:'cloudflare',production_ready:false,capabilities:['generation','preview','timing','channels','settings','scheduling','publishing','oauth','analytics','music','editing']});
      }
      const runMatch=path.match(/^\/api\/channels\/([a-z0-9_-]{1,64})\/run$/);
      if(runMatch&&request.method==='POST'){
        await authorize(request,env.ADMIN_TOKEN);
        const input=await body(request);
        if(!input||Array.isArray(input)||Object.keys(input).some(k=>!['review_required','publishing_mode','publish_platforms','topic'].includes(k))||typeof input.review_required!=='boolean')
          throw new ApiError(422,'Choose review_required true or false');
        if(input.publishing_mode!==undefined&&!['review','direct','settings'].includes(input.publishing_mode))throw new ApiError(422,'Invalid publishing mode');
        if(input.publishing_mode==='review'&&!input.review_required||input.publishing_mode==='direct'&&input.review_required)throw new ApiError(422,'Publishing mode conflicts with review selection');
        if(input.publish_platforms!==undefined&&(!Array.isArray(input.publish_platforms)||!input.publish_platforms.length||input.publish_platforms.length>2||input.publish_platforms.some((p:any)=>!['youtube','instagram'].includes(p))))throw new ApiError(422,'Choose YouTube and/or Instagram');
        if(input.topic!==undefined&&(typeof input.topic!=='string'||input.topic.length>1000))throw new ApiError(422,'Topic must be at most 1,000 characters');
        const id=taskId(request.headers.get('Idempotency-Key')||'');
        await generation(env,'create',id,{...input,channel:runMatch[1]});
        try{await env.GENERATION_WORKFLOW.create({id,params:{videoId:id}});}
        catch(e){try{await(await env.GENERATION_WORKFLOW.get(id)).status();}catch{throw e;}}
        return reply({video_id:id,review_required:input.review_required},202);
      }
      if(path==='/migration/assets/recent'&&request.method==='GET'){
        await authorize(request,env.ADMIN_TOKEN);
        const url=`https://api.cloudinary.com/v1_1/${env.CLOUDINARY_CLOUD_NAME}/resources/image/upload?prefix=${encodeURIComponent(env.CLOUDINARY_PREFIX+'/assets/image/')}&max_results=100`;
        const r=await fetch(url,{headers:{Authorization:'Basic '+btoa(`${env.CLOUDINARY_API_KEY}:${env.CLOUDINARY_API_SECRET}`)}});
        if(!r.ok)throw new ApiError(502,'Cloudinary asset lookup HTTP '+r.status);
        const b=await r.json() as any;
        return reply({assets:(b.resources||[]).map((x:any)=>({url:x.secure_url,public_id:x.public_id,bytes:x.bytes,created_at:x.created_at}))});
      }
      if(path==='/migration/generations'&&request.method==='POST'){
        await authorize(request,env.ADMIN_TOKEN);
        const id=taskId(request.headers.get('Idempotency-Key')||'');const input=await body(request);
        if(typeof input.channel!=='string'||!/^[a-z0-9_-]{1,64}$/.test(input.channel))throw new ApiError(400,'Invalid channel');
        await generation(env,'create',id,{channel:input.channel,review_required:true});
        try{await env.GENERATION_WORKFLOW.create({id,params:{videoId:id}});}
        catch(e){try{await(await env.GENERATION_WORKFLOW.get(id)).status();}catch{throw e;}}
        return reply({video_id:id,review_required:true,publishing:false},202);
      }
      const genMatch=path.match(/^\/migration\/generations\/([a-f0-9]{32})$/);
      if(genMatch&&request.method==='GET'){
        await authorize(request,env.ADMIN_TOKEN);const state=await generation(env,'status',genMatch[1]);
        return reply({...state,steps:Object.keys(state.steps||{})});
      }
      if (path==='/migration/database-check' && request.method==='GET') {
        await authorize(request,env.ADMIN_TOKEN);
        return reply(await rpc(env,'check'));
      }
      if (path==='/migration/assets/upload' && request.method==='POST') {
        await authorize(request,env.ADMIN_TOKEN);
        return reply(await assetUpload(env,await body(request)));
      }
      if (path==='/migration/media-tasks' && request.method==='POST') {
        await authorize(request,env.ADMIN_TOKEN);
        const id=taskId(request.headers.get('Idempotency-Key')||'');
        const input=await body(request);
        const video=taskId(input.video_id||'');
        const manifest=validateManifest(input.manifest as Manifest,env);
        await createTask(env,id,video,manifest);
        try {await env.MEDIA_WORKFLOW.create({id,params:{taskId:id}});}
        catch (error) {
          // A replay after a lost response is safe; other creation failures must surface.
          try {await (await env.MEDIA_WORKFLOW.get(id)).status();} catch {throw error;}
        }
        return reply({render_task_id:id,job_id:id},202);
      }
      const statusMatch=path.match(/^\/migration\/media-tasks\/([a-f0-9]{32})$/);
      if (statusMatch && request.method==='GET') {
        await authorize(request,env.ADMIN_TOKEN);
        const t=await loadTask(env,statusMatch[1]);
        return reply({render_task_id:t.id,status:t.status,pipeline_id:t.pipeline_id,result:t.result_json,error:t.error});
      }
      const match=path.match(/^\/internal\/media-tasks\/([a-f0-9]{32})\/(claim|heartbeat|upload|complete)$/);
      if (match && request.method==='POST') {
        await authorize(request,env.MEDIA_WORKER_TOKEN);
        const [,id,action]=match;
        const data=await body(request);
        if (typeof data.owner!=='string' || data.owner.length<32 || data.owner.length>128) throw new ApiError(400,'Invalid worker owner');
        const hash=await digest(data.owner);
        if (action==='claim') {
          const manifest=await claim(env,id,hash);
          return reply(manifest ? {claimed:true,manifest:await resolveAssets(manifest,env)} : {claimed:false});
        }
        const task=await owned(env,id,hash);
        if (action==='heartbeat') {
          await heartbeat(env,id,hash);
          return reply({ok:true});
        }
        if (action==='upload') {
          if (task.status!=='running') throw new ApiError(409,'Task is not running');
          return reply(await signedUpload(env,id,task.manifest_json.operation));
        }
        if (!['succeeded','failed'].includes(data.status)) throw new ApiError(400,'Invalid completion status');
        let result:any={};
        if(data.status==='succeeded'){
          const verified=await verifyOutput(env,id,task.manifest_json.operation);
          const measured=Number(data.metrics?.duration);
          if(!Number.isFinite(measured)||measured<=0||measured>600)throw new ApiError(409,'Missing measured output duration');
          if(verified.duration!=null&&Math.abs(verified.duration-measured)>.2)throw new ApiError(409,'Output duration disagrees with canonical probe');
          // Cloudinary omits duration for some WAV resources; the trusted canonical ffprobe result supplies it.
          result={...verified,duration:verified.duration??measured,metrics:data.metrics||{}};
        }
        // Never store raw signed URLs or provider exceptions as error strings.
        await finish(env,id,hash,data.status,result,data.status==='failed'?'CircleCI media processing failed; inspect sanitized job logs':'');
        try {await (await env.MEDIA_WORKFLOW.get(id)).sendEvent({type:'media-complete',payload:{taskId:id}});} catch { /* Workflow reconciles DB. */ }
        return reply({ok:true});
      }
      throw new ApiError(404,'Unknown API route');
    } catch (error) {
      if(!(error instanceof ApiError))console.error(JSON.stringify({event:'api_failure',kind:error instanceof Error?error.name:'Unknown',
        frames:error instanceof Error?error.stack?.split('\n').slice(1,4).map(line=>line.replace(/https?:\/\/\S+/g,'[url]')):[]}));
      return reply({detail:error instanceof ApiError?error.message:'Operation failed; inspect sanitized server logs'},error instanceof ApiError?error.status:500);
    }
  }
} satisfies ExportedHandler<Env>;
