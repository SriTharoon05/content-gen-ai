import {WorkflowEntrypoint,type WorkflowEvent,type WorkflowStep} from 'cloudflare:workers';
import {generation,createTask,loadTask} from './db';
import {config,textModel,embedding,geminiSpeech,groqSpeech,speechChunks,transcribe,image,upload} from './providers';
import {digest,validateManifest} from './storage';
import type {Env,Manifest} from './types';
import contract from './contract.json';

// The exact JSON schemas and editorial skills are exported from backend/app, not forked.
export function validateSchema(value:any,schema:any,root=schema):void {
  if(schema.$ref)return validateSchema(value,root.$defs[schema.$ref.split('/').pop()],root);
  if(schema.anyOf){if(!schema.anyOf.some((s:any)=>{try{validateSchema(value,s,root);return true;}catch{return false;}}))throw new Error('Schema union mismatch');return;}
  if(schema.enum&&!schema.enum.includes(value))throw new Error('Schema enum mismatch');
  if(schema.type==='object'){
    if(!value||typeof value!=='object'||Array.isArray(value))throw new Error('Expected object');
    for(const k of schema.required||[])if(!(k in value))throw new Error('Missing '+k);
    for(const [k,s] of Object.entries(schema.properties||{}))if(k in value)validateSchema(value[k],s,root);
  } else if(schema.type==='array'){
    if(!Array.isArray(value)||value.length<(schema.minItems||0)||value.length>(schema.maxItems??Infinity))throw new Error('Array bounds');
    for(const item of value)validateSchema(item,schema.items,root);
  } else if(schema.type==='string'){
    if(typeof value!=='string'||value.length<(schema.minLength||0)||value.length>(schema.maxLength??Infinity))throw new Error('String bounds');
  } else if(schema.type==='boolean'&&typeof value!=='boolean')throw new Error('Expected boolean');
  else if(schema.type==='null'&&value!==null)throw new Error('Expected null');
  else if(['number','integer'].includes(schema.type)){
    if(typeof value!=='number'||!Number.isFinite(value)||(schema.type==='integer'&&!Number.isInteger(value))||value<(schema.minimum??-Infinity)||value>(schema.maximum??Infinity))throw new Error('Number bounds');
  }
}
export function validateScript(s:any) {
  validateSchema(s,contract.schemas.Script);
  if(s.beats.length<20||s.beats.length>30)throw new Error('Use 20–30 contextual scenes');
  if(s.beats.some((b:any,i:number)=>b.shot_id!==`s${String(i+1).padStart(3,'0')}`||b.speaker))throw new Error('Invalid single-narrator scene IDs');
  const count=s.beats.reduce((n:number,b:any)=>n+b.narration.trim().split(/\s+/).length,0);
  if(count<130||count>205||s.beats[0].narration.split(/\s+/).length>16)throw new Error('Script pacing bounds');
}
function context(c:any) {
  const channel=c.channel;const skills=contract.skills as Record<string,string>;
  return `CHANNEL: ${channel.name}. NICHE: ${channel.niche}\nOWNER INSTRUCTIONS: ${channel.strategy_json?.instructions??skills['brand/'+channel.slug+'.md']??''}
STRATEGY: ${JSON.stringify(channel.strategy_json)}\nOPTIONS: ${JSON.stringify(channel.overrides_json)}
Original model-generated content. External fact checking is disabled. Do not invent studies, current news, statistics or real-person quotes. Clearly frame fiction/speculation.
PRIOR CONCEPTS TO AVOID: ${JSON.stringify(c.prior)}`;
}
const skill=(name:string)=>(contract.skills as Record<string,string>)[name+'.md']||'';

