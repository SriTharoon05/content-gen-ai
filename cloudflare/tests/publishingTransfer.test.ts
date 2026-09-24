import {test} from 'node:test';
import assert from 'node:assert/strict';
import {registerHooks} from 'node:module';
import {encrypt,fernetKey} from '../src/crypto';
import type {PublishingEnv} from '../src/oauth';
// Only replace the Workers runtime base class; all transfer/DB/provider code is real.
registerHooks({resolve(specifier,context,next){if(specifier==='cloudflare:workers')return {url:'data:text/javascript,export class WorkflowEntrypoint {}',shortCircuit:true};return next(specifier,context);}});
// Node lacks Workers' fixed-length stream. The mock transport drains its body below.
Object.defineProperty(globalThis,'FixedLengthStream',{value:class extends TransformStream {constructor(_size:number){super();}},configurable:true});
const {publishUnit}=await import('../src/publishingWorkflow');
const env={SUPABASE_URL:'https://fixture.supabase.co',SUPABASE_KEY:'fixture',CLOUDINARY_CLOUD_NAME:'fixture',GOOGLE_CLIENT_ID:'fixture',GOOGLE_CLIENT_SECRET:'fixture',META_APP_SECRET:'fixture',META_API_VERSION:'v25.0'} as PublishingEnv;
test('YouTube probes resumable offset and streams only the remaining range',async()=>{
 const original=globalThis.fetch,updates:any[]=[],requests:string[]=[];
 const encrypted=await encrypt('refresh-fixture',await fernetKey(undefined,'fixture','google'));
 let puts=0;
 globalThis.fetch=async(input,init)=>{
  const url=String(input);requests.push(url);
  if(url.includes('/rpc/')){const p=JSON.parse(init!.body as string);if(p.p_action==='claim')return Response.json({publication:{status:'uploading',platform:'youtube',session_url:'https://www.googleapis.com/upload/youtube/v3/videos?upload_id=fixture'},video:{channel:'fixture',output:'https://res.cloudinary.com/fixture/video/upload/file.mp4'}});if(p.p_action==='credentials')return Response.json({youtube:{refresh_token_encrypted:encrypted}});updates.push(p.p_payload);return Response.json({});}
  if(url.includes('oauth2'))return Response.json({access_token:'fixture'});
  if(url.includes('cloudinary')){if(init?.method==='HEAD')return new Response(null,{headers:{'Content-Length':'10'}});assert.equal((init?.headers as any).Range,'bytes=5-9');return new Response(new Uint8Array(5),{status:206,headers:{'Content-Range':'bytes 5-9/10'}});}
  if(url.includes('/upload/youtube/')){puts++;if(puts===1)return new Response(null,{status:308,headers:{Range:'bytes=0-4'}});assert.ok(init?.body instanceof ReadableStream);assert.equal((init?.headers as any)['Content-Range'],'bytes 5-9/10');assert.equal((await new Response(init!.body).arrayBuffer()).byteLength,5);return Response.json({id:'remote-fixture',status:{privacyStatus:'public'}});}
  throw new Error('Unexpected request');
 };
 try{assert.equal((await publishUnit(env,'fixture')).status,'published');assert.equal(puts,2);assert.equal(updates[0].remote_id,'remote-fixture');assert.ok(requests.length<12);}finally{globalThis.fetch=original;}
});
test('already-completed YouTube probe never downloads or reuploads media bytes',async()=>{
 const original=globalThis.fetch;
 const encrypted=await encrypt('refresh-fixture',await fernetKey(undefined,'fixture','google'));
 globalThis.fetch=async(input,init)=>{
  const url=String(input);
  if(url.includes('/rpc/')){const p=JSON.parse(init!.body as string);if(p.p_action==='claim')return Response.json({publication:{status:'uploading',platform:'youtube',session_url:'https://www.googleapis.com/upload/youtube/v3/videos?upload_id=fixture'},video:{channel:'fixture',output:'https://res.cloudinary.com/fixture/video/upload/file.mp4'}});if(p.p_action==='credentials')return Response.json({youtube:{refresh_token_encrypted:encrypted}});return Response.json({});}
  if(url.includes('oauth2'))return Response.json({access_token:'fixture'});
  if(url.includes('cloudinary')){assert.equal(init?.method,'HEAD');return new Response(null,{headers:{'Content-Length':'10'}});}
  assert.equal((init?.headers as any)['Content-Range'],'bytes */10');return Response.json({id:'existing-fixture'});
 };
 try{assert.equal((await publishUnit(env,'fixture')).status,'published');}finally{globalThis.fetch=original;}
});
test('Instagram ambiguous commit is never repeated',async()=>{
 const original=globalThis.fetch;let calls=0;
 globalThis.fetch=async(input,init)=>{assert.ok(String(input).includes('/rpc/'));calls++;const p=JSON.parse(init!.body as string);return Response.json(p.p_action==='claim'?{publication:{status:'committing'},video:{}}:{});};
 try{assert.equal((await publishUnit(env,'fixture')).status,'uncertain');assert.equal(calls,2);}finally{globalThis.fetch=original;}
});
test('Instagram finished container commits once with direct Instagram endpoint',async()=>{
 const original=globalThis.fetch;let commits=0;const statuses:string[]=[];
 const encrypted=await encrypt('instagram-fixture',await fernetKey(undefined,'fixture','meta'));
 globalThis.fetch=async(input,init)=>{
  const url=String(input);
  if(url.includes('/rpc/')){const p=JSON.parse(init!.body as string);if(p.p_action==='claim')return Response.json({publication:{status:'uploading',platform:'instagram',session_url:'1234'},video:{channel:'fixture',output:'https://res.cloudinary.com/fixture/video/upload/file.mp4'}});if(p.p_action==='credentials')return Response.json({instagram:{token_encrypted:encrypted,login_mode:'instagram',token_expires_at:new Date(Date.now()+30*86400000).toISOString(),account_id:'5678'}});statuses.push(p.p_payload.status);return Response.json({});}
  assert.ok(url.startsWith('https://graph.instagram.com/'));assert.ok(!url.includes('facebook'));
  if(url.includes('media_publish')){commits++;assert.equal(statuses[0],'committing');assert.equal(String(init!.body),'creation_id=1234');return Response.json({id:'9012'});}
  return Response.json({status_code:'FINISHED'});
 };
 try{assert.equal((await publishUnit(env,'fixture')).status,'published');assert.equal(commits,1);assert.deepEqual(statuses,['committing','published']);}finally{globalThis.fetch=original;}
});
