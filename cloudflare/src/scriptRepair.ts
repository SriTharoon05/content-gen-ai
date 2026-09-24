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
  const value=await generate(selected,prompt+repair,schema);
  try{validate(value);return value;}
  catch(error){
    const message=error instanceof Error?error.message:'Invalid script';
    await save({attempts:attempt,scene_count:Array.isArray(value?.beats)?value.beats.length:null,
      error:message,candidate:value});
    throw new Error(`Script validation attempt ${attempt}/3: ${message}`);
  }
}
