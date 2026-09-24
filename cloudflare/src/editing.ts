import {WorkflowEntrypoint, type WorkflowEvent, type WorkflowStep} from 'cloudflare:workers';
import {rpc, createTask, loadTask, expire} from './db';
import {config, transcribe, englishCaptions} from './providers';
import {allowedMediaUrl, assetUpload, digest, validateManifest} from './storage';
import {ApiError, taskId, type Env, type Manifest} from './types';

export type EditParams={editId:string;prepared?:{url:string;sha256:string;duration:number};words?:any[]};
export type EditEnv = Env & {EDITING_WORKFLOW: Workflow<EditParams>};
export interface EditInput {operation:'rerender'|'speed'|'bgm'; revision:number; playback_rate?:number; music_track?:string|null; music_volume_pct?:number; music_start_seconds?:number; music_end_seconds?:number; ducking?:boolean}
const editRpc=(env:Env,action:string,id='',payload:any={})=>rpc(env,action,id,payload,'cf_edit');
export function validateEdit(value:any):EditInput {
  if(!value || !['rerender','speed','bgm'].includes(value.operation) || !Number.isSafeInteger(value.revision) || value.revision<0) throw new ApiError(400,'Operation and current preview revision required');
  if(value.operation==='speed' && (!Number.isFinite(value.playback_rate)||value.playback_rate<.75||value.playback_rate>1.25)) throw new ApiError(400,'Playback rate must be 0.75–1.25');
  if(value.music_volume_pct!==undefined && (!Number.isFinite(value.music_volume_pct)||value.music_volume_pct<0||value.music_volume_pct>100))throw new ApiError(400,'Music volume must be 0–100');
  for(const k of ['music_start_seconds','music_end_seconds']) if(value[k]!==undefined && (!Number.isFinite(value[k])||value[k]<0))throw new ApiError(400,'Invalid music crop');
  if(value.music_track && !/^[a-f0-9]{32}$/.test(value.music_track))throw new ApiError(400,'Invalid music track');
  if(value.ducking!==undefined && typeof value.ducking!=='boolean')throw new ApiError(400,'Invalid ducking selection');
  return Object.fromEntries(['operation','revision','playback_rate','music_track','music_volume_pct','music_start_seconds','music_end_seconds','ducking'].filter(k=>value[k]!==undefined).map(k=>[k,value[k]])) as unknown as EditInput;
}
export function cropBounds(duration:number,start=0,end=0) {
  end=end||duration;
  if(![duration,start,end].every(Number.isFinite)||start<0||end>duration+.05||end-start<3)throw new ApiError(400,'Choose at least 3 seconds inside the music duration');
  return [start,Math.min(end,duration)];
}
export function buildEditManifest(snapshot:any, input:EditInput):Manifest {
  const m:Manifest=structuredClone(snapshot.manifest);
  if(!m?.files?.['narration.wav']||!m.images?.length)throw new ApiError(409,'Saved narration and images are required; regenerate no assets automatically');
  const measured=Boolean(m.script&&m.words?.length);
  if(!measured && (input.operation==='speed'||!m.files['captions.ass']))throw new ApiError(409,'Saved script/alignment is unavailable for this legacy speed edit');
  m.operation=measured?'assemble_script':'assemble'; delete m.files['source.mp4'];
  if(input.operation==='bgm') {
    const track=snapshot.music;
    delete m.files['music.audio']; m.music=null;m.intensity=0;
    if(input.music_track) {
      if(!track?.sha256 || track.archived)throw new ApiError(409,'Music asset has no verified checksum or was removed');
      const [start,end]=cropBounds(track.duration_seconds,input.music_start_seconds??track.trim_start,input.music_end_seconds??track.trim_end);
      m.files['music.audio']={url:track.path,sha256:track.sha256};
      m.music={...track,path:'music.audio'};m.music_start=start;m.music_end=end;
      m.intensity=(input.music_volume_pct??track.default_volume_pct??30)/100*Math.min(.5,Number(m.settings.music?.max_intensity??.25));
    }
    m.ducking=input.ducking??true;
    // Older outputs lack a checksum: reassemble saved assets, never trust an unchecked MP4.
    if(snapshot.output_sha256 && /^[a-f0-9]{64}$/.test(snapshot.output_sha256)) {
      m.operation='remix';m.duration=snapshot.duration;
      m.files['source.mp4']={url:snapshot.output,sha256:snapshot.output_sha256};
    }
  }
  return m;
}

