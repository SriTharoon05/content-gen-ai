import {test} from 'node:test';
import assert from 'node:assert/strict';
import {scriptWithRepair} from '../src/scriptRepair';
import contract from '../src/contract.json';

const config={settings:{keys:{gemini_free:['fake-gemini'],groq:['fake-groq']}}};
const validate=(value:any)=>{if(value.beats.length<20)throw new Error(`Expected 20–30 contextual scenes; received ${value.beats.length}`);};
test('invalid script persists count and next attempt repairs with Groq and concrete feedback',async()=>{
  let saved:any;
  await assert.rejects(scriptWithRepair(config,'reserved premise',{},null,async()=>({beats:[{}]}),validate,async v=>{saved=v;}),/attempt 1\/3/);
  assert.equal(saved.scene_count,1);
  const result=await scriptWithRepair(config,'reserved premise',{},saved,async(c,p)=>{
    assert.deepEqual(c.settings.keys.gemini_free,[]);
    assert.match(p,/received 1/);assert.match(p,/reserved premise/);
    return {beats:Array(25).fill({})};
  },validate,async()=>{throw new Error('Valid output must not be rejected');});
  assert.equal(result.beats.length,25);
  assert.equal(config.settings.keys.gemini_free.length,1);
});
test('three invalid attempts exhaust; fourth makes no provider call',async()=>{
  let saved:any;let calls=0;
  const generate=async()=>{calls++;return {beats:[]};};
  for(let i=0;i<3;i++)await assert.rejects(scriptWithRepair(config,'',{},saved,generate,validate,async v=>{saved=v;}));
  await assert.rejects(scriptWithRepair(config,'',{},saved,generate,validate,async()=>{}),/exhausted 3/);
  assert.equal(calls,3);
});
test('without Groq repair keeps configured Gemini keys',async()=>{
  const c={settings:{keys:{gemini_free:['fake']}}};
  await scriptWithRepair(c,'',{}, {attempts:1,error:'bad',scene_count:1},async selected=>{
    assert.equal(selected.settings.keys.gemini_free[0],'fake');return {beats:Array(25).fill({})};
  },validate,async()=>{});
});
test('constrained schema retains final-unresolved flag semantics and permits flexible spoken beats',async()=>{
  const original=structuredClone(contract.schemas.Script);
  const value={beats:Array(25).fill({}),flagged_claims:['Unsupported assertion remains factual']};
  const result=await scriptWithRepair(config,'writer prompt',contract.schemas.Script,null,async(_c,p,s)=>{
    assert.match(s.properties.flagged_claims.description,/final spoken narration/);
    assert.match(s.properties.flagged_claims.description,/Never clear/);
    assert.match(s.$defs.Beat.properties.narration.description,/first beat is a complete engaging hook/);
    assert.match(s.$defs.Beat.properties.narration.description,/Later beat lengths are flexible/);
    assert.match(p,/FIRST beat is a complete/);
    assert.match(p,/Remaining beat word counts are flexible/);
    assert.match(p,/flagged_claims lists ONLY unsupported assertions still unresolved/);
    assert.match(p,/not a warning history/);
    assert.match(p,/Use \[\] when all such risks are genuinely resolved/);
    assert.match(p,/Never clear an entry while its assertion remains factual/);
    assert.match(p,/Ordinary established explanatory mechanisms do not need flags/);
    return value;
  },validate,async()=>{throw new Error('No invalid output');});
  assert.deepEqual(contract.schemas.Script,original);
  // Prompt guidance must never automatically remove writer risks or bypass QA.
  assert.deepEqual(result.flagged_claims,value.flagged_claims);
});
test('repair may delete unsupported old premise details while preserving reserved entity and angle',async()=>{
  await scriptWithRepair(config,'reserved premise',{}, {attempts:1,error:'$.beats[0].narration: No tags',scene_count:25,candidate:{beats:[]}},async(_c,p)=>{
    assert.match(p,/preserving the\s+reserved entity\/angle/);
    assert.match(p,/NOT unsupported details from the rejected draft or\s+premise/);
    assert.match(p,/Delete old invented studies/);
    assert.match(p,/speculative\s+applications rather than carrying them forward/);
    return {beats:Array(25).fill({})};
  },validate,async()=>{});
});
