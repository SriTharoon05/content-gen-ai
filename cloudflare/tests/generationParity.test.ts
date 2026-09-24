import {test} from 'node:test';
import assert from 'node:assert/strict';
import {generationProfile,conversationTurns,captionGroups,translatedPhrases,englishCaptions,groqSpeech,geminiSpeech,transcribe} from '../src/providers';
import {scriptWithRepair} from '../src/scriptRepair';

test('saved language and per-run options override global defaults',()=>{
  const c={channel:{strategy_json:{conversation:true},overrides_json:{primary_language:'ta',music_volume_pct:20}},options:{music_volume_pct:30},settings:{languages:{primary:'en'}}};
  assert.deepEqual(generationProfile(c),{language:'ta',conversation:true,options:{primary_language:'ta',music_volume_pct:30}});
});
test('conversation turns follow explicit speakers, never visual alternation',()=>{
  assert.deepEqual(conversationTurns([{speaker:'Alex',narration:'Can you imagine?'},{speaker:'Alex',narration:'Here is why.'},{speaker:'Sam',narration:'Yeah, exactly!'}]),[{speaker:'Alex',text:'Can you imagine? Here is why.'},{speaker:'Sam',text:'Yeah, exactly!'}]);
  assert.throws(()=>conversationTurns([{speaker:'Alex',narration:'Only one'}]),/Both/);
  assert.throws(()=>conversationTurns([{speaker:'',narration:'Missing label'}]),/explicit/);
});
test('English subtitles retain measured source phrase boundaries, not invented word timestamps',()=>{
  const words=[{word:'வணக்கம்',start:.2,end:.8},{word:'நண்பா.',start:.85,end:1.4},{word:'அடுத்து',start:2,end:2.6}];
  const groups=captionGroups(words);
  assert.deepEqual(translatedPhrases(groups,{phrases:[{id:0,text:'Hello, friend.'},{id:1,text:'Next'}]}),[{word:'Hello, friend.',start:.2,end:1.4},{word:'Next',start:2,end:2.6}]);
  assert.throws(()=>translatedPhrases(groups,{phrases:[{id:0,text:'Hello'}]}),/omitted/);
  assert.throws(()=>translatedPhrases(groups,{phrases:[{id:1,text:'Hello'},{id:0,text:'Next'}]}),/Invalid/);
  assert.throws(()=>captionGroups([{word:'bad',start:3,end:2}]),/Invalid/);
});
test('English caption path remains a no-call pass-through',async()=>{
  const words=[{word:'Hello',start:0,end:1}];
  assert.equal(await englishCaptions({channel:{},settings:{}},words),words);
});
test('unsupported Groq TTS language fails before any network call',async()=>{
  await assert.rejects(groqSpeech({} as any,{channel:{overrides_json:{primary_language:'ta'}},settings:{}},'வணக்கம்'),/does not support ta/);
});
test('script repair preserves Tamil duo requirements instead of English solo constraints',async()=>{
  const c={channel:{overrides_json:{primary_language:'ta'},strategy_json:{conversation:true}},settings:{keys:{groq:['fake']}}};
  await scriptWithRepair(c,'original premise',{}, {attempts:1,error:'bad',scene_count:2},async(_,prompt)=>{
    assert.match(prompt,/Natural ta narration/);assert.match(prompt,/Alex\/Sam/);assert.doesNotMatch(prompt,/empty speaker fields|130–205 total/);
    return {};
  },()=>{},async()=>{});
});
test('Gemini duo sends canonical two-speaker voices and exact transcript, rotates failed key',async()=>{
  const original=globalThis.fetch;const seen:any[]=[];
  globalThis.fetch=async(url,init)=>{
    if(String(url).includes('generativelanguage')){
      const body=JSON.parse(String(init?.body));seen.push(body);
      if(seen.length===1)return new Response('{}',{status:503});
      return Response.json({candidates:[{content:{parts:[{inlineData:{data:Buffer.from([0,0,0,0]).toString('base64')}}]}}]});
    }
    return Response.json({secure_url:'https://res.cloudinary.com/test/video/upload/voice.wav'});
  };
  try{
    const c={channel:{overrides_json:{primary_language:'ta'}},settings:{keys:{gemini_free:['one','two']},voice:{}}};
    const result=await geminiSpeech({CLOUDINARY_CLOUD_NAME:'test',CLOUDINARY_API_KEY:'fake',CLOUDINARY_API_SECRET:'fake',CLOUDINARY_PREFIX:'test'} as any,c,'Alex: முதல் வரி\nSam: ஆம்','Warm',true);
    assert.ok(result?.sha256);assert.equal(seen.length,2);
    const cast=seen[1].generationConfig.speechConfig.multiSpeakerVoiceConfig.speakerVoiceConfigs;
    assert.deepEqual(cast.map((x:any)=>[x.speaker,x.voiceConfig.prebuiltVoiceConfig.voiceName]),[['Alex','Puck'],['Sam','Zephyr']]);
    assert.match(seen[1].contents[0].parts[0].text,/Alex: முதல் வரி\nSam: ஆம்/);
  }finally{globalThis.fetch=original;}
});
test('ASR sends source language, translation uses independent Groq keys and measured spans',async()=>{
  const original=globalThis.fetch;let requests=0;
  const words=Array.from({length:21},(_,i)=>({word:'சொல்.',start:i,end:i+.5}));
  globalThis.fetch=async(url,init)=>{
    if(String(url).includes('audio.wav'))return new Response(new Uint8Array([0,0]));
    if(String(url).includes('transcriptions')){
      assert.equal((init?.body as FormData).get('language'),'ta');
      return Response.json({words});
    }
    requests++;assert.ok(String(url).includes('api.groq.com'));
    if(requests===1)return new Response('{}',{status:429});
    return Response.json({choices:[{message:{content:JSON.stringify({phrases:words.map((_,id)=>({id,text:'Word.'}))})}}]});
  };
  try{
    const c={channel:{overrides_json:{primary_language:'ta'}},settings:{keys:{groq:['first','second'],gemini_free:['not-for-translation']}}};
    const heard=await transcribe(c,'https://test/audio.wav');
    const captions=await englishCaptions(c,heard);
    assert.equal(requests,2);assert.equal(captions[20].start,20);assert.equal(captions[20].end,20.5);
  }finally{globalThis.fetch=original;}
});
