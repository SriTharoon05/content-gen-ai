import {test} from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {languageOptions,geminiVoiceOptions,speedDurationValid,analyticsTable} from './cloudflareDashboard.js'

test('dashboard language and voice options match the native validation contract',()=>{
  const admin=readFileSync(new URL('../../cloudflare/src/dashboardAdmin.ts',import.meta.url),'utf8')
  const languages=admin.match(/const languages='([^']+)'/)[1].split(' ')
  assert.deepEqual(languageOptions.map(item=>item.value),languages)
  for(const voice of geminiVoiceOptions)assert.ok(admin.includes(voice.value))
  assert.ok(languageOptions.some(item=>item.value==='ta'&&item.label==='Tamil'))
})

test('speed changes reject durations that would waste a render outside the supported range',()=>{
  assert.equal(speedDurationValid(60,.8),true)
  assert.equal(speedDurationValid(88.1,.75),false)
  assert.equal(speedDurationValid(50,1.25),false)
  for(const [duration,rate] of [[NaN,1],[60,0],[60,1.6]])assert.equal(speedDurationValid(duration,rate),false)
})

test('analytics renders native YouTube rows and Instagram totals without losing zero',()=>{
  assert.deepEqual(analyticsTable('youtube',{columnHeaders:[{name:'day'},{name:'views'}],rows:[['2026-10-01',3]]}),{columns:['day','views'],rows:[['2026-10-01',3]]})
  assert.deepEqual(analyticsTable('instagram',{data:[{name:'views',total_value:{value:0}},{name:'reach',values:[{value:2},{value:4}]},{name:'accounts_engaged',values:[]}]}).rows,[['views',0],['reach',6],['accounts engaged','Unavailable']])
})