export class GenerationWorkflow extends WorkflowEntrypoint<Env,{videoId:string}> {
  async run(event:WorkflowEvent<{videoId:string}>,step:WorkflowStep) {
    const id=event.payload.videoId;
    // Secrets are fetched inside each step and never returned into Workflow state.
    const checkpoint=async(name:string,fn:(c:any)=>Promise<any>,retries=2):Promise<any>=>{
      await step.sleep('yield-'+name,'1 second');
      return step.do(name,{retries:{limit:retries,delay:'30 seconds',backoff:'exponential'},timeout:'15 minutes'},async()=>{
        const state=await generation(this.env,'status',id);
        if(Object.prototype.hasOwnProperty.call(state.steps,name))return state.steps[name];
        const value=await fn(await config(this.env,id));
        await generation(this.env,'save',id,{key:name,value});return value;
      });
    };
    const model=async(name:string,schemaName:keyof typeof contract.schemas,prompt:(c:any)=>string,check?:(x:any)=>void)=>checkpoint(name,async c=>{
      const value=await textModel(c,context(c)+'\n'+prompt(c),contract.schemas[schemaName]);
      validateSchema(value,contract.schemas[schemaName]);check?.(value);return value;
    });
    const media=async(name:string,manifest:Manifest):Promise<any>=>{
      const tid=await checkpoint(name+'-task',async()=>{
        const task=(await digest(id+':'+name)).slice(0,32);
        await createTask(this.env,task,id,validateManifest(manifest,this.env));
        try{await this.env.MEDIA_WORKFLOW.create({id:task,params:{taskId:task}});}
        catch(e){try{await(await this.env.MEDIA_WORKFLOW.get(task)).status();}catch{throw e;}}
        return task;
      });
      for(let n=0;n<120;n++){
        const t=await step.do(name+'-poll-'+n,async()=>{const t=await loadTask(this.env,tid);return {status:t.status,result:t.result_json};});
        if(t.status==='succeeded')return {...t.result,duration:t.result.duration??t.result.metrics?.duration};
        if(t.status==='failed')throw new Error(name+' media task failed; inspect CircleCI');
        await step.sleep(name+'-wait-'+n,'2 minutes');
      }
      throw new Error(name+' media deadline exceeded');
    };
    try {
      const candidates=await model('concepts','UniqueConceptSet',()=>`Propose exactly five distinct original concepts fitting the channel. Each core_entity/content_angle pair must be novel. ${skill('hooks_and_retention')}`);
      const concept=await checkpoint('concept',async c=>{
        for(const candidate of candidates.candidates){
          const vector=await embedding(c,candidate.core_concept);
          const r=await generation(this.env,'reserve',id,{...candidate,embedding:vector});if(!r.collision)return r;
        }
        throw new Error('All five concepts collided; no generation spend permitted');
      });
      const premise=await model('premise','Premise',()=>`Develop this reserved concept without changing its entity/angle: ${JSON.stringify(concept)}. A distinct 45–75 second story with a hook and a meaningful payoff.`);
      let script=await model('script','Script',()=>`${skill('hooks_and_retention')}\n${skill('audio_direction')}\nPREMISE:${JSON.stringify(premise)}
Write 20–25 sequential visual beats, up to 30 only if context needs them. shot_id MUST be exactly s001, s002, s003 ... in order, never numeric IDs or other prefixes. 130–180 spoken words, absolute maximum 205. Natural fluent complete thoughts, no robotic fragments. An expressive human voice, contractions, varied rhythm. The first beat must be at most 16 words and give a specific reason to watch. Facts/science use a curiosity question or concrete puzzle, not generic hype. Fiction starts in an intriguing scene, not forced 'do you know'. Every speaker field MUST be the empty string, not Narrator or a voice name.`,validateScript);
      let qa=await model('qa','QAFinding',()=>`${skill('qa_rules')}\nReview this script for meaningful payoff, natural spoken flow, channel fit, safe factual framing and a specific engaging hook. SCRIPT:${JSON.stringify(script)}`);
      for(let r=0;!qa.passed&&r<2;r++){
        script=await model('revision-'+r,'Script',()=>`${skill('hooks_and_retention')}\nRevise ${JSON.stringify(script)} using ${JSON.stringify(qa.findings)}. Keep 20–30 beats, 130–205 words, sequential IDs, hook <=16 words.`,validateScript);
        qa=await model('review-'+r,'QAFinding',()=>`${skill('qa_rules')}\nReview revised script: ${JSON.stringify(script)}`);
      }
      if(!qa.passed)throw new Error('Editorial QA failed after two revisions');
      await step.do('save-approved-script',async()=>{await generation(this.env,'save',id,{key:'script',value:script});await generation(this.env,'save',id,{key:'qa',value:qa});});
      const plain=script.beats.map((b:any)=>b.narration).join(' ');
      const voice=await model('voice','VoiceDirection',()=>`${skill('audio_direction')}\nDirect single-narrator delivery for ${JSON.stringify(script)}. Fluent, warm, expressive, unhurried. multi_speaker=false. tagged_transcript must preserve every spoken word.`,v=>{
        if(v.multi_speaker)throw new Error('Single narrator required');
      });
      let sources=await checkpoint('gemini-audio',async c=>{
        const audio=await geminiSpeech(this.env,c,plain,voice.directors_notes);return audio?[audio]:[];
      });
      if(!sources.length){
        sources=[];const chunks=speechChunks(plain);
        for(let i=0;i<chunks.length;i++){
          await step.sleep('tts-rate-'+i,'7 seconds');
          sources.push(await checkpoint('groq-audio-'+i,c=>groqSpeech(this.env,c,chunks[i])));
        }
      }
      const settings=await checkpoint('render-settings',async c=>{
        const s=c.settings;
        return {video:{...s.video,width:720,height:1280,fps:30},music:s.music,align:s.align,runtime:{ffmpeg_path:'ffmpeg',ffprobe_path:'ffprobe',low_memory_render:true}};
      });
      const audioManifest:Manifest={version:1,operation:'prepare_audio',settings,playback_rate:.96,files:{},sources:sources.map((_:any,i:number)=>i===0?'source.audio':`part-${i}.wav`)};
      sources.forEach((a:any,i:number)=>audioManifest.files[audioManifest.sources[i]]=a);
      let ready=await media('audio',audioManifest);
      // Measure first. A slight tempo correction uses the same newly-generated narration,
      // not another TTS call; ASR always runs AFTER the final audio preparation.
      if(ready.duration>90&&ready.duration<=110){
        const rate=.96*ready.duration/88;
        ready=await media('audio-fit',{...audioManifest,playback_rate:rate});
      }
      if(!(ready.duration>=45&&ready.duration<=90))throw new Error('Narration outside 45–90 seconds; refusing image spend');
      const audio=await checkpoint('audio-ready',async()=>{
        const r=await fetch(ready.url);if(!r.ok)throw new Error('Prepared audio missing');
        const bytes=await r.arrayBuffer();return {...ready,sha256:Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),x=>x.toString(16).padStart(2,'0')).join('')};
      });
      const words=await checkpoint('words',c=>transcribe(c,audio.url));
      const visuals=await model('visuals','VisualPlan',()=>`${skill('image_and_publishing')}\n${skill('motion_and_captions')}\nScript:${JSON.stringify(script)}. One detailed image prompt for EACH exact shot_id. Portrait 9:16, consistent characters/style, no text/watermarks. No reused assets.`,v=>{
        if(v.shots.map((s:any)=>s.shot_id).join()!==script.beats.map((s:any)=>s.shot_id).join())throw new Error('Visual scene IDs differ from script');
      });
      const edit=await model('edit','EditPlan',()=>`${skill('motion_and_captions')}\nScript:${JSON.stringify(script)}. Context-aware transitions for every shot: zoom_out, dissolve, crossfade, dip_to_black, match_cut or hard_cut; strictly no left/right swipe. Not all hard cuts. music_track=null for this fresh no-reuse test.`,e=>{
        if(e.transitions.map((s:any)=>s.shot_id).join()!==script.beats.map((s:any)=>s.shot_id).join())throw new Error('Edit IDs differ');
        if(e.transitions.every((s:any)=>['hard_cut','match_cut'].includes(s.kind)))throw new Error('Missing contextual blend transitions');
      });
      const files:Manifest['files']={'narration.wav':{url:audio.url,sha256:audio.sha256}};const images:string[]=[];
      for(const shot of visuals.shots){
        // Serial requests + >=2 seconds also fit 60 RPM; no burst per provider key.
        await step.sleep('image-rate-'+shot.shot_id,'2 seconds');
        const a=await checkpoint('image-'+shot.shot_id,c=>image(this.env,id,'image-'+shot.shot_id,c,shot.image_prompt+'\n'+visuals.style_block),0);
        const name='images/'+shot.shot_id+'.png';files[name]=a;images.push(name);
      }
      await model('copy','PublishCopy',()=>`${skill('image_and_publishing')}\nWrite accurate engaging YouTube title/description and separate Instagram caption with relevant hashtags, never promise virality. Script:${JSON.stringify(script)}`);
      const manifest:Manifest={version:1,operation:'assemble_script',files,images,settings,script,words,transitions:edit.transitions,intensity:0,ducking:true,music:null};
      await checkpoint('render-checkpoint',()=>upload(this.env,new TextEncoder().encode(JSON.stringify(manifest)),'checkpoint','json','application/json'));
      const result=await media('render',manifest);
      await step.do('finalize-review-only',()=>generation(this.env,'complete',id,result));
      return {videoId:id,status:'AWAITING_APPROVAL',url:result.url};
    } catch(error) {
      const message=error instanceof Error?error.message:'Generation failed';
      await step.do('record-failure',()=>generation(this.env,'fail',id,{error:message.slice(0,450)}));
      throw new Error(message);
    }
  }
}
