import test from 'node:test';
import assert from 'node:assert/strict';
import {validateSchedule} from '../src/scheduler';
const valid=()=>({owner:'cloudflare',schedule:{enabled:false,run_at:'10:00',videos_per_channel:2,timezone_offset_minutes:330,daily_credit_ceiling:.8,channels:['lorehush']}});
test('schedule validates full timezone/time/budget contract',()=>assert.deepEqual(validateSchedule(valid()),valid()));
test('schedule rejects missing fields and unsafe limits',()=>{
  for(const value of [{}, {...valid(),owner:'other'}, {...valid(),schedule:{...valid().schedule,run_at:'25:00'}},
    {...valid(),schedule:{...valid().schedule,daily_credit_ceiling:NaN}},
    {...valid(),schedule:{...valid().schedule,channels:['../x']}},
    {...valid(),schedule:{...valid().schedule,enabled:'true'}}])assert.throws(()=>validateSchedule(value));
});
