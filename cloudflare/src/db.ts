import {ApiError,type Env,type Manifest} from './types';

// Existing Supabase HTTPS Data API. Transactional mutations stay in PostgreSQL.
export async function rpc(env:Env,action:string,id:string='',payload:Record<string,any>={},functionName='cf_media_task') {
  const started=Date.now();
  const response=await fetch(`${env.SUPABASE_URL.replace(/\/$/,'')}/rest/v1/rpc/${functionName}`,{
    method:'POST',headers:{apikey:env.SUPABASE_KEY,Authorization:`Bearer ${env.SUPABASE_KEY}`,'Content-Type':'application/json'},
    body:JSON.stringify({p_action:action,p_task_id:id,p_payload:payload}),signal:AbortSignal.timeout(15000),
  });
  if(!response.ok) {
    const data=await response.json().catch(()=>({})) as any;
    const code=/^[A-Z0-9_]{1,30}$/.test(data.code||'')?data.code:'HTTP_'+response.status;
    console.error(JSON.stringify({event:'database_rpc_failure',code,action,elapsed_ms:Date.now()-started}));
    throw new ApiError(502,`Database API operation failed (${code})`);
  }
  const result=await response.json() as any;
  if(result?.error) throw new ApiError(result.status||409,result.error);
  return result;
}
export const generation=(env:Env,action:string,id:string,payload:Record<string,any>={})=>rpc(env,action,id,payload,'cf_generation');
export const loadTask=(env:Env,id:string)=>rpc(env,'load',id);
export const createTask=(env:Env,id:string,videoId:string,manifest:Manifest)=>rpc(env,'create',id,{video_id:videoId,manifest});
export async function claim(env:Env,id:string,ownerHash:string) {
  const result=await rpc(env,'claim',id,{owner_hash:ownerHash});
  return result?.manifest as Manifest|undefined;
}
export async function owned(env:Env,id:string,ownerHash:string) {
  const row=await loadTask(env,id);
  if(row.owner_hash!==ownerHash || new Date(row.deadline).getTime()<Date.now()) throw new ApiError(409,'Task is not owned or has expired');
  return row;
}
export const finish=(env:Env,id:string,ownerHash:string,status:string,result:Record<string,any>,error:string)=>rpc(env,'finish',id,{owner_hash:ownerHash,status,result,error});
export const expire=(env:Env,id:string)=>rpc(env,'expire',id);
export const heartbeat=(env:Env,id:string,ownerHash:string)=>rpc(env,'heartbeat',id,{owner_hash:ownerHash});
export const triggered=(env:Env,id:string,pipelineId:string)=>rpc(env,'triggered',id,{pipeline_id:pipelineId});
export const triggerFailed=(env:Env,id:string)=>rpc(env,'trigger_failed',id);
