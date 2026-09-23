import {WorkflowEntrypoint,type WorkflowEvent,type WorkflowStep} from 'cloudflare:workers';
import {generation,createTask,loadTask,expire,triggered} from './db';
import {config,textModel,embedding,geminiSpeech,groqSpeech,transcribe,image,upload} from './providers';
import {digest,validateManifest} from './storage';
import {validateSchema,validateScript} from './generation';
import contract from './contract.json';
import type {Env} from './types';

export interface StageParams {videoId:string;name:string;op:string;data:any;retries:number;parentId?:string;parentKind?:'generation'|'media';}
export async function runStage(env:Env,step:WorkflowStep,id:string,p:StageParams):Promise<any> {
  await step.do('start-'+p.name,async()=>{
    try{await env.GENERATION_STAGE.create({id,params:p});}
    catch(e){try{await(await env.GENERATION_STAGE.get(id)).status();}catch{throw e;}}
  });
  try {
    const event=await step.waitForEvent<any>('done-'+p.name,{type:'stage-'+p.name,timeout:'16 minutes'});
    if(!event.payload.ok)throw new Error(p.name+': '+event.payload.error);
    return event.payload.value;
  } catch(error) {
    // One reconciliation lookup if callback delivery was lost; no high-frequency binding polling.
    const state=await step.do('reconcile-'+p.name,async()=>{const s=await(await env.GENERATION_STAGE.get(id)).status();return {status:s.status,output:s.output??null};});
    if(state.status==='complete')return state.output;
    throw error;
  }
}
function context(c:any) {
  const channel=c.channel;
  return `CHANNEL:${channel.name}. NICHE:${channel.niche}\nOWNER INSTRUCTIONS:${channel.strategy_json?.instructions??(contract.skills as any)['brand/'+channel.slug+'.md']??''}
STRATEGY:${JSON.stringify(channel.strategy_json)}\nOPTIONS:${JSON.stringify(channel.overrides_json)}
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
      if(op==='media-inspect'){await expire(this.env,id);const t=await loadTask(this.env,id);return {status:t.status,result:t.result_json};}
      if(op==='media-trigger'){
        const r=await fetch(`https://circleci.com/api/v2/project/${this.env.CIRCLECI_PROJECT_SLUG}/pipeline/run`,{
          method:'POST',headers:{'Circle-Token':this.env.CIRCLECI_TOKEN,'Content-Type':'application/json'},
          body:JSON.stringify({definition_id:this.env.CIRCLECI_PIPELINE_DEFINITION_ID,config:{branch:this.env.CIRCLECI_BRANCH},checkout:{branch:this.env.CIRCLECI_BRANCH},parameters:{render_task_id:id}})});
        if(!r.ok)throw new Error('CircleCI trigger HTTP '+r.status);
        const b=await r.json() as any;if(!b.id)throw new Error('Missing pipeline ID');await triggered(this.env,id,b.id);return b.id;
      }
      const state=await generation(this.env,'status',id);
      if(Object.prototype.hasOwnProperty.call(state.steps,name))return state.steps[name];
      let value:any;
      if(op==='media-submit'){
        const tid=(await digest(id+':'+data.name)).slice(0,32);
        await createTask(this.env,tid,id,validateManifest(data.manifest,this.env));
        try{await this.env.MEDIA_WORKFLOW.create({id:tid,params:{taskId:tid}});}
        catch(e){try{await(await this.env.MEDIA_WORKFLOW.get(tid)).status();}catch{throw e;}}
        value=tid;
      } else if(op==='audio-ready'){
        const r=await fetch(data.url);if(!r.ok)throw new Error('Prepared audio missing');
        const bytes=await r.arrayBuffer();value={...data,sha256:Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),x=>x.toString(16).padStart(2,'0')).join('')};
      } else if(op==='checkpoint'){
        value=await upload(this.env,new TextEncoder().encode(JSON.stringify(data)),'checkpoint','json','application/json');
      } else {
        const c=await config(this.env,id);
        if(op==='model'){
          const schema=(contract.schemas as any)[data.schema];
          value=await textModel(c,context(c)+'\n'+data.prompt,schema);validateSchema(value,schema);
          if(data.schema==='Script')validateScript(value);
          if(data.schema==='VoiceDirection'&&value.multi_speaker)throw new Error('Single narrator required');
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
          const a=await geminiSpeech(this.env,c,data.plain,data.direction);value=a?[a]:[];
        } else if(op==='groq-audio')value=await groqSpeech(this.env,c,data.text);
        else if(op==='words')value=await transcribe(c,data.url);
        else if(op==='image')value=await image(this.env,id,name,c,data.prompt);
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
