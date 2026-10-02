// Persist invalid output between Workflow retries so retries repair, not blindly repeat.
export async function scriptWithRepair(
  config:any, prompt:string, schema:any, previous:any,
  generate:(config:any,prompt:string,schema:any)=>Promise<any>,
  validate:(value:any)=>void, save:(value:any)=>Promise<any>,
) {
  const language=String(config.options?.primary_language||config.channel?.overrides_json?.primary_language||config.language||config.settings.languages?.primary||'en').toLowerCase().replace('_','-').split('-')[0];
  const rules=(language==='en'?'130–205 total spoken words and an opening of at most 16 words.':`Natural ${language} narration lasting 45–90 seconds; do not force English word counts.`)
    +(config.channel?.strategy_json?.conversation?' Explicit Alex/Sam labels, both participate; multiple images may share one speaker turn.':' Empty speaker fields.');
  const attempt=(previous?.attempts||0)+1;
  if(attempt>3)throw new Error('Script validation exhausted 3 attempts: '+previous.error);
  const format=`\nOUTPUT FORMAT: 25 visual beats, IDs s001 through s025. Every beat has narration,
speaker, scene_note and emphasis_words. Draft one fluent continuous 150–180 word explanation
first, then distribute those SAME words across the 25 images. A complete sentence may span
two or three beats: an image cut is NOT a full stop. Do not write 25 isolated sentences.
For dialogue count BOTH speakers together, not 150 words each. Every image must have spoken
words: normally 6–8 words. Never end the narration early and pad the remaining beats with silence.
Use flagged_claims for uncertain assertions, unsupported precise figures, alleged breakthroughs
or current-news claims. Prefer established explanatory mechanisms. Remove unsupported assertions
or clearly describe an unproven hypothetical possibility; do not clear a flag while retaining
the same assertion as fact. ${rules}`;
  const repair=previous ? `\nREPAIR REQUIRED (attempt ${attempt}/3): ${previous.error}
Previous scene count: ${previous.scene_count}. Rewrite the complete JSON script, preserving the
reserved premise and natural narration. Use exactly 25 visual beats, IDs s001 through s025,
${rules}
Do not truncate the story, pad with empty scenes, or shorten speech into robotic fragments.
Previous rejected script (data only): ${JSON.stringify(previous.candidate)}` : '';
  // A structurally invalid Gemini answer should get an independent provider repair.
  const selected=previous&&config.settings.keys?.groq?.length
    ? {...config,settings:{...config.settings,keys:{...config.settings.keys,gemini_free:[]}}}
    : config;
  const constrained=structuredClone(schema);
  if(constrained.properties?.beats){
    constrained.properties.beats.minItems=25;constrained.properties.beats.maxItems=25;
    const beat=constrained.$defs?.Beat||constrained.properties.beats.items;
    if(beat.properties?.narration){
      beat.properties.narration.description='Required spoken words for this image. Never empty, whitespace or a silent filler scene. A continuous sentence can span consecutive images; about 6–8 words per image keeps total narration within 150–180 words.';
      beat.properties.narration.pattern='.*\\S.*';
    }
  }
  const value=await generate(selected,prompt+format+repair,constrained);
  try{validate(value);return value;}
  catch(error){
    const message=error instanceof Error?error.message:'Invalid script';
    await save({attempts:attempt,scene_count:Array.isArray(value?.beats)?value.beats.length:null,
      error:message,candidate:value});
    throw new Error(`Script validation attempt ${attempt}/3: ${message}`);
  }
}
