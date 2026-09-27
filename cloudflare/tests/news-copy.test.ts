import {test} from 'node:test';
import assert from 'node:assert/strict';
import {normalizeNewsHighlights,generateNewsCopy} from '../src/news-copy';
test('highlight normalization repairs casing or missing phrase without changing facts',()=>{
 const v={slides:[{headline:'A New Battery',body:'A reported trial.',highlight:'battery'}]};
 assert.equal(normalizeNewsHighlights(v).slides[0].highlight,'Battery');
 v.slides[0].highlight='imaginary';
 const s=normalizeNewsHighlights(v).slides[0];assert.equal(s.headline,v.slides[0].headline);assert.equal(s.body,v.slides[0].body);assert.ok(s.headline.includes(s.highlight));
});
test('invalid copy gets actionable repair feedback and succeeds without more image calls',async()=>{
 let calls=0;
 const out=await generateNewsCopy(async p=>{calls++;if(calls===2)assert.ok(p.includes('Each headline <=80'));return {ok:calls===2};},v=>{if(!v.ok)throw Error('long');return v;},'facts');
 assert.equal(calls,2);assert.equal(out.ok,true);
});
test('repair terminates after three invalid outputs',async()=>{
 let calls=0;await assert.rejects(generateNewsCopy(async()=>{calls++;return {};},()=>{throw Error('invalid');},'facts'),/three repair/);assert.equal(calls,3);
});
test('provider exhaustion cools down twice and never exceeds three sweeps',async()=>{
 let calls=0,waits=0;
 await assert.rejects(generateNewsCopy(async()=>{calls++;throw Error('provider');},v=>v,'facts',async()=>{waits++;}),/three key sweeps/);
 assert.equal(calls,3);assert.equal(waits,2);
});