/** Caller MUST authenticate before delegating. null means route not handled. */
export async function handleEditing(request:Request,env:EditEnv):Promise<Response|null> {
  const path=new URL(request.url).pathname;const method=request.method;
  const json=(x:any,status=200)=>Response.json(x,{status});
  let match=path.match(/^\/api\/videos\/([a-f0-9]{32})\/edits$/);
  if(match&&method==='POST') {
    const input=validateEdit(await request.json());const key=request.headers.get('Idempotency-Key');
    if(!key||key.length>128)throw new ApiError(400,'Idempotency-Key required');
    const id=(await digest('edit:'+match[1]+':'+key)).slice(0,32);
    const state=await editRpc(env,'create',id,{video_id:match[1],input});
    if(!['succeeded','failed'].includes(state.status)) {
      try{await env.EDITING_WORKFLOW.create({id,params:{editId:id}});}
      catch(e){try{await(await env.EDITING_WORKFLOW.get(id)).status();}catch{throw e;}}
    }
    return json({id,status:state.status,revision:state.revision},202);
  }
  match=path.match(/^\/api\/edits\/([a-f0-9]{32})$/);
  if(match&&method==='GET')return json(await editRpc(env,'public',match[1]));
  if(path==='/api/music'&&method==='GET')return json(await editRpc(env,'music-list'));
  if(path==='/api/music/upload'&&method==='POST') {
    const body=await request.json() as any;
    return json(await assetUpload(env,{kind:'music',sha256:body.sha256,extension:body.extension}));
  }
  if(path==='/api/music'&&method==='POST') {
    const body=await request.json() as any;
    if(!/^[a-f0-9]{64}$/.test(body.sha256||'')||typeof body.name!=='string'||!body.name.trim()||body.name.length>160||!(/^[a-z0-9_-]{1,48}$/).test(body.category||'')||body.rights_cleared!==true)throw new ApiError(400,'Name, category, checksum and music rights confirmation required');
    const publicId=`${env.CLOUDINARY_PREFIX}/assets/music/${body.sha256}`;
    const response=await fetch(`https://api.cloudinary.com/v1_1/${env.CLOUDINARY_CLOUD_NAME}/resources/video/upload/${encodeURIComponent(publicId)}`,{headers:{Authorization:'Basic '+btoa(`${env.CLOUDINARY_API_KEY}:${env.CLOUDINARY_API_SECRET}`)}});
    if(!response.ok)throw new ApiError(409,'Upload music before adding it to the catalog');
    const asset=await response.json() as any;
    if(asset.public_id!==publicId||!Number.isFinite(asset.duration)||asset.bytes>25*1024*1024||!['mp3','wav','m4a','ogg','flac','aac'].includes(asset.format))throw new ApiError(400,'Unsupported music upload; maximum 25 MB');
    const [start,end]=cropBounds(asset.duration,Number(body.trim_start||0),Number(body.trim_end||0));
    const volume=Number(body.default_volume_pct??30);if(!Number.isFinite(volume)||volume<0||volume>100)throw new ApiError(400,'Invalid volume');
    const id=(await digest('music:'+body.sha256+':'+body.category+':'+start+':'+end)).slice(0,32);
    return json(await editRpc(env,'music-save',id,{name:body.name.trim(),category:body.category,path:allowedMediaUrl(asset.secure_url,env),sha256:body.sha256,duration_seconds:asset.duration,trim_start:start,trim_end:end,default_volume_pct:volume}),201);
  }
  match=path.match(/^\/api\/music\/([a-f0-9]{32})$/);
  if(match&&method==='DELETE')return json(await editRpc(env,'music-delete',match[1]));
  match=path.match(/^\/api\/music\/([a-f0-9]{32})\/checksum$/);
  if(match&&method==='POST') {
    // Browser hashes existing legacy audio once. CircleCI verifies bytes before using it.
    const body=await request.json() as any;
    allowedMediaUrl(body.path,env);
    if(!/^[a-f0-9]{64}$/.test(body.sha256||''))throw new ApiError(400,'Invalid checksum');
    return json(await editRpc(env,'music-checksum',match[1],{path:body.path,sha256:body.sha256}));
  }
  return null;
}

