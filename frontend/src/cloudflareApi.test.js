import {test} from 'node:test'
import assert from 'node:assert/strict'
import {cloudflareRequest,postCloudflare,publicationLocked,musicCropValid,safeMediaUrl} from './cloudflareApi.js'

test('publication controls block ambiguous, active and completed uploads',()=>{
  for(const status of ['uncertain','uploading','pending','published'])assert.equal(publicationLocked(status),true)
  for(const status of [undefined,'blocked','failed','remote_failed'])assert.equal(publicationLocked(status),false)
})
test('music crops remain inside source and include at least three seconds',()=>{
  assert.equal(musicCropValid(2,5,20),true)
  for(const args of [[-1,5,20],[0,2,20],[0,30,20],[NaN,5,20],[0,Infinity,20]])assert.equal(musicCropValid(...args),false)
})
test('media sources reject script schemes and unencrypted remote hosts',()=>{
  assert.equal(safeMediaUrl('javascript:alert(1)'), '')
  assert.equal(safeMediaUrl('http://example.com/media.mp4'), '')
  assert.equal(safeMediaUrl('https://res.cloudinary.com/demo/video/upload/a.mp4'),'https://res.cloudinary.com/demo/video/upload/a.mp4')
  assert.equal(safeMediaUrl(undefined),'')
})
test('request uses Cloudflare credential, preserves idempotency header and exposes errors',async()=>{
  const originalFetch=globalThis.fetch,originalStorage=globalThis.localStorage
  globalThis.localStorage={getItem:key=>key==='storyshorts.cloudflare.token'?'mock-cloudflare':null}
  try{
    globalThis.fetch=async(url,options)=>{
      assert.ok(url.endsWith('/videos/test/edits'))
      assert.equal(options.headers.Authorization,'Bearer mock-cloudflare')
      assert.equal(options.headers['Idempotency-Key'],'same-edit')
      assert.deepEqual(JSON.parse(options.body),{operation:'rerender',revision:0})
      return new Response(JSON.stringify({id:'edit'}),{status:202})
    }
    assert.deepEqual(await postCloudflare('/videos/test/edits',{operation:'rerender',revision:0},{headers:{'Idempotency-Key':'same-edit'}}),{id:'edit'})
    globalThis.fetch=async()=>new Response(JSON.stringify({error:'Stale preview'}),{status:409})
    await assert.rejects(cloudflareRequest('/videos/test'),e=>e.status===409&&e.message==='Stale preview')
    globalThis.fetch=async()=>new Response('<html>Unavailable</html>',{status:502})
    await assert.rejects(cloudflareRequest('/videos/test'),/invalid response/)
  }finally{globalThis.fetch=originalFetch;globalThis.localStorage=originalStorage}
})
