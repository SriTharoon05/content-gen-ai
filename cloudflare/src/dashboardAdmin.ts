/** Native admin routes. Caller MUST authenticate ADMIN_TOKEN before dispatch. */
import contract from './contract.json';
import {rpc} from './db';
import {ApiError,type Env} from './types';

type Obj=Record<string,any>;
const defaults=contract.defaults as Obj;
const keyFields=['gemini_free','gemini_paid','gemini_audio_paid','pollinations','groq'];
const languages='en ta te ml kn hi mr bn gu pa ur ar id ms th vi fil ja ko cmn es pt fr de it nl pl ru tr uk'.split(' ');
const fail=(message:string):never=>{throw new ApiError(422,message);};
function object(v:any):v is Obj{return !!v&&typeof v==='object'&&!Array.isArray(v);}
function exact(v:any,allowed:string[],label:string){if(!object(v)||Object.keys(v).some(k=>!allowed.includes(k)))fail(`Invalid ${label} fields`);}
function str(v:any,max:number,min=0){if(typeof v!=='string'||v.trim().length<min||v.length>max||v.includes('\0'))fail('Invalid text value');return v.trim();}
function num(v:any,min:number,max:number,integer=false){if(typeof v!=='number'||!Number.isFinite(v)||v<min||v>max||(integer&&!Number.isInteger(v)))fail('Number outside supported range');}
function one(v:any,values:any[]){if(!values.includes(v))fail('Unsupported option');}
function strings(v:any,max:number,size=500){if(!Array.isArray(v)||v.length>max)fail('Invalid list');return v.map((x:any)=>str(x,size));}
function bool(v:any){if(typeof v!=='boolean')fail('Expected boolean');}
const overrides:Obj={publishing_mode:['settings','review','direct'],publish_platforms:['youtube','instagram'],primary_language:languages,
  speech_tempo:[.75,1.6],music_volume_pct:[0,100],min_shots:[20,30],max_shots:[20,30],target_seconds:[45,90]};
