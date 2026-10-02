import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {URL as NodeURL} from 'node:url';
import {Miniflare,convertV4MiniflareOptions} from 'miniflare';

test('image fetch options are accepted by the actual Cloudflare runtime',async()=>{
  const source=readFileSync(new NodeURL('../src/providers.ts',import.meta.url),'utf8');
  assert.ok(!/redirect\s*:\s*['"]error['"]/.test(source));
  const modes=[...source.matchAll(/redirect\s*:\s*['"]([^'"]+)['"]/g)].map(match=>match[1]);
  assert.ok(modes.length>=2);assert.ok(modes.every(mode=>mode==='manual'));
  // Workerd implements a narrower Request redirect enum than Node. Construct
  // the production options inside workerd; no network, secrets or purchases.
  const mf=new Miniflare(convertV4MiniflareOptions({modules:true,compatibilityDate:'2026-09-23',
    script:`export default {async fetch(){const modes=${JSON.stringify(modes)};return Response.json(modes.map(redirect=>new Request('https://example.test',{method:'POST',body:'{}',redirect,signal:AbortSignal.timeout(90000)}).redirect))}}`}));
  try{const response=await mf.dispatchFetch('http://localhost');assert.equal(response.status,200);assert.deepEqual(await response.json(),modes);}
  finally{await mf.dispose();}
});
