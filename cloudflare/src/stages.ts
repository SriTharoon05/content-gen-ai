import {WorkflowEntrypoint,type WorkflowEvent,type WorkflowStep} from 'cloudflare:workers';
import {generation,createTask,loadTask,expire,triggered} from './db';
import {config,textModel,embedding,geminiSpeech,groqSpeech,transcribe,image,upload,generationProfile,englishCaptions} from './providers';
import {digest,validateManifest} from './storage';
import {validateSchema,validateScript} from './generation';
import contract from './contract.json';
import type {Env,Asset} from './types';
import {scriptWithRepair} from './scriptRepair';

export interface StageParams {videoId:string;name:string;op:string;data:any;retries:number;parentId?:string;parentKind?:'generation'|'media';}
export interface ImageBatchShot {shot_id:string;prompt:string;}
type ImageRequest=(env:Env,id:string,stage:string,c:any,prompt:string,options?:{maxPermitChecks:number;maxKeys:number})=>Promise<Asset>;
export function imageBatchConcurrency(c:any){
  const requested=Number(c.settings.runtime?.image_concurrency??3);
  const ownerLimit=[1,2,3].includes(requested)?requested:3;
  return Math.min(ownerLimit,(c.settings.keys?.pollinations?.length??0)>1?2:3);
}
export async function generateImageBatch(env:Env,id:string,shots:ImageBatchShot[],state:any,c:any,requestImage:ImageRequest=image){
  if(!Array.isArray(shots)||shots.length<1||shots.length>3||new Set(shots.map(s=>s.shot_id)).size!==shots.length||shots.some(s=>!/^s\d{3}$/.test(s.shot_id)||typeof s.prompt!=='string'||!s.prompt.trim()))throw new Error('Image batch requires one to three distinct scene IDs and prompts');
  const outcomes=await Promise.allSettled(shots.map(async shot=>{
    const key='image-'+shot.shot_id;
    const asset=Object.prototype.hasOwnProperty.call(state.steps||{},key)?state.steps[key]
      :await requestImage(env,id,key,c,shot.prompt,{maxPermitChecks:6,maxKeys:shots.length===3?1:4});
    // Same keys/ledger as the old standalone child. Persist successful siblings
    // even if another image fails, so continuation/recovery cannot rebuy them.
    if(!Object.prototype.hasOwnProperty.call(state.steps||{},key))await generation(env,'save',id,{key,value:asset});
    return {shot_id:shot.shot_id,name:'images/'+shot.shot_id+'.png',asset};
  }));
  for(let i=0;i<outcomes.length;i++)if(outcomes[i].status==='rejected'){
    const error=(outcomes[i] as PromiseRejectedResult).reason;
    throw new Error('image-'+shots[i].shot_id+': '+(error instanceof Error?error.message:'Image failed'));
  }
  return outcomes.map(result=>(result as PromiseFulfilledResult<{shot_id:string;name:string;asset:Asset}>).value);
}
export function stageEventTimeout(retries:number):`${number} minutes` {
  // A child attempt may run for 15 minutes. Two retries plus their backoff
  // cannot fit inside the previous fixed 16-minute parent deadline.
  return `${16*(Math.max(0,Math.min(3,Math.trunc(retries)))+1)} minutes`;
}
export async function runStage(env:Env,step:WorkflowStep,id:string,p:StageParams):Promise<any> {
  await step.do('start-'+p.name,async()=>{
    try{await env.GENERATION_STAGE.create({id,params:p});}
    catch(e){try{await(await env.GENERATION_STAGE.get(id)).status();}catch{throw e;}}
  });
  try {
    const event=await step.waitForEvent<any>('done-'+p.name,{type:'stage-'+p.name,timeout:stageEventTimeout(p.retries)});
    if(!event.payload.ok)throw new Error(p.name+': '+event.payload.error);
    return event.payload.value;
  } catch(error) {
    // One reconciliation lookup if callback delivery was lost; no high-frequency binding polling.
    const state=await step.do('reconcile-'+p.name,async()=>{
      // Checkpoints can commit before notification delivery fails. Recover them
      // even if the notification step made the child appear errored.
      if(!p.op.startsWith('media-')||p.op==='media-submit'){
        const saved=await generation(env,'status',p.videoId);
        if(Object.prototype.hasOwnProperty.call(saved.steps||{},p.name))return {status:'complete',output:saved.steps[p.name]};
      }
      const s=await(await env.GENERATION_STAGE.get(id)).status();return {status:s.status,output:s.output??null};
    });
    if(state.status==='complete')return state.output;
    throw error;
  }
}
function context(c:any) {
  const channel=c.channel;
  return `CHANNEL:${channel.name}. NICHE:${channel.niche}\nOWNER INSTRUCTIONS:${channel.strategy_json?.instructions??(contract.skills as any)['brand/'+channel.slug+'.md']??''}
STRATEGY:${JSON.stringify(channel.strategy_json)}\nOPTIONS:${JSON.stringify(channel.overrides_json)}
REQUESTED TOPIC:${JSON.stringify(c.options?.topic||'Choose an original topic within the niche')}
Original model-generated content. External fact checking disabled. Do not invent studies, current news, statistics or real-person quotes. Clearly frame fiction/speculation.
PRIOR CONCEPTS:${JSON.stringify(c.prior.slice(0,30))}`;
}
export class GenerationStageWorkflow extends WorkflowEntrypoint<Env,StageParams> {
  async run(event:WorkflowEvent<StageParams>,step:WorkflowStep) {
    const p=event.payload;
    const notify=async(payload:any)=>{
      if(!p.parentId)return;
      const binding=p.parentKind==='media'?this.env.MEDIA_WORKFLOW:this.env.GENERATION_WORKFLOW;
      await(await binding.get(p.parentId)).sendEvent({type:'stage-'+p.name,payload});
    };
    try {
    const value=await step.do('execute',{retries:{limit:p.retries,delay:'30 seconds',backoff:'exponential'},timeout:'15 minutes'},async()=>{
      const {videoId:id,name,op,data}=p;
      // These child stages each get their own Free-plan external-subrequest budget.
      if(op==='media-inspect'){await expire(this.env,id);const t=await loadTask(this.env,id);return {status:t.status,result:t.result_json,pipelineId:t.pipeline_id||''};}
      if(op==='media-trigger'){
        // Step retries/replays must not buy another CircleCI run after a known
        // accepted trigger, including the period before its worker claims the DB.
        const task=await loadTask(this.env,id);
        if(task.pipeline_id)return task.pipeline_id;
        if(task.status!=='queued')return null;
        const r=await fetch(`https://circleci.com/api/v2/project/${this.env.CIRCLECI_PROJECT_SLUG}/pipeline/run`,{
          method:'POST',headers:{'Circle-Token':this.env.CIRCLECI_TOKEN,'Content-Type':'application/json'},
          body:JSON.stringify({definition_id:this.env.CIRCLECI_PIPELINE_DEFINITION_ID,config:{branch:this.env.CIRCLECI_BRANCH},checkout:{branch:this.env.CIRCLECI_BRANCH},parameters:{render_task_id:id}}),signal:AbortSignal.timeout(60000)});
        if(!r.ok)throw new Error('CircleCI trigger HTTP '+r.status);
        const b=await r.json() as any;if(!b.id)throw new Error('Missing pipeline ID');await triggered(this.env,id,b.id);return b.id;
      }
      const state=await generation(this.env,'status',id);
      if(Object.prototype.hasOwnProperty.call(state.steps,name))return state.steps[name];
      let value:any;
      if(op==='media-submit'){
        const tid=(await digest(id+':'+data.name)).slice(0,32);
        await createTask(this.env,tid,id,validateManifest(data.manifest,this.env));
        try{await this.env.MEDIA_WORKFLOW.create({id:tid,params:{taskId:tid,notifyGeneration:p.parentId}});}
        catch(e){try{await(await this.env.MEDIA_WORKFLOW.get(tid)).status();}catch{throw e;}}
        value=tid;
      } else if(op==='audio-ready'){
        if(/^[a-f0-9]{64}$/.test(data.metrics?.sha256||''))value={...data,sha256:data.metrics.sha256};
        else {
        const r=await fetch(data.url);if(!r.ok)throw new Error('Prepared audio missing');
        const bytes=await r.arrayBuffer();value={...data,sha256:Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),x=>x.toString(16).padStart(2,'0')).join('')};
        }
      } else if(op==='checkpoint'){
        value=await upload(this.env,new TextEncoder().encode(JSON.stringify(data)),'checkpoint','json','application/json');
      } else {
        const c=await config(this.env,id);
        if(op==='model'){
          const schema=(contract.schemas as any)[data.schema];
          if(data.schema==='Script') {
            value=await scriptWithRepair(c,context(c)+'\n'+data.prompt,schema,
              state.steps[name+'-validation'],textModel,s=>validateScript(s,generationProfile(c)),
              value=>generation(this.env,'save',id,{key:name+'-validation',value}));
          } else {
            value=await textModel(c,context(c)+'\n'+data.prompt,schema);validateSchema(value,schema);
          }
          if(data.schema==='VoiceDirection'&&value.multi_speaker!==generationProfile(c).conversation)throw new Error('Voice configuration differs from channel');
          if(['VisualPlan','EditPlan'].includes(data.schema)){
            const expected=state.steps.script.beats.map((x:any)=>x.shot_id).join();
            const actual=(value.shots||value.transitions).map((x:any)=>x.shot_id).join();
            if(actual!==expected)throw new Error('Scene IDs differ from approved script');
            if(value.transitions?.every((x:any)=>['hard_cut','match_cut'].includes(x.kind)))throw new Error('Missing contextual blends');
          }
        } else if(op==='concept'){
          for(const candidate of data.candidates){const vector=await embedding(c,candidate.core_concept);
            const r=await generation(this.env,'reserve',id,{...candidate,embedding:vector});if(!r.collision){value=r;break;}}
          if(!value)throw new Error('All concepts collided; no media spend');
        } else if(op==='gemini-audio'){
          const a=await geminiSpeech(this.env,c,data.plain,data.direction,data.conversation===true);value=a?[a]:[];
        } else if(op==='groq-audio')value=await groqSpeech(this.env,c,data.text,data.speaker);
        else if(op==='words')value=await transcribe(c,data.url);
        else if(op==='english-captions')value=await englishCaptions(c,data.words);
        else if(op==='profile'){
          value={...generationProfile(c),imageConcurrency:imageBatchConcurrency(c)};
        }
        else if(op==='default-bgm'){
          const {options}=generationProfile(c);
          const track=c.music_track;
          if(!track||options.music_enabled===false)value={music:null,intensity:0,ducking:options.ducking??c.settings.music.ducking??true};
          else {
            if(!track.rights_cleared||track.archived)throw new Error('Default music must be active and rights cleared');
            const pct=Number(options.music_volume_pct??track.default_volume_pct??c.settings.music.default_volume_pct??20);
            if(!Number.isFinite(pct)||pct<0||pct>100)throw new Error('Music intensity must be 0–100%');
            // Browser registration hashes legacy/new music once; CircleCI verifies
            // bytes. Never hash an entire music file inside a 10ms Worker step.
            const {allowedMediaUrl}=await import('./storage');
            const url=allowedMediaUrl(track.path,this.env);
            const sha256=track.sha256;
            if(!/^[a-f0-9]{64}$/.test(sha256||''))throw new Error('Register the default music checksum in the Music library before generation');
            value={asset:{url,sha256},music:track,intensity:Math.min(.5,Math.max(0,Number(c.settings.music.max_intensity??.25)))*pct/100,ducking:options.ducking??c.settings.music.ducking??true,music_start:track.trim_start||0,music_end:track.trim_end??null};
          }
        }
        else if(op==='image')value=await image(this.env,id,name,c,data.prompt);
        else if(op==='image-batch')value=await generateImageBatch(this.env,id,data.shots,state,c);
        else if(op==='settings'){
          const s=c.settings;value={video:{...s.video,width:720,height:1280,fps:30},music:s.music,align:s.align,runtime:{ffmpeg_path:'ffmpeg',ffprobe_path:'ffprobe',low_memory_render:true}};
        } else throw new Error('Unknown generation stage');
      }
      await generation(this.env,'save',id,{key:name,value});return value;
    });
    await step.do('notify-success',()=>notify({ok:true,value}));return value;
    }catch(error){
      const message=error instanceof Error?error.message:'Stage failed';
      await step.do('notify-failure',()=>notify({ok:false,error:message}));throw error;
    }
  }
}