export class EditingWorkflow extends WorkflowEntrypoint<EditEnv,EditParams> {
  async run(event:WorkflowEvent<EditParams>,step:WorkflowStep) {
    const id=taskId(event.payload.editId);
    const dbRetry={retries:{limit:1,delay:'10 seconds' as const,backoff:'constant' as const}};
    const state=await step.do('claim-edit',dbRetry,()=>editRpc(this.env,'claim',id));
    if(state.status==='succeeded'||state.status==='failed')return {status:state.status};
    const media=async(name:string,manifest:Manifest)=>{
      const tid=(await digest(id+':'+name)).slice(0,32);
      await step.do(name+'-submit',dbRetry,async()=>{
        await createTask(this.env,tid,state.video_id,validateManifest(manifest,this.env));
        // Main integration adds notifyEditing to MediaWorkflow's parameter type/terminal notification.
        const binding=this.env.MEDIA_WORKFLOW as Workflow<{taskId:string;notifyEditing?:string}>;
        try{await binding.create({id:tid,params:{taskId:tid,notifyEditing:event.instanceId}});}
        catch(e){try{await(await binding.get(tid)).status();}catch{throw e;}}
        return tid;
      });
      // Callback completes immediately. Five hourly fallback checks cover the four-hour
      // media deadline; with one DB retry they cost <=20 external requests per instance.
      for(let n=0;n<5;n++){
        const row=await step.do(name+'-status-'+n,dbRetry,async()=>{await expire(this.env,tid);return loadTask(this.env,tid);});
        if(row.status==='succeeded')return {taskId:tid,...row.result_json,duration:row.result_json.duration??row.result_json.metrics?.duration};
        if(row.status==='failed')throw new Error(name+' media task failed; inspect CircleCI');
        if(n<4)try{await step.waitForEvent(name+'-wait-'+n,{type:'render-'+tid,timeout:'1 hour'});}catch{/* bounded DB reconciliation for missed callback */}
      }
      throw new Error('Media deadline exceeded');
    };
    try {
      let manifest=buildEditManifest(state.snapshot,state.input);
      if(state.input.operation==='speed'&&!event.payload.prepared) {
        const prepared=await media('audio',{version:1,operation:'prepare_audio',files:{'source.audio':manifest.files['narration.wav']},settings:manifest.settings,playback_rate:state.input.playback_rate});
        if(!prepared.metrics?.sha256)throw new Error('Update CircleCI media worker: prepared audio checksum required');
        if(!(prepared.duration>=45&&prepared.duration<=90))throw new Error('Selected speed produces narration outside 45–90 seconds; choose a closer speed');
        // Fresh instance resets the Free-plan 50-subrequest budget before ASR/final render.
        await step.do('continue-after-audio',async()=>{
          const next=id+'-render';const params={editId:id,prepared:{url:prepared.url,sha256:prepared.metrics.sha256,duration:prepared.duration}};
          try{await this.env.EDITING_WORKFLOW.create({id:next,params});}
          catch(e){try{await(await this.env.EDITING_WORKFLOW.get(next)).status();}catch{throw e;}}
        });
        return {status:'continued',editId:id};
      }
      if(state.input.operation==='speed'&&event.payload.prepared) {
        const prepared=event.payload.prepared;
        manifest.files['narration.wav']={url:allowedMediaUrl(prepared.url,this.env),sha256:prepared.sha256};
        // Only ASR is rerun. No script, TTS, image generation or model editorial calls.
        if(!event.payload.words) {
          const words=await step.do('align-changed-audio',{retries:{limit:2,delay:'30 seconds',backoff:'exponential'},timeout:'10 minutes'},async()=>transcribe(await config(this.env,state.video_id),prepared.url));
          await step.do('continue-after-alignment',async()=>{
            const next=id+'-aligned';const params={editId:id,prepared,words};
            try{await this.env.EDITING_WORKFLOW.create({id:next,params});}
            catch(e){try{await(await this.env.EDITING_WORKFLOW.get(next)).status();}catch{throw e;}}
          });
          return {status:'continued',editId:id};
        }
        manifest.words=event.payload.words;
        manifest.captions=manifest.language&&manifest.language!=='en'
          ? await step.do('translate-aligned-captions',{retries:{limit:2,delay:'30 seconds',backoff:'exponential'},timeout:'10 minutes'},async()=>englishCaptions(await config(this.env,state.video_id),manifest.words!))
          : manifest.words;
      }
      const result=await media('render',manifest);
      await step.do('complete-edit',dbRetry,()=>editRpc(this.env,'complete',id,{task_id:result.taskId}));
      return {status:'succeeded',url:result.url,revision:state.revision};
    }catch(e){
      await step.do('fail-edit',dbRetry,()=>editRpc(this.env,'fail',id,{error:e instanceof Error?e.message.slice(0,450):'Media editing failed'}));
      throw e;
    }
  }
}
