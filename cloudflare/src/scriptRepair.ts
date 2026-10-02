// These are drafting instructions, never a programmatic waiver of the factual gate.
export const FINAL_NARRATION_RULES=`FINAL NARRATION SELF-CHECK: flagged_claims lists ONLY unsupported assertions still unresolved in the final spoken narration, not a warning history from older drafts. Read the final narration before returning JSON. Remove an entry only after genuinely deleting its unsupported assertion or expressly describing it as a noncurrent, not-currently-available, unproven hypothetical. Use [] when all such risks are genuinely resolved. Ordinary established explanatory mechanisms do not need flags. Never clear an entry while its assertion remains factual; adding "may" alone does not correct a claim of available technology. Do not invent precise figures, studies, institutions, historical evidence or experimental improvements. Delete a speculative practical application if needed and explain the established mechanism instead. The reserved core_concept and premise are creative proposals, NOT evidence: preserve the entity/angle, not invented supporting details. Narration contains spoken words only, with no square-bracket audio tags or stage directions; put delivery instructions in scene_note for the later voice director.`;

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
For dialogue count BOTH speakers together, not 150 words each. The FIRST beat is a complete,
self-contained engaging hook, up to 16 words in English, never an arbitrary 6–8-word fragment.
Remaining beat word counts are flexible: preserve fluent sentences across cuts, not a complete
sentence per beat or a rigid words-per-image quota. Every image must have spoken words. Never
end narration early and pad remaining beats with silence. ${FINAL_NARRATION_RULES} ${rules}`;
  const repair=previous ? `\nREPAIR REQUIRED (attempt ${attempt}/3): ${previous.error}
Previous scene count: ${previous.scene_count}. Rewrite the complete JSON script, preserving the
reserved entity/angle and natural narration, NOT unsupported details from the rejected draft or
premise. Delete old invented studies, institutions, quantitative improvements and speculative
applications rather than carrying them forward. Use exactly 25 visual beats, IDs s001 through s025,
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
      beat.properties.narration.description='Required spoken words only, no square-bracket audio tags or stage directions, never empty or silent filler. The first beat is a complete engaging hook (up to 16 words in English). Later beat lengths are flexible and a fluent sentence may span consecutive images; do not force isolated complete sentences or a fixed words-per-image quota.';
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
