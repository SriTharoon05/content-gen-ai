import {ApiError, taskId, type Env, type Manifest} from './types';
import {claim,createTask,rpc,heartbeat,finish,loadTask,owned,generation} from './db';
import {assetUpload,digest,resolveAssets,signedUpload,validateManifest,verifyOutput} from './storage';
export {MediaWorkflow} from './workflow';
export {GenerationWorkflow} from './generation';

async function authorize(request: Request, secret: string) {
  const supplied = request.headers.get('Authorization') || '';
  if (!secret || await digest(supplied) !== await digest('Bearer '+secret)) throw new ApiError(401,'Invalid credentials');
}
async function body(request: Request): Promise<any> {
  const bytes = await request.arrayBuffer();
  if (bytes.byteLength > 512*1024) throw new ApiError(413,'Send asset references, not media bytes');
  try {return JSON.parse(new TextDecoder().decode(bytes));} catch {throw new ApiError(400,'Invalid JSON');}
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const origin = request.headers.get('Origin') || '';
    const headers: Record<string,string> = {'Content-Type':'application/json','Cache-Control':'no-store','Vary':'Origin'};
    if (env.CORS_ORIGINS.split(',').map(x=>x.trim()).includes(origin)) {
      headers['Access-Control-Allow-Origin']=origin;
      headers['Access-Control-Allow-Headers']='Authorization, Content-Type, Idempotency-Key';
      headers['Access-Control-Allow-Methods']='GET, POST, OPTIONS';
    }
    const reply = (data: unknown,status=200)=>new Response(JSON.stringify(data),{status,headers});
    try {
      const path = new URL(request.url).pathname;
      if (request.method==='OPTIONS') return new Response(null,{status:204,headers});
      if (path==='/health') return reply({ok:true,mode:'isolated-media-pilot',production_ready:false});
      if (env.ENABLE_MEDIA_PILOT !== 'true') throw new ApiError(503,'Media pilot is disabled');
      if(path==='/migration/generations'&&request.method==='POST'){
        await authorize(request,env.ADMIN_TOKEN);
        const id=taskId(request.headers.get('Idempotency-Key')||'');const input=await body(request);
        if(typeof input.channel!=='string'||!/^[a-z0-9_-]{1,64}$/.test(input.channel))throw new ApiError(400,'Invalid channel');
        await generation(env,'create',id,{channel:input.channel});
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
        const result=data.status==='succeeded' ? {...await verifyOutput(env,id,task.manifest_json.operation),metrics:data.metrics||{}} : {};
        // Never store raw signed URLs or provider exceptions as error strings.
        await finish(env,id,hash,data.status,result,data.status==='failed'?'CircleCI media processing failed; inspect sanitized job logs':'');
        try {await (await env.MEDIA_WORKFLOW.get(id)).sendEvent({type:'media-complete',payload:{taskId:id}});} catch { /* Workflow reconciles DB. */ }
        return reply({ok:true});
      }
      throw new ApiError(404,'Route is not implemented in the isolated pilot; keep the frontend on Render');
    } catch (error) {
      return reply({detail:error instanceof ApiError?error.message:'Pilot operation failed'},error instanceof ApiError?error.status:500);
    }
  }
} satisfies ExportedHandler<Env>;
