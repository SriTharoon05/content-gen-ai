import type {Env} from './types';
import {ApiError} from './types';
import {rpc} from './db';
import {random,digest,b64,fernetKey,encrypt,decrypt} from './crypto';
export interface PublishingEnv extends Env {
 GOOGLE_CLIENT_ID:string; GOOGLE_CLIENT_SECRET:string; GOOGLE_REDIRECT_URI:string;
 META_APP_ID:string; META_APP_SECRET:string; META_REDIRECT_URI:string; META_API_VERSION:string;
 OAUTH_ENCRYPTION_KEY?:string;
 PUBLISH_WORKFLOW:Workflow<{publicationId:string;segment?:number}>;
}
export const publishRpc=(env:Env,action:string,id='',payload:Record<string,any>={})=>rpc(env,action,id,payload,'cf_publish');
export async function socialFetch(input:string,init:RequestInit={}) {
 // Workers supports manual/follow, not the browser-only error redirect mode.
 // Reject redirects ourselves so provider credentials never follow a new host.
 const response=await fetch(input,{...init,redirect:'manual',signal:AbortSignal.timeout(45000)});
 // YouTube uses 308 without Location for resumable-upload acknowledgement.
 if(response.status>=300&&response.status<400&&response.headers.has('Location'))throw new ApiError(502,'Unexpected social provider redirect');
 return response;
}
const scopes=['https://www.googleapis.com/auth/youtube.upload','https://www.googleapis.com/auth/youtube.readonly','https://www.googleapis.com/auth/yt-analytics.readonly'];
const igScopes=['instagram_business_basic','instagram_business_content_publish','instagram_business_manage_insights'];
export async function checked(response:Response) {
 const body=await response.json().catch(()=>({})) as any;
 if(!response.ok||body.error)throw new ApiError(502,`Social provider request rejected (HTTP ${response.status}; code ${Number.isInteger(body.error?.code)?body.error.code:'unavailable'})`);
 return body;
}
export async function key(env:PublishingEnv,provider:'google'|'meta') {return fernetKey(env.OAUTH_ENCRYPTION_KEY,provider==='google'?env.GOOGLE_CLIENT_SECRET:env.META_APP_SECRET,provider);}
async function storedCredential(env:PublishingEnv,provider:'google'|'meta',value:string){
 try{return await decrypt(value,await key(env,provider));}
 catch{throw new ApiError(409,`${provider} stored connection cannot be decrypted; use the original OAuth secret/encryption key or reconnect`);}
}
function config(env:PublishingEnv,provider:'google'|'meta') {
 const google=provider==='google';const id=google?env.GOOGLE_CLIENT_ID:env.META_APP_ID,secret=google?env.GOOGLE_CLIENT_SECRET:env.META_APP_SECRET;
 if(!id||!secret)throw new ApiError(409,`Configure ${provider} OAuth credentials first`);
 const uri=new URL(google?env.GOOGLE_REDIRECT_URI:env.META_REDIRECT_URI);
 if((uri.protocol!=='https:'&&!(uri.protocol==='http:'&&['localhost','127.0.0.1'].includes(uri.hostname)))||uri.pathname!==`/auth/${provider}/callback`||uri.search||uri.hash||uri.username)throw new ApiError(409,'Invalid OAuth callback URL');
 return {id,secret,uri};
}
export async function accessToken(env:PublishingEnv,slug:string,platform:'youtube'|'instagram') {
 const rows=await publishRpc(env,'credentials',slug);
 if(platform==='youtube') {
  if(!rows.youtube?.refresh_token_encrypted)throw new ApiError(409,'Connect YouTube first');
  const refresh=await storedCredential(env,'google',rows.youtube.refresh_token_encrypted);
  const result=await checked(await socialFetch('https://oauth2.googleapis.com/token',{method:'POST',body:new URLSearchParams({client_id:env.GOOGLE_CLIENT_ID,client_secret:env.GOOGLE_CLIENT_SECRET,refresh_token:refresh,grant_type:'refresh_token'})}));
  return {token:result.access_token as string,account:rows.youtube.remote_channel_id as string};
 }
 const row=rows.instagram;
 if(!row?.token_encrypted||row.login_mode!=='instagram'||Date.parse(row.token_expires_at)<=Date.now())throw new ApiError(409,'Reconnect direct Instagram Login');
 let token=await storedCredential(env,'meta',row.token_encrypted);
 if(Date.parse(row.token_expires_at)<Date.now()+7*86400000) {
  const result=await checked(await socialFetch('https://graph.instagram.com/refresh_access_token?'+new URLSearchParams({grant_type:'ig_refresh_token',access_token:token})));
  token=result.access_token;
  await publishRpc(env,'oauth_save',slug,{platform,encrypted:await encrypt(token,await key(env,'meta')),remote_id:row.account_id,name:row.account_name,expires_at:new Date(Date.now()+result.expires_in*1000).toISOString()});
 }
 return {token,account:row.account_id as string};
}
// Public start/callback routes are browser-bound; connect route MUST be behind admin auth.
export async function oauthRoute(request:Request,env:PublishingEnv):Promise<Response|null> {
 const url=new URL(request.url); const connect=url.pathname.match(/^\/api\/channels\/([^/]+)\/connect\/(google|meta)$/);
 if(connect&&request.method==='POST') {
  const provider=connect[2] as 'google'|'meta',cfg=config(env,provider),ticket=random();
  await publishRpc(env,'oauth_ticket',await digest(ticket),{channel:decodeURIComponent(connect[1]),phase:provider==='google'?'ticket':'meta_ticket'});
  return Response.json({authorization_url:`${cfg.uri.origin}/auth/${provider}/start?ticket=${encodeURIComponent(ticket)}`});
 }
 const match=url.pathname.match(/^\/auth\/(google|meta)\/(start|callback)$/);if(!match)return null;
 const provider=match[1] as 'google'|'meta',google=provider==='google',cfg=config(env,provider);
 const cookieName=`cf_oauth_${provider}`,cookieAttrs=`; HttpOnly; SameSite=Lax; Path=/auth/${provider}${cfg.uri.protocol==='https:'?'; Secure':''}`;
 if(request.method!=='GET')throw new ApiError(405,'GET required');
 if(match[2]==='start') {
  const state=random(),nonce=google?state:random(),verifier=google?random()+random():await digest(nonce);
  await publishRpc(env,'oauth_start',await digest(url.searchParams.get('ticket')||''),{phase:google?'ticket':'meta_ticket',state:await digest(state),verifier,next_phase:google?'consent':'ig_consent'});
  const params=new URLSearchParams({client_id:cfg.id,redirect_uri:cfg.uri.href,response_type:'code',state,scope:google?scopes.join(' '):igScopes.join(',')});
  if(google){params.set('access_type','offline');params.set('prompt','consent select_account');params.set('code_challenge_method','S256');params.set('code_challenge',b64(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(verifier)))).replace(/=+$/,''));}
  else {params.set('enable_fb_login','0');params.set('force_authentication','1');}
  return new Response(null,{status:302,headers:{Location:(google?'https://accounts.google.com/o/oauth2/v2/auth?':'https://www.instagram.com/oauth/authorize?')+params,'Set-Cookie':`${cookieName}=${nonce}; Max-Age=600${cookieAttrs}`,'Cache-Control':'no-store'}});
 }
 const state=url.searchParams.get('state')||'',code=url.searchParams.get('code')||'';
 const cookie=request.headers.get('Cookie')?.split(';').map(x=>x.trim()).find(x=>x.startsWith(cookieName+'='))?.slice(cookieName.length+1)||'';
 if(!state||!cookie||!code||(google&&await digest(state)!==await digest(cookie)))throw new ApiError(400,'OAuth browser mismatch or authorization denied; reconnect');
 const attempt=await publishRpc(env,'oauth_consume',await digest(state),{phase:google?'consent':'ig_consent',...(!google?{nonce:await digest(cookie)}:{})});
 if(google) {
  const tokens=await checked(await socialFetch('https://oauth2.googleapis.com/token',{method:'POST',body:new URLSearchParams({client_id:cfg.id,client_secret:cfg.secret,redirect_uri:cfg.uri.href,code,code_verifier:attempt.verifier,grant_type:'authorization_code'})}));
  if(!tokens.refresh_token||!scopes.every(s=>(tokens.scope||'').split(' ').includes(s)))throw new ApiError(400,'Grant offline upload, channel read and analytics permissions');
  const channels=await checked(await socialFetch('https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true',{headers:{Authorization:`Bearer ${tokens.access_token}`}}));
  if(channels.items?.length!==1)throw new ApiError(400,'Select an account with exactly one YouTube channel');
  await publishRpc(env,'oauth_save',attempt.channel,{platform:'youtube',encrypted:await encrypt(tokens.refresh_token,await key(env,provider)),remote_id:channels.items[0].id,name:channels.items[0].snippet.title});
 } else {
  const short=await checked(await socialFetch('https://api.instagram.com/oauth/access_token',{method:'POST',body:new URLSearchParams({client_id:cfg.id,client_secret:cfg.secret,grant_type:'authorization_code',redirect_uri:cfg.uri.href,code})}));
  const long=await checked(await socialFetch('https://graph.instagram.com/access_token?'+new URLSearchParams({grant_type:'ig_exchange_token',client_secret:cfg.secret,access_token:short.access_token})));
  const account=await checked(await socialFetch(`https://graph.instagram.com/${env.META_API_VERSION||'v25.0'}/me?fields=user_id,username`,{headers:{Authorization:`Bearer ${long.access_token}`}}));
  if(!account.user_id||!account.username||!long.expires_in)throw new ApiError(502,'Instagram professional account identity missing');
  await publishRpc(env,'oauth_save',attempt.channel,{platform:'instagram',encrypted:await encrypt(long.access_token,await key(env,provider)),remote_id:String(account.user_id),name:account.username,expires_at:new Date(Date.now()+long.expires_in*1000).toISOString()});
 }
 return new Response('Connected successfully. Return to the dashboard.',{headers:{'Content-Type':'text/plain; charset=utf-8','Set-Cookie':`${cookieName}=; Max-Age=0${cookieAttrs}`,'Cache-Control':'no-store'}});
}
