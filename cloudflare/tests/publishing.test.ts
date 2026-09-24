import {test} from 'node:test';
import assert from 'node:assert/strict';
import {decrypt,encrypt,fernetKey} from '../src/crypto';
import {publicationCopy,publishingRoute,routeCompletedVideo,trustedMedia,uploadUrl} from '../src/publishing';
import {oauthRoute,type PublishingEnv} from '../src/oauth';
const env={SUPABASE_URL:'https://fixture.supabase.co',SUPABASE_KEY:'fixture',CLOUDINARY_CLOUD_NAME:'fixture',GOOGLE_CLIENT_ID:'id',GOOGLE_CLIENT_SECRET:'secret',GOOGLE_REDIRECT_URI:'https://worker.test/auth/google/callback',META_APP_ID:'id',META_APP_SECRET:'secret',META_REDIRECT_URI:'https://worker.test/auth/meta/callback'} as PublishingEnv;
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
