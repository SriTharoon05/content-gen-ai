import type {Env} from './types';

// Admin-authenticated diagnostics. Never return credentials or provider bodies.
export async function platformUsage(env:Env) {
  const r=await fetch(`https://api.cloudinary.com/v1_1/${env.CLOUDINARY_CLOUD_NAME}/usage`,{
    headers:{Authorization:'Basic '+btoa(`${env.CLOUDINARY_API_KEY}:${env.CLOUDINARY_API_SECRET}`)},signal:AbortSignal.timeout(15000),
  });
  if(!r.ok){await r.body?.cancel();return {cloudinary:{available:false,status:r.status}};}
  const source=await r.json() as any;
  const usage:any={available:true};
  for(const key of ['plan','last_updated','date'])if(typeof source[key]==='string')usage[key]=source[key];
  for(const key of ['credits','storage','bandwidth','transformations','requests','resources','objects']){
    const value=source[key];
    if(typeof value==='number')usage[key]=value;
    else if(value&&typeof value==='object')usage[key]=Object.fromEntries(Object.entries(value).filter(([k,v])=>['usage','used','limit','credits_usage','used_percent'].includes(k)&&typeof v==='number'));
  }
  return {cloudinary:usage};
}
