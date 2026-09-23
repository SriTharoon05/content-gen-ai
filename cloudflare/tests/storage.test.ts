import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {allowedMediaUrl,assetUpload,signedUpload,validateManifest,resolveAssets} from '../src/storage';
import {taskId,type Env,type Manifest} from '../src/types';
const env={CLOUDINARY_CLOUD_NAME:'test-cloud',CLOUDINARY_API_KEY:'test-key',CLOUDINARY_API_SECRET:'test-secret',CLOUDINARY_PREFIX:'pilot',SUPABASE_URL:'https://test.supabase.co',SUPABASE_KEY:'test-supa'} as Env;
const manifest=():Manifest=>({version:1,operation:'prepare_audio',settings:{},files:{'source.audio':{sha256:'a'.repeat(64),url:'https://res.cloudinary.com/test-cloud/video/upload/source.wav'}}});

test('legacy Supabase reads and own Cloudinary URLs allowed',()=>{
  assert.ok(allowedMediaUrl('https://test.supabase.co/storage/v1/object/public/assets/source.wav',env));
  assert.ok(allowedMediaUrl('https://res.cloudinary.com/test-cloud/video/upload/a.mp4',env));
});
test('external storage, account spoofing and plaintext rejected',()=>{
  for(const url of ['http://test.supabase.co/storage/v1/object/public/a','https://res.cloudinary.com/other/video/upload/a','https://test.supabase.co.evil.test/storage/v1/object/a','https://user:pass@test.supabase.co/storage/v1/object/a','https://localhost/a']) assert.throws(()=>allowedMediaUrl(url,env));
});
test('manifest validates checksums, paths and required inputs',()=>{
  assert.equal(validateManifest(manifest(),env).settings.runtime.ffmpeg_path,'ffmpeg');
  for(const name of ['../source.audio','/source.audio','C:\\source.audio']) {
    const m=manifest();m.files[name]=m.files['source.audio']; assert.throws(()=>validateManifest(m,env));
  }
  const m=manifest();m.files['source.audio'].sha256='invalid';assert.throws(()=>validateManifest(m,env));
});
test('renderer settings cannot specify executables or output paths',()=>{
  const m=manifest();m.settings.runtime={ffmpeg_path:'/malicious'};m.output_key='overwrite-other';
  validateManifest(m,env);assert.equal(m.settings.runtime.ffmpeg_path,'ffmpeg');assert.equal(m.output_key,undefined);
});
test('output upload is task-scoped and signature excludes API key',async()=>{
  const r=await signedUpload(env,'a'.repeat(32),'prepare_audio');
  assert.equal(r.url,'https://api.cloudinary.com/v1_1/test-cloud/video/upload');
  assert.equal(r.fields.public_id,`pilot/tasks/${'a'.repeat(32)}/narration`);
  const {signature,api_key,...fields}=r.fields;
  const expected=createHash('sha256').update(Object.keys(fields).sort().map(k=>`${k}=${fields[k]}`).join('&')+'test-secret').digest('hex');
  assert.equal(signature,expected);assert.equal(api_key,'test-key');
  assert.ok(!JSON.stringify(r).includes('test-secret'));
});
test('all new asset types write to Cloudinary',async()=>{
  for(const kind of ['image','audio','music','checkpoint']) {
    const r=await assetUpload(env,{kind,sha256:'a'.repeat(64),extension:'json'});
    assert.ok(r.url.startsWith('https://api.cloudinary.com/'));
    if(kind==='checkpoint') assert.ok(r.fields.public_id.endsWith('.json'));
  }
});
test('legacy keys are signed, not copied or rewritten',async()=>{
  const original=globalThis.fetch;
  globalThis.fetch=async()=>new Response(JSON.stringify({signedURL:'/object/sign/assets/old.wav?token=temporary'}));
  try {
    const m=manifest();m.files['source.audio']={sha256:'a'.repeat(64),key:'old.wav'};
    const result=await resolveAssets(m,env);
    assert.equal(result.files['source.audio'].url,'https://test.supabase.co/storage/v1/object/sign/assets/old.wav?token=temporary');
    assert.equal(m.files['source.audio'].url,undefined);
  } finally {globalThis.fetch=original;}
});
test('invalid task IDs rejected',()=>{
  assert.equal(taskId('a'.repeat(32)),'a'.repeat(32));
  assert.throws(()=>taskId('../job'));assert.throws(()=>taskId(''));
});
