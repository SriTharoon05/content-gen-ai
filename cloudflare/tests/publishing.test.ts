import {test} from 'node:test';
import assert from 'node:assert/strict';
import {decrypt,encrypt,fernetKey} from '../src/crypto';
import {publicationCopy,publishingRoute,routeCompletedVideo,trustedMedia,uploadUrl} from '../src/publishing';
import {oauthRoute,socialFetch,type PublishingEnv} from '../src/oauth';
const env={SUPABASE_URL:'https://fixture.supabase.co',SUPABASE_KEY:'fixture',CLOUDINARY_CLOUD_NAME:'fixture',GOOGLE_CLIENT_ID:'id',GOOGLE_CLIENT_SECRET:'secret',GOOGLE_REDIRECT_URI:'https://worker.test/auth/google/callback',META_APP_ID:'id',META_APP_SECRET:'secret',META_REDIRECT_URI:'https://worker.test/auth/meta/callback'} as PublishingEnv;
test('socialFetch forces Workers-compatible manual redirects for every caller mode',async()=>{
 const original=globalThis.fetch,response=Response.json({ok:true});let calls=0;
 const url='https://www.googleapis.com/youtube/v3/videos',headers={Authorization:'Bearer fixture'},body='fixture';
 globalThis.fetch=async(input,init)=>{calls++;assert.equal(input,url);assert.equal(init?.redirect,'manual');assert.equal(init?.method,'POST');assert.deepEqual(init?.headers,headers);assert.equal(init?.body,body);assert.ok(init?.signal instanceof AbortSignal);return response;};
 try{
  for(const redirect of [undefined,'error','follow','manual'] as const){
   assert.equal(await socialFetch(url,{method:'POST',headers,body,...(redirect?{redirect}:{})}),response);
  }
  assert.equal(calls,4);
 }finally{globalThis.fetch=original;}
});
test('socialFetch rejects 3xx with Location without following the redirect',async()=>{
 const original=globalThis.fetch;let response:Response,calls=0;
 globalThis.fetch=async(_input,init)=>{calls++;assert.equal(init?.redirect,'manual');return response;};
 try{
  for(const status of [300,301,302,303,304,307,308,399]){
   for(const location of ['https://evil.test/redirect','']){
    response=new Response(null,{status,headers:{Location:location}});
    const before=calls;
    await assert.rejects(()=>socialFetch('https://www.googleapis.com/upload/youtube/v3/videos',{headers:{Authorization:'Bearer fixture'}}),{status:502,message:'Unexpected social provider redirect'});
    assert.equal(calls,before+1);
   }
  }
 }finally{globalThis.fetch=original;}
});
test('socialFetch preserves YouTube 308 acknowledgements without Location',async()=>{
 const original=globalThis.fetch;
 try{
  for(const headers of [{},{Range:'bytes=0-1023'}] as Record<string,string>[]){
   const response=new Response(null,{status:308,headers});
   globalThis.fetch=async()=>response;
   const result=await socialFetch('https://www.googleapis.com/upload/youtube/v3/videos',{method:'PUT'});
   assert.equal(result,response);assert.equal(result.status,308);assert.equal(result.headers.get('Range'),headers.Range??null);
  }
 }finally{globalThis.fetch=original;}
});
test('socialFetch preserves successful upload initialization with Location',async()=>{
 const original=globalThis.fetch,location='https://www.googleapis.com/upload/youtube/v3/videos?upload_id=fixture';
 const response=new Response(null,{status:201,headers:{Location:location}});
 globalThis.fetch=async()=>response;
 try{const result=await socialFetch('https://www.googleapis.com/upload/youtube/v3/videos',{method:'POST'});assert.equal(result,response);assert.equal(result.headers.get('Location'),location);}finally{globalThis.fetch=original;}
});
test('Python Fernet fixture decrypts; tampering rejected; round trip',async()=>{
 const key=Uint8Array.from({length:32},(_,i)=>i);
 assert.equal(await decrypt('gAAAAABqtUUqaPZe88G1emJQjcZGwYGTCGNzy9SITuRlPNwuWkhOdjhSEGrlqexmazpd3fLtH51pQ13Yw1fECPf1wgMEfPLtsv6vusc026Px9pUVKcgEmkY=',key),'fixture-not-a-secret');
 const token=await encrypt('round trip',key);assert.equal(await decrypt(token,key),'round trip');
 await assert.rejects(()=>decrypt(token.slice(0,-5)+'AAAA=',key));
 assert.equal((await fernetKey(undefined,'secret','google')).length,32);
});
test('persisted media and upload session allowlists reject third party hosts',()=>{
 assert.ok(trustedMedia(env,'https://res.cloudinary.com/fixture/video/upload/file.mp4'));
 assert.throws(()=>trustedMedia(env,'https://res.cloudinary.com/other/video/upload/file.mp4'));
 assert.throws(()=>uploadUrl('https://evil.test/upload/youtube/v3/videos'));
 assert.ok(uploadUrl('https://www.googleapis.com/upload/youtube/v3/videos?upload_id=fixture'));
});
test('copy strips internal model label and caps captions',()=>{
 const copy=publicationCopy({title:'Story [flux.1-schnell]',description:'a'.repeat(6000),instagram_caption:'b'.repeat(3000),hashtags:['viral','History']});
 assert.equal(copy.title,'Story');assert.ok(copy.description.length<=5000);assert.ok(copy.caption.length<=2200);assert.deepEqual(copy.tags,['History']);
});
test('forced review completion never triggers workflow or provider',async()=>{
 const original=globalThis.fetch;let calls=0;
 globalThis.fetch=async()=>{calls++;return Response.json({options:{force_review:true,publishing_mode:'direct'},approved:false});};
 try{assert.equal((await routeCompletedVideo(env,'fixture')).status,'awaiting_approval');assert.equal(calls,1);}finally{globalThis.fetch=original;}
});
test('approval sends exact preview revision, connections redact DB secrets contract',async()=>{
 const original=globalThis.fetch;let payload:any;
 globalThis.fetch=async(_url,init)=>{payload=JSON.parse(init!.body as string);return Response.json({approved:true,state:'READY'});};
 try{const r=await publishingRoute(new Request('https://worker.test/api/videos/'+'a'.repeat(32)+'/approve',{method:'POST',body:JSON.stringify({output:'current.mp4'})}),env);assert.equal(r?.status,200);assert.deepEqual(payload.p_payload,{output:'current.mp4'});}finally{globalThis.fetch=original;}
});
test('Instagram start directs only to Instagram with independent browser cookie',async()=>{
 const original=globalThis.fetch;globalThis.fetch=async()=>Response.json({channel:'fixture'});
 try{const r=await oauthRoute(new Request('https://worker.test/auth/meta/start?ticket=fixture'),env);const u=new URL(r!.headers.get('Location')!);assert.equal(u.hostname,'www.instagram.com');assert.equal(u.searchParams.get('enable_fb_login'),'0');assert.ok(r!.headers.get('Set-Cookie')?.includes('HttpOnly'));assert.ok(!r!.headers.get('Set-Cookie')?.includes(u.searchParams.get('state')!));}finally{globalThis.fetch=original;}
});
test('OAuth callback without browser cookie stops before external calls',async()=>{
 await assert.rejects(()=>oauthRoute(new Request('https://worker.test/auth/google/callback?state=fixture&code=fixture'),env));
});
