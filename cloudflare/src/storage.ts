import {ApiError, type Env, type Manifest} from './types';

export async function digest(value: string, algorithm = 'SHA-256') {
  const bytes = await crypto.subtle.digest(algorithm, new TextEncoder().encode(value));
  return Array.from(new Uint8Array(bytes), b => b.toString(16).padStart(2, '0')).join('');
}

export function allowedMediaUrl(value: string, env: Env) {
  const u = new URL(value);
  if (u.protocol !== 'https:' || u.username || u.password || u.port) throw new ApiError(400, 'Invalid media URL');
  const cloud = u.hostname === 'res.cloudinary.com' && u.pathname.startsWith(`/${env.CLOUDINARY_CLOUD_NAME}/`);
  const supa = env.SUPABASE_URL && u.origin === new URL(env.SUPABASE_URL).origin && u.pathname.startsWith('/storage/v1/object/');
  if (!cloud && !supa) throw new ApiError(400, 'Media URL is outside configured storage accounts');
  return value;
}

export function validateManifest(m: Manifest, env: Env) {
  if (m.version !== 1 || !['assemble', 'remix', 'prepare_audio','assemble_script'].includes(m.operation)) throw new ApiError(400, 'Unsupported media operation');
  if (!m.files || Object.keys(m.files).length > 128) throw new ApiError(400, 'Invalid asset count');
  for (const [name, asset] of Object.entries(m.files)) {
    if (!name || name.startsWith('/') || /[\\:]/.test(name) || name.split('/').includes('..')) throw new ApiError(400, 'Unsafe asset path');
    if (!/^[a-f0-9]{64}$/.test(asset.sha256)) throw new ApiError(400, 'Missing asset checksum');
    if (asset.url) allowedMediaUrl(asset.url, env);
    else if (!asset.key || asset.key.startsWith('/') || asset.key.split('/').includes('..')) throw new ApiError(400, 'Missing asset location');
  }
  const required = m.operation === 'prepare_audio' ? ['source.audio',...(m.sources||[])] : ['narration.wav', ...(m.operation === 'remix' ? ['source.mp4'] : [...(m.operation==='assemble_script'?[]:['captions.ass']), ...(m.images || [])])];
  if (required.some(name => !m.files[name])) throw new ApiError(400, 'Required media input missing');
  if (['assemble','assemble_script'].includes(m.operation) && (!m.images?.length || m.images.length > 100)) throw new ApiError(400, 'Invalid scene count');
  if(m.operation==='assemble_script'&&(!m.words?.length||!m.script?.beats?.length))throw new ApiError(400,'Measured words and script required');
  if (m.operation !== 'prepare_audio' && (m.settings?.video?.width !== 720 || m.settings?.video?.height !== 1280 || m.settings?.video?.fps !== 30)) throw new ApiError(400, 'Expected canonical 720x1280, 30 FPS');
  // Never accept executable paths or a caller-supplied output target.
  m.settings = {...m.settings, runtime: {ffmpeg_path: 'ffmpeg', ffprobe_path: 'ffprobe', low_memory_render: true}};
  delete m.output_key;
  return m;
}

export async function resolveAssets(manifest: Manifest, env: Env) {
  const files: Record<string, any> = {};
  for (const [name, asset] of Object.entries(manifest.files)) {
    if (asset.url) {files[name] = {...asset, url: allowedMediaUrl(asset.url, env)}; continue;}
    const encoded = asset.key!.split('/').map(encodeURIComponent).join('/');
    const r = await fetch(`${env.SUPABASE_URL}/storage/v1/object/sign/assets/${encoded}`, {
      method: 'POST', headers: {'Authorization': `Bearer ${env.SUPABASE_KEY}`, apikey: env.SUPABASE_KEY, 'Content-Type': 'application/json'},
      body: JSON.stringify({expiresIn: 3600}),
    });
    if (!r.ok) throw new ApiError(502, `Legacy asset signing failed (${r.status})`);
    const body = await r.json() as {signedURL: string};
    const url = body.signedURL.startsWith('/object/') ? env.SUPABASE_URL + '/storage/v1' + body.signedURL : new URL(body.signedURL, env.SUPABASE_URL).href;
    files[name] = {...asset, url: allowedMediaUrl(url, env)};
  }
  return {...manifest, files};
}

export function outputTarget(env: Env, id: string, operation: string) {
  const audio = operation === 'prepare_audio';
  return {publicId: `${env.CLOUDINARY_PREFIX}/tasks/${id}/${audio ? 'narration' : 'final'}`, resourceType: 'video', extension: audio ? 'wav' : 'mp4'};
}

export async function signedUpload(env: Env, id: string, operation: string) {
  const target = outputTarget(env, id, operation);
  return uploadCapability(env,target.publicId,target.resourceType);
}

async function uploadCapability(env: Env, publicId: string, resourceType: string): Promise<{url: string; fields: Record<string,string>}> {
  const fields: Record<string, string> = {public_id: publicId, timestamp: String(Math.floor(Date.now()/1000)), overwrite: 'true', type: 'upload'};
  const source = Object.keys(fields).sort().map(k => `${k}=${fields[k]}`).join('&');
  return {url: `https://api.cloudinary.com/v1_1/${env.CLOUDINARY_CLOUD_NAME}/${resourceType}/upload`,
    fields: {...fields, api_key: env.CLOUDINARY_API_KEY, signature: await digest(source + env.CLOUDINARY_API_SECRET)}};
}

export async function assetUpload(env: Env, input: {kind: string; sha256: string; extension: string}) {
  if (!['image','audio','music','checkpoint'].includes(input.kind) || !/^[a-f0-9]{64}$/.test(input.sha256) || !/^[a-z0-9]{1,8}$/.test(input.extension)) throw new ApiError(400,'Invalid asset descriptor');
  const type=input.kind==='image'?'image':input.kind==='checkpoint'?'raw':'video';
  const publicId=`${env.CLOUDINARY_PREFIX}/assets/${input.kind}/${input.sha256}${type==='raw'?'.'+input.extension:''}`;
  return uploadCapability(env,publicId,type);
}

export async function verifyOutput(env: Env, id: string, operation: string) {
  const target = outputTarget(env, id, operation);
  const r = await fetch(`https://api.cloudinary.com/v1_1/${env.CLOUDINARY_CLOUD_NAME}/resources/video/upload/${encodeURIComponent(target.publicId)}`, {
    headers: {Authorization: 'Basic ' + btoa(`${env.CLOUDINARY_API_KEY}:${env.CLOUDINARY_API_SECRET}`)},
  });
  if (!r.ok) throw new ApiError(502, `Cloudinary output verification failed (${r.status})`);
  const asset = await r.json() as any;
  if (asset.public_id !== target.publicId || asset.format !== target.extension || asset.bytes < 2048) throw new ApiError(409, 'Invalid rendered output');
  return {url: allowedMediaUrl(asset.secure_url, env), bytes: asset.bytes, duration: asset.duration};
}