const overrideFields=[...Object.keys(overrides),'languages','voice','pace_note','tone','style_note','must_include','must_avoid','agent_directs_voice','music_enabled','music_track','ducking'];
export function validateChannel(input:any,create=false):Obj{
  exact(input,['name','tagline','niche','instructions','conversation','enabled','overrides','topic_seeds','voice_name',...(create?['slug']:[])],'channel');
  const p=structuredClone(input);
  if(create){if(typeof p.slug!=='string'||!/^[a-z][a-z0-9-]{1,63}$/.test(p.slug))fail('Invalid channel identifier');if(!('name'in p)||!('niche'in p))fail('Name and niche are required');}
  for(const [k,max,min] of [['name',120,1],['tagline',255,0],['niche',3000,1],['instructions',20000,0],['voice_name',80,0]] as const)if(k in p)p[k]=str(p[k],max,min);
  for(const k of ['enabled','conversation'])if(k in p)bool(p[k]);
  if('topic_seeds'in p)p.topic_seeds=strings(p.topic_seeds,100).filter(Boolean);
  if('overrides'in p){
    exact(p.overrides,overrideFields,'channel override');
    for(const [k,v] of Object.entries(p.overrides)){
      if(k==='languages'||k==='publish_platforms'){strings(v,30);for(const x of v as string[])one(x,k==='languages'?languages:['youtube','instagram']);}
      else if(['agent_directs_voice','music_enabled','ducking'].includes(k))bool(v);
      else if(overrides[k]){if(typeof overrides[k][0]==='number')num(v,overrides[k][0],overrides[k][1]);else one(v,overrides[k]);}
      else str(v,3000);
    }
    if(p.overrides.min_shots>p.overrides.max_shots)fail('Minimum shots exceeds maximum');
  }
  return p;
}
// Only known public fields are ever returned, even if older settings contain extra secrets.
function publicShape(value:any,template:any):any{
  if(Array.isArray(template))return Array.isArray(value)?structuredClone(value):structuredClone(template);
  if(object(template)){const out:Obj={};for(const k of Object.keys(template))out[k]=publicShape(value?.[k],template[k]);return out;}
  return typeof value===typeof template?value:template;
}
export function publicSettings(value:any):Obj{
  const out=publicShape(value,defaults);
  // Catalog objects are explicitly projected as arrays otherwise do not carry a schema.
  out.models.image_catalog=(out.models.image_catalog||[]).map((x:Obj)=>({id:x.id,label:x.label,credits:x.credits,rpm:x.rpm}));
  out.keys={};
  for(const k of keyFields){const v=value?.keys?.[k];out.keys[k]=k==='gemini_audio_paid'?(v?'__keep__':''):
    (Array.isArray(v)?v.map((_:unknown,i:number)=>`__keep__:${i}`):[]);}
  return out;
}
function typedPatch(p:any,t:any){
  exact(p,Object.keys(t),'settings');
  for(const k of Object.keys(p)){
    if(Array.isArray(t[k])){if(!Array.isArray(p[k])||p[k].length>100)fail('Invalid settings list');}
    else if(object(t[k]))typedPatch(p[k],t[k]);
    else if(typeof p[k]!==typeof t[k])fail('Invalid settings value type');
    else if(typeof p[k]==='number')num(p[k],-1e9,1e9);
    else if(typeof p[k]==='string')str(p[k],20000);
  }
}
function merge(a:Obj,b:Obj):Obj{const out=structuredClone(a);for(const k of Object.keys(b))out[k]=object(b[k])?merge(object(a[k])?a[k]:{},b[k]):b[k];return out;}
export function validateSettings(input:any,current:any):Obj{
  exact(input,[...Object.keys(defaults),'keys'],'settings');
  const p=structuredClone(input),nonsecret={...p};delete nonsecret.keys;
  // Schedule writes must use the ownership-guarded /api/schedule endpoint.
  if(p.schedule&&JSON.stringify(merge(current.schedule||defaults.schedule,p.schedule))!==JSON.stringify(current.schedule||defaults.schedule))fail('Save scheduling through /api/schedule');
  typedPatch(nonsecret,defaults);
  if(p.keys){exact(p.keys,keyFields,'credential');for(const k of Object.keys(p.keys)){
    if(p.keys[k]===null){delete p.keys[k];continue;}
    if(k==='gemini_audio_paid')str(p.keys[k],2048);else strings(p.keys[k],50,2048);
  }}
  const c=merge(publicSettings(current),p);
  one(c.models.text,['gemini-3.1-flash-lite']);one(c.models.text_fallback,['gemini-3.1-flash-lite']);one(c.models.text_tier,['free']);one(c.models.service_tier,['standard','flex']);one(c.models.thinking,['low','medium','high']);
  one(c.models.tts,['gemini-3.1-flash-tts-preview','gemini-2.5-flash-preview-tts','gemini-2.5-pro-preview-tts','canopylabs/orpheus-v1-english','canopylabs/orpheus-arabic-saudi']);
  one(c.models.image_endpoint,[defaults.models.image_endpoint]);one(c.align.groq_endpoint,[defaults.align.groq_endpoint]);one(c.align.provider,['groq']);one(c.align.groq_model,['whisper-large-v3','whisper-large-v3-turbo']);
  if(!c.models.image_catalog.length)fail('Image catalog is required');
  const ids=new Set();for(const item of c.models.image_catalog){exact(item,['id','label','credits','rpm'],'image model');str(item.id,150,1);str(item.label,150,1);num(item.credits,.0000001,10);if(item.rpm!==undefined)num(item.rpm,1,300,true);if(ids.has(item.id))fail('Duplicate image model');ids.add(item.id);}one(c.models.image_model,[...ids]);
  for(const [k,min,max] of [['min_shots',20,30],['max_shots',20,30],['min_seconds',45,90],['max_seconds',45,90],['target_seconds',45,90],['zoom_speed',0,.25],['zoom_max',0,.6],['min_shot_seconds',.3,4]] as const)num(c.video[k],min,max);
  if(c.video.min_shots>c.video.max_shots||c.video.min_seconds>c.video.max_seconds)fail('Invalid range');
  num(c.video.min_shots,20,30,true);num(c.video.max_shots,20,30,true);
  one(c.video.width,[720]);one(c.video.height,[1280]);one(c.video.fps,[30]);num(c.voice.speech_tempo,.75,1.6);
  for(const [k,min,max] of [['caption_y',0,1280],['caption_max_word_seconds',.1,5],['caption_min_word_seconds',.01,1],['lead_silence_ms',0,2000],['tail_silence_ms',0,2000]] as const)num(c.video[k],min,max);
  one(c.voice.default_voice,'Zephyr Puck Charon Kore Fenrir Leda Orus Aoede Callirrhoe Autonoe Enceladus Iapetus Umbriel Algieba Despina Erinome Algenib Rasalgethi Laomedeia Achernar Alnilam Schedar Gacrux Pulcherrima Achird Zubenelgenubi Vindemiatrix Sadachbia Sadaltager Sulafat'.split(' '));
  one(c.voice.groq_voice,['troy','austin','daniel','autumn','diana','hannah']);one(c.voice.groq_arabic_voice,['fahad','abdullah','sultan','lulwa','noura','aisha']);
  num(c.align.min_match_ratio,0,1);num(c.reuse.similarity_threshold,0,1);
  one(c.languages.primary,languages);for(const l of strings(c.languages.additional,30,10))one(l,languages);
  one(c.publishing.youtube_privacy,['private','unlisted','public']);if(!c.publishing.platforms.length)fail('Choose a publishing platform');for(const x of strings(c.publishing.platforms,2))one(x,['youtube','instagram']);
  num(c.music.default_volume_pct,0,100);num(c.music.max_intensity,.001,.5);strings(c.music.categories,100,80);
  num(c.music.duck_threshold,.0001,1);num(c.music.duck_ratio,1,20);
  one(c.runtime.agent_runtime,['roles']);num(c.runtime.worker_concurrency,1,8,true);num(c.runtime.image_concurrency,1,12,true);num(c.runtime.provider_retries,1,5,true);num(c.runtime.max_qa_rounds,0,3,true);
  num(c.runtime.ffmpeg_threads,1,16,true);num(c.runtime.http_timeout_seconds,10,600,true);
  one(c.runtime.ffmpeg_path,['ffmpeg']);one(c.runtime.ffprobe_path,['ffprobe']);
  for(const k of Object.keys(c.pricing))num(c.pricing[k],0,1e9);
  num(c.pricing.credit_price_usd,.000001,1e9);num(c.pricing.usd_to_inr,.000001,1e9);
  return p;
}
export function channelView(row:Obj):Obj{
  const strategy:Obj={},o:Obj={};
  for(const k of ['instructions','narrative','visual_style','voice','audience','voice_name'])if(typeof row.strategy?.[k]==='string')strategy[k]=row.strategy[k];
  if(typeof row.strategy?.conversation==='boolean')strategy.conversation=row.strategy.conversation;
  if(Array.isArray(row.strategy?.topic_seeds))strategy.topic_seeds=row.strategy.topic_seeds.filter((v:any)=>typeof v==='string');
  for(const k of overrideFields)if(Object.hasOwn(row.overrides||{},k)){
    try{validateChannel({overrides:{[k]:row.overrides[k]}});o[k]=row.overrides[k];}catch{/* Omit invalid/unknown legacy fields rather than reflecting nested secrets. */}
  }
  const instructions=typeof strategy.instructions==='string'?strategy.instructions:[(contract.skills as Obj)[`brand/${row.slug}.md`],...['narrative','visual_style','voice','audience'].map(k=>strategy[k]?`${k.replaceAll('_',' ')}: ${strategy[k]}`:'')].filter(Boolean).join('\n\n');
  // Native generation now handles primary-language narration and Alex/Sam conversations.
  // Additional-language variant fan-out is intentionally not advertised yet.
  const supported=!(o.languages?.length);
  return {slug:row.slug,name:row.name,tagline:row.tagline,niche:row.niche,enabled:row.enabled,
    strategy,overrides:o,stats:{videos:Number(row.stats?.videos)||0,ready:Number(row.stats?.ready)||0},
    instructions,supported,unsupported_reason:supported?'':'Additional-language variants are not yet supported by Cloudflare; select one primary language'};
}
export async function readAdminBody(request:Request,maxBytes=131072){
  if(Number(request.headers.get('Content-Length')||0)>maxBytes){await request.body?.cancel();throw new ApiError(413,'Request too large');}
  const reader=request.body?.getReader();if(!reader)throw new ApiError(400,'Invalid JSON');
  const chunks:Uint8Array[]=[];let total=0;
  try{for(;;){const {done,value}=await reader.read();if(done)break;total+=value.byteLength;
    if(total>maxBytes){await reader.cancel();throw new ApiError(413,'Request too large');}chunks.push(value);
  }}finally{reader.releaseLock();}
  const bytes=new Uint8Array(total);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.byteLength;}
  try{return JSON.parse(new TextDecoder('utf-8',{fatal:true,ignoreBOM:false}).decode(bytes));}catch{throw new ApiError(400,'Invalid JSON');}
}
const body=readAdminBody;
export async function handleDashboardAdmin(request:Request,env:Env):Promise<Response|null>{
  const path=new URL(request.url).pathname,method=request.method;
  const call=(action:string,id='',payload:Obj={})=>rpc(env,action,id,payload,'cf_admin');
  const reply=(data:any,status=200)=>Response.json(data,{status,headers:{'Cache-Control':'no-store'}});
  if(path==='/api/settings'){
    if(method==='GET'){const r=await call('settings_get');return reply({settings:publicSettings(r.settings)});}
    if(method==='PUT'){const b=await body(request);if(!object(b))fail('Expected settings object');if('settings'in b)exact(b,['settings'],'request');const current=await call('settings_get');const patch=validateSettings(b.settings??b,current.settings);const r=await call('settings_save','',{patch,revision:current.revision});return reply({settings:publicSettings(r.settings),saved:true});}
  }
  if(path==='/api/channels'){
    if(method==='GET'){const r=await call('channels');return reply({channels:r.channels.map(channelView)});}
    if(method==='POST'){const p=validateChannel(await body(request),true);const r=await call('channel_create',p.slug,p);return reply({channel:channelView(r.channel)},201);}
  }
  const match=path.match(/^\/api\/channels\/([a-z][a-z0-9-]{1,63})$/);
  if(match){
    if(method==='GET'){const r=await call('channel_get',match[1]);return reply({channel:channelView(r.channel)});}
    if(method==='PUT'){const r=await call('channel_update',match[1],validateChannel(await body(request)));return reply({channel:channelView(r.channel)});}
    if(method==='DELETE')return reply(await call('channel_delete',match[1]));
  }
  return null;
}
