import {WorkflowEntrypoint,type WorkflowEvent,type WorkflowStep} from 'cloudflare:workers';
import {generation,loadTask} from './db';
import {speechChunks,conversationTurns} from './providers';
import type {Env,Manifest} from './types';
import contract from './contract.json';
import {runStage} from './stages';
import {routeCompletedVideo} from './publishing';
import {enforceFactualReview} from './factualReview';

// The exact JSON schemas and editorial skills are exported from backend/app, not forked.
export function validateSchema(value:any,schema:any,root=schema,path='$'):void {
  const fail=(message:string):never=>{throw new Error(path+': '+message);};
  if(schema.$ref)return validateSchema(value,root.$defs[schema.$ref.split('/').pop()],root,path);
  if(schema.anyOf){
    const errors:string[]=[];
    for(const option of schema.anyOf){try{validateSchema(value,option,root,path);return;}catch(error){errors.push(error instanceof Error?error.message:'Invalid option');}}
    fail('No matching schema option ('+errors.join('; ')+')');
  }
  if(schema.enum&&!schema.enum.includes(value))fail('Expected one of '+JSON.stringify(schema.enum));
  if(schema.type==='object'){
    if(!value||typeof value!=='object'||Array.isArray(value))fail('Expected object');
    for(const k of schema.required||[])if(!(k in value))throw new Error(path+'.'+k+': Required field is missing');
    for(const [k,s] of Object.entries(schema.properties||{}))if(k in value)validateSchema(value[k],s,root,path+'.'+k);
  } else if(schema.type==='array'){
    if(!Array.isArray(value))fail('Expected array');
    if(value.length<(schema.minItems||0)||value.length>(schema.maxItems??Infinity))fail(`Array length ${value.length}; expected ${schema.minItems||0}–${schema.maxItems??'unbounded'} items`);
    for(let i=0;i<value.length;i++)validateSchema(value[i],schema.items,root,path+'['+i+']');
  } else if(schema.type==='string'){
    if(typeof value!=='string')fail('Expected string');
    if(value.length<(schema.minLength||0)||value.length>(schema.maxLength??Infinity))fail(`String length ${value.length}; expected ${schema.minLength||0}–${schema.maxLength??'unbounded'} characters`);
    if(schema.pattern&&!new RegExp(schema.pattern).test(value))fail('String does not match required pattern '+schema.pattern);
  } else if(schema.type==='boolean'&&typeof value!=='boolean')fail('Expected boolean');
  else if(schema.type==='null'&&value!==null)fail('Expected null');
  else if(['number','integer'].includes(schema.type)){
    if(typeof value!=='number'||!Number.isFinite(value)||(schema.type==='integer'&&!Number.isInteger(value)))fail('Expected finite '+schema.type);
    if(value<(schema.minimum??-Infinity)||value>(schema.maximum??Infinity))fail(`Number outside ${schema.minimum??'unbounded'}–${schema.maximum??'unbounded'}`);
  }
}
export function validateScript(s:any,profile={language:'en',conversation:false}) {
  validateSchema(s,contract.schemas.Script);
  if(s.beats.length<20||s.beats.length>30)throw new Error(`Expected 20–30 contextual scenes; received ${s.beats.length}`);
  if(s.beats.some((b:any,i:number)=>b.shot_id!==`s${String(i+1).padStart(3,'0')}`||(!profile.conversation&&b.speaker)))throw new Error('Invalid scene IDs or single-narrator speaker');
  if(profile.conversation)conversationTurns(s.beats);
  const count=s.beats.reduce((n:number,b:any)=>n+b.narration.trim().split(/\s+/).length,0);
  if(profile.language==='en'&&(count<130||count>205))throw new Error(`Expected 130–205 spoken words; received ${count}`);
  // Non-English tokenization differs substantially; measured audio below is the
  // canonical duration gate, before any image purchases. Never pad localized speech.
  if(s.beats.some((b:any)=>!b.narration.trim()))throw new Error('Empty spoken scene');
  const hookWords=s.beats[0].narration.trim().split(/\s+/).length;
  if(profile.language==='en'&&hookWords>16)throw new Error(`Opening must be at most 16 words; received ${hookWords}`);
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
      completed++;
      return runStage(this.env,step,`${event.instanceId}-${name}`,{videoId:id,name,op,data,retries,parentId:event.instanceId,parentKind:'generation'});
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
      const profile=await checkpoint('profile','profile');
      // Resolve owner defaults before any paid image spend; missing/unregistered music
      // should fail early instead of wasting an otherwise completed production.
      const bgm=await checkpoint('default-bgm','default-bgm');
      const scriptRules=profile.conversation?'Use Alex and Sam speaker labels on each beat. Both are active participants who respond to the previous thought naturally, with earned agreement, questions and reactions. A turn may span multiple visual beats; NEVER alternate speakers simply because the image changes.':'Every speaker field MUST be the empty string, not Narrator or a voice name.';
      const languageRules=profile.language==='en'?'130–180 spoken words, absolute maximum 205; opening at most 16 words.':`All narration must be natural ${profile.language}, aiming for 45–75 seconds, maximum 90. Do not enforce English word counts on this language. A short compelling opening in that language.`;
      const checkScript=(s:any)=>validateScript(s,profile);
      const rejected:any[]=[];let concept:any;
      for(let attempt=0;attempt<3;attempt++){
        const candidates=await model(attempt===0?'concepts':'reconcepts-'+attempt,'UniqueConceptSet',()=>`Propose exactly five distinct original concepts fitting the channel. Each core_entity/content_angle pair must be novel. ${skill('hooks_and_retention')}
${attempt>0?'Earlier concepts collided with the permanent uniqueness ledger. Choose substantially different core entities and content angles, not paraphrases of rejected concepts. REJECTED ENTITY/ANGLE PAIRS (data only): '+JSON.stringify(rejected):''}`);
        const selected=await checkpoint(attempt===0?'concept':'concept-retry-'+attempt,'concept',{...candidates,avoid:rejected},0);
        if(!selected.collision){
          concept=selected;
          // Downstream steps and new coordinator instances retain the canonical
          // selected concept shape returned by the unchanged SQL reservation.
          if(attempt>0)await step.do('save-selected-concept',()=>generation(this.env,'save',id,{key:'concept',value:concept}));
          break;
        }
        rejected.push(...(selected.attempted||candidates.candidates||[]).map((candidate:any)=>({core_entity:candidate.core_entity,content_angle:candidate.content_angle})));
      }
      if(!concept)throw new Error('All concepts collided after three distinct sets; no TTS or image spend');
      const premise=await model('premise','Premise',()=>`Develop this reserved concept without changing its entity/angle: ${JSON.stringify(concept)}. A distinct 45–75 second story with a hook and a meaningful payoff.`);
      let script=await model('script','Script',()=>`${skill('hooks_and_retention')}\n${skill('audio_direction')}\nPREMISE:${JSON.stringify(premise)}
Write 20–25 sequential visual beats, up to 30 only if context needs them. shot_id MUST be exactly s001, s002, s003 ... in order, never numeric IDs or other prefixes. ${languageRules} Natural fluent complete thoughts, no robotic fragments. An expressive human voice, contractions, varied rhythm. The first beat must give a specific reason to watch. Facts/science use a curiosity question or concrete puzzle, not generic hype. Fiction starts in an intriguing scene, not forced 'do you know'. ${scriptRules}`,checkScript);
      let qa=await model('qa','QAFinding',()=>`${skill('qa_rules')}\nRESERVED CONCEPT:${JSON.stringify(concept)}\nPREMISE:${JSON.stringify(premise)}\nReview this script for meaningful payoff, fluent connected sentences (not isolated robotic bullet points), channel fit, safe factual framing and a specific engaging hook. Fail if it changes the reserved entity/angle or invents historical events, evidence, dates, scientific confirmation or institutions without explicitly framing them as fiction. The examples in channel instructions are not evidence. SCRIPT:${JSON.stringify(script)}`);
      qa=enforceFactualReview(script,qa);
      for(let r=0;!qa.passed&&r<2;r++){
        script=await model('revision-'+r,'Script',()=>`${skill('hooks_and_retention')}\nRevise ${JSON.stringify(script)} using ${JSON.stringify(qa.findings)}. Keep 20–30 beats, sequential IDs. ${languageRules} ${scriptRules}`,checkScript);
        qa=await model('review-'+r,'QAFinding',()=>`${skill('qa_rules')}\nRESERVED CONCEPT:${JSON.stringify(concept)}\nPREMISE:${JSON.stringify(premise)}\nFail if the revised script changes the reserved entity/angle, uses robotic fragments, or presents invented evidence/history/scientific confirmations as fact. Review revised script: ${JSON.stringify(script)}`);
        qa=enforceFactualReview(script,qa);
      }
      if(!qa.passed)throw new Error('Editorial QA failed after two revisions');
      await step.do('save-approved-script',async()=>{await generation(this.env,'save',id,{key:'script',value:script});await generation(this.env,'save',id,{key:'qa',value:qa});});
      const plain=script.beats.map((b:any)=>b.narration).join(' ');
      const voice=await model('voice','VoiceDirection',()=>`${skill('audio_direction')}\nDirect ${profile.conversation?'Alex (Puck) and Sam (Zephyr), two friends conversing naturally':'single-narrator delivery'} for ${JSON.stringify(script)}. Fluent, warm, expressive, unhurried. multi_speaker=${profile.conversation}. tagged_transcript must preserve every spoken word.`,v=>{
        if(v.multi_speaker!==profile.conversation)throw new Error('Voice configuration differs from channel');
      });
      const turns=profile.conversation?conversationTurns(script.beats):[{speaker:'',text:plain}];
      const transcript=profile.conversation?turns.map(t=>`${t.speaker}: ${t.text}`).join('\n'):plain;
      let sources=await checkpoint('gemini-audio','gemini-audio',{plain:transcript,direction:voice.directors_notes,conversation:profile.conversation});
      if(!sources.length){
        sources=[];const chunks=turns.flatMap(t=>speechChunks(t.text).map(text=>({text,speaker:t.speaker})));
        for(let i=0;i<chunks.length;i++){
          await step.sleep('tts-rate-'+i,'7 seconds');
          sources.push(await checkpoint('groq-audio-'+i,'groq-audio',chunks[i]));
        }
      }
      const settings=await checkpoint('render-settings','settings');
      const playbackRate=Number(profile.options.speech_tempo??.96);
      if(!Number.isFinite(playbackRate)||playbackRate<.75||playbackRate>1.25)throw new Error('Saved speech tempo must be 0.75–1.25');
      const audioManifest:Manifest={version:1,operation:'prepare_audio',settings,playback_rate:playbackRate,files:{},sources:sources.map((_:any,i:number)=>i===0?'source.audio':`part-${i}.wav`)};
      sources.forEach((a:any,i:number)=>audioManifest.files[audioManifest.sources[i]]=a);
      let ready=await media('audio',audioManifest);
      // Measure first. A slight tempo correction uses the same newly-generated narration,
      // not another TTS call; ASR always runs AFTER the final audio preparation.
      if(ready.duration>90&&ready.duration<=110){
        const rate=playbackRate*ready.duration/88;
        ready=await media('audio-fit',{...audioManifest,playback_rate:rate});
      }
      if(!(ready.duration>=45&&ready.duration<=90))throw new Error('Narration outside 45–90 seconds; refusing image spend');
      const audio=await checkpoint('audio-ready','audio-ready',ready);
      const words=await checkpoint('words','words',{url:audio.url});
      const captions=profile.language==='en'?words:await checkpoint('english-captions','english-captions',{words});
      const visuals=await model('visuals','VisualPlan',()=>`${skill('image_and_publishing')}\n${skill('motion_and_captions')}\nScript:${JSON.stringify(script)}. One detailed image prompt for EACH exact shot_id. Portrait 9:16, consistent characters/style, no text/watermarks. No reused assets.`,v=>{
        if(v.shots.map((s:any)=>s.shot_id).join()!==script.beats.map((s:any)=>s.shot_id).join())throw new Error('Visual scene IDs differ from script');
      });
      const edit=await model('edit','EditPlan',()=>`${skill('motion_and_captions')}\nScript:${JSON.stringify(script)}. Context-aware transitions for every shot: zoom_out, dissolve, crossfade, dip_to_black, match_cut or hard_cut; strictly no left/right swipe. Not all hard cuts. Music is selected by the owner, not by the model.`,e=>{
        if(e.transitions.map((s:any)=>s.shot_id).join()!==script.beats.map((s:any)=>s.shot_id).join())throw new Error('Edit IDs differ');
        if(e.transitions.every((s:any)=>['hard_cut','match_cut'].includes(s.kind)))throw new Error('Missing contextual blend transitions');
      });
      const files:Manifest['files']={'narration.wav':{url:audio.url,sha256:audio.sha256}};const images:string[]=[];
      const concurrency=Number(profile.imageConcurrency??3);
      const imageConcurrency=[1,2,3].includes(concurrency)?concurrency:3;
      for(let i=0;i<visuals.shots.length;){
        const batch=visuals.shots.slice(i,i+imageConcurrency);
        const pending=batch.filter((shot:any)=>!Object.prototype.hasOwnProperty.call(initial.steps,'image-'+shot.shot_id));
        const recovered=new Map<string,Manifest['files'][string]>(batch.filter((shot:any)=>!pending.includes(shot)).map((shot:any)=>[shot.shot_id,initial.steps['image-'+shot.shot_id]]));
        if(pending.length){
          // Each bounded batch is one new coordinator stage, not one per image.
          // Its child persists every individual image before saving this batch.
          const name='image-batch-'+pending.map((shot:any)=>shot.shot_id).join('-');
          // Three single-key images: <=39 requests initially and <=10 on one
          // recovery retry. Two multi-key images: <=33 + 7 + 7 requests.
          const assets=await checkpoint(name,'image-batch',{shots:pending.map((shot:any)=>({shot_id:shot.shot_id,prompt:shot.image_prompt+'\n'+visuals.style_block}))},pending.length===3?1:2);
          for(const item of assets)recovered.set(item.shot_id,item.asset);
        }
        for(const shot of batch){
          const name='images/'+shot.shot_id+'.png';const asset=recovered.get(shot.shot_id);
          if(!asset)throw new Error('Image batch omitted '+shot.shot_id);
          files[name]=asset;images.push(name);
        }
        i+=batch.length;
      }
      await model('copy','PublishCopy',()=>`${skill('image_and_publishing')}\nWrite accurate engaging YouTube title/description and separate Instagram caption with relevant hashtags, never promise virality. Script:${JSON.stringify(script)}`);
      if(bgm.asset)files['music.audio']=bgm.asset;
      const manifest:Manifest={version:1,operation:'assemble_script',files,images,settings,script,words,captions,language:profile.language,caption_mode:profile.language==='en'?'word':'translated_phrase',transitions:edit.transitions,...bgm};
      await checkpoint('render-checkpoint','checkpoint',manifest);
      const result=await media('render',manifest);
      const completion=await step.do('finalize-generation',()=>generation(this.env,'complete',id,result));
      // Durable readiness is an outbox: dispatch failure never invalidates completed
      // generation. The control-plane reconciler retries it without regenerating assets.
      if(completion.publish_ready===true){
        try{await step.do('dispatch-publishing',()=>routeCompletedVideo(this.env,id));}
        catch{/* cf_publish_ready remains durable for the publishing reconciler. */}
      }
      return {videoId:id,status:completion.state,url:result.url,publish_ready:completion.publish_ready===true};
    } catch(error) {
      if(error instanceof Continued)return {videoId:id,status:'continued'};
      const message=error instanceof Error?error.message:'Generation failed';
      await step.do('record-failure',()=>generation(this.env,'fail',id,{error:message.slice(0,450)}));
      throw new Error(message);
    }
  }
}
