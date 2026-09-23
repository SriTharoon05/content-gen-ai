import {WorkflowEntrypoint,type WorkflowEvent,type WorkflowStep} from 'cloudflare:workers';
import {generation,loadTask} from './db';
import {speechChunks} from './providers';
import type {Env,Manifest} from './types';
import contract from './contract.json';
import {runStage} from './stages';

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
const skill=(name:string)=>(contract.skills as Record<string,string>)[name+'.md']||'';

class Continued extends Error {}
export class GenerationWorkflow extends WorkflowEntrypoint<Env,{videoId:string;segment?:number;runKey?:string}> {
  async run(event:WorkflowEvent<{videoId:string;segment?:number;runKey?:string}>,step:WorkflowStep) {
    const id=event.payload.videoId;
    const initial:any=await step.do('load-checkpoints',()=>generation(this.env,'status',id));
    let completed=0;
    // Secrets are fetched inside each step and never returned into Workflow state.
    const checkpoint=async(name:string,op:string,data:any={},retries=2):Promise<any>=>{
      if(Object.prototype.hasOwnProperty.call(initial.steps,name))return initial.steps[name];
      if(completed>=8){
        const segment=(event.payload.segment||0)+1;const runKey=event.payload.runKey||event.instanceId;
        await step.do('continue-generation',async()=>{
          const next=`${runKey}-p${segment}`;
          try{await this.env.GENERATION_WORKFLOW.create({id:next,params:{videoId:id,segment,runKey}});}
          catch(e){try{await(await this.env.GENERATION_WORKFLOW.get(next)).status();}catch{throw e;}}
        });
        throw new Continued();
      }
      const value=await runStage(this.env,step,`${event.instanceId}-${name}`,{videoId:id,name,op,data,retries,parentId:event.instanceId,parentKind:'generation'});
      completed++;return value;
    };
    const model=async(name:string,schemaName:keyof typeof contract.schemas,prompt:()=>string,check?:(x:any)=>void)=>{
      const value=await checkpoint(name,'model',{schema:schemaName,prompt:prompt()});check?.(value);return value;
    };
    const media=async(name:string,manifest:Manifest):Promise<any>=>{
      const tid=await checkpoint(name+'-task','media-submit',{name,manifest});
      for(let n=0;n<25;n++){
        const row:any=await step.do(name+'-inspect-'+n,()=>loadTask(this.env,tid));
        if(row.status==='succeeded')return {...row.result_json,duration:row.result_json.duration??row.result_json.metrics?.duration};
        if(row.status==='failed')throw new Error(name+' media task failed; inspect CircleCI');
        try{await step.waitForEvent(name+'-wait-'+n,{type:'render-'+tid,timeout:'10 minutes'});}
        catch{/* A missing callback recovers via one DB lookup per ten minutes. */}
      }
      throw new Error(name+' media deadline exceeded');
    };
    try {
      const candidates=await model('concepts','UniqueConceptSet',()=>`Propose exactly five distinct original concepts fitting the channel. Each core_entity/content_angle pair must be novel. ${skill('hooks_and_retention')}`);
      const concept=await checkpoint('concept','concept',candidates,0);
      const premise=await model('premise','Premise',()=>`Develop this reserved concept without changing its entity/angle: ${JSON.stringify(concept)}. A distinct 45–75 second story with a hook and a meaningful payoff.`);
      let script=await model('script','Script',()=>`${skill('hooks_and_retention')}\n${skill('audio_direction')}\nPREMISE:${JSON.stringify(premise)}
Write 20–25 sequential visual beats, up to 30 only if context needs them. shot_id MUST be exactly s001, s002, s003 ... in order, never numeric IDs or other prefixes. 130–180 spoken words, absolute maximum 205. Natural fluent complete thoughts, no robotic fragments. An expressive human voice, contractions, varied rhythm. The first beat must be at most 16 words and give a specific reason to watch. Facts/science use a curiosity question or concrete puzzle, not generic hype. Fiction starts in an intriguing scene, not forced 'do you know'. Every speaker field MUST be the empty string, not Narrator or a voice name.`,validateScript);
      let qa=await model('qa','QAFinding',()=>`${skill('qa_rules')}\nRESERVED CONCEPT:${JSON.stringify(concept)}\nPREMISE:${JSON.stringify(premise)}\nReview this script for meaningful payoff, fluent connected sentences (not isolated robotic bullet points), channel fit, safe factual framing and a specific engaging hook. Fail if it changes the reserved entity/angle or invents historical events, evidence, dates, scientific confirmation or institutions without explicitly framing them as fiction. The examples in channel instructions are not evidence. SCRIPT:${JSON.stringify(script)}`);
      for(let r=0;!qa.passed&&r<2;r++){
        script=await model('revision-'+r,'Script',()=>`${skill('hooks_and_retention')}\nRevise ${JSON.stringify(script)} using ${JSON.stringify(qa.findings)}. Keep 20–30 beats, 130–205 words, sequential IDs, hook <=16 words.`,validateScript);
        qa=await model('review-'+r,'QAFinding',()=>`${skill('qa_rules')}\nRESERVED CONCEPT:${JSON.stringify(concept)}\nPREMISE:${JSON.stringify(premise)}\nFail if the revised script changes the reserved entity/angle, uses robotic fragments, or presents invented evidence/history/scientific confirmations as fact. Review revised script: ${JSON.stringify(script)}`);
      }
      if(!qa.passed)throw new Error('Editorial QA failed after two revisions');
      await step.do('save-approved-script',async()=>{await generation(this.env,'save',id,{key:'script',value:script});await generation(this.env,'save',id,{key:'qa',value:qa});});
      const plain=script.beats.map((b:any)=>b.narration).join(' ');
      const voice=await model('voice','VoiceDirection',()=>`${skill('audio_direction')}\nDirect single-narrator delivery for ${JSON.stringify(script)}. Fluent, warm, expressive, unhurried. multi_speaker=false. tagged_transcript must preserve every spoken word.`,v=>{
        if(v.multi_speaker)throw new Error('Single narrator required');
      });
      let sources=await checkpoint('gemini-audio','gemini-audio',{plain,direction:voice.directors_notes});
      if(!sources.length){
        sources=[];const chunks=speechChunks(plain);
        for(let i=0;i<chunks.length;i++){
          await step.sleep('tts-rate-'+i,'7 seconds');
          sources.push(await checkpoint('groq-audio-'+i,'groq-audio',{text:chunks[i]}));
        }
      }
      const settings=await checkpoint('render-settings','settings');
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
      const audio=await checkpoint('audio-ready','audio-ready',ready);
      const words=await checkpoint('words','words',{url:audio.url});
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
        if(!Object.prototype.hasOwnProperty.call(initial.steps,'image-'+shot.shot_id)&&completed<8)
          await step.sleep('image-rate-'+shot.shot_id,'2 seconds');
        // Retries read the DB checkpoint/settled ledger before making any new purchase.
        // An uncertain reservation still fails closed; a post-save platform failure can recover.
        const a=await checkpoint('image-'+shot.shot_id,'image',{prompt:shot.image_prompt+'\n'+visuals.style_block},2);
        const name='images/'+shot.shot_id+'.png';files[name]=a;images.push(name);
      }
      await model('copy','PublishCopy',()=>`${skill('image_and_publishing')}\nWrite accurate engaging YouTube title/description and separate Instagram caption with relevant hashtags, never promise virality. Script:${JSON.stringify(script)}`);
      const manifest:Manifest={version:1,operation:'assemble_script',files,images,settings,script,words,transitions:edit.transitions,intensity:0,ducking:true,music:null};
      await checkpoint('render-checkpoint','checkpoint',manifest);
      const result=await media('render',manifest);
      await step.do('finalize-review-only',()=>generation(this.env,'complete',id,result));
      return {videoId:id,status:'AWAITING_APPROVAL',url:result.url};
    } catch(error) {
      if(error instanceof Continued)return {videoId:id,status:'continued'};
      const message=error instanceof Error?error.message:'Generation failed';
      await step.do('record-failure',()=>generation(this.env,'fail',id,{error:message.slice(0,450)}));
      throw new Error(message);
    }
  }
}
