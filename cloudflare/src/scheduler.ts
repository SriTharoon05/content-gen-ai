import {rpc} from './db';
import {ApiError,type Env} from './types';

export function validateSchedule(input:any){
  const s=input?.schedule;
  if(!['render','cloudflare'].includes(input?.owner)||!s||typeof s.enabled!=='boolean'||
    !/^([01][0-9]|2[0-3]):[0-5][0-9]$/.test(s.run_at)||
    !Number.isInteger(s.videos_per_channel)||s.videos_per_channel<1||s.videos_per_channel>20||
    !Number.isInteger(s.timezone_offset_minutes)||s.timezone_offset_minutes< -720||s.timezone_offset_minutes>840||
    typeof s.daily_credit_ceiling!=='number'||!Number.isFinite(s.daily_credit_ceiling)||s.daily_credit_ceiling<0||s.daily_credit_ceiling>10||
    !Array.isArray(s.channels)||s.channels.some((v:any)=>typeof v!=='string'||!/^[a-z0-9_-]{1,64}$/.test(v)))
    throw new ApiError(422,'Invalid schedule settings');
  return input;
}

export async function saveSchedule(env:Env,input:any){
  validateSchedule(input);
  if(input.owner==='cloudflare'){
    // Only a server-configured origin can attest to the deployed ownership guard.
    const origin=env.RENDER_API_ORIGIN;
    if(!origin)throw new ApiError(409,'Configure RENDER_API_ORIGIN and deploy the Render scheduler guard first');
    const response=await fetch(new URL('/health',origin),{signal:AbortSignal.timeout(15000),redirect:'error'});
    const health=await response.json() as any;
    if(!response.ok||health.scheduler_owner_guard!==true)throw new ApiError(409,'Render scheduler ownership guard is not deployed');
    await rpc(env,'verify_guard','',{},'cf_schedule');
  }
  return rpc(env,'save','',input,'cf_schedule');
}

export async function tick(env:Env) {
  const batch=await rpc(env,'tick','',{},'cf_schedule');
  // Stay below the free request subrequest ceiling, even when all creates replay.
  const pending=(batch.videos||[]).slice(0,5);
  for(const {video_id:id} of pending){
    try{await env.GENERATION_WORKFLOW.create({id,params:{videoId:id}});}
    catch(error){try{await(await env.GENERATION_WORKFLOW.get(id)).status();}catch{throw error;}}
    await rpc(env,'submitted',id,{},'cf_schedule');
  }
  return {queued:pending.length};
}
