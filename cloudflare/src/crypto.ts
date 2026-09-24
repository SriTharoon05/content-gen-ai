// Python cryptography.fernet compatible: authenticate before AES-CBC decrypt.
const enc = new TextEncoder();
export const b64 = (b: Uint8Array) => btoa(String.fromCharCode(...b)).replace(/\+/g, '-').replace(/\//g, '_');
const un64 = (s: string) => Uint8Array.from(atob(s.replace(/-/g, '+').replace(/_/g, '/')), c => c.charCodeAt(0));
export const random = () => b64(crypto.getRandomValues(new Uint8Array(32))).replace(/=+$/, '');
export async function digest(s: string) { return [...new Uint8Array(await crypto.subtle.digest('SHA-256', enc.encode(s)))].map(x=>x.toString(16).padStart(2,'0')).join(''); }
export async function fernetKey(explicit: string|undefined, secret: string, provider: 'google'|'meta') {
  if (explicit) return un64(explicit);
  if (!secret) throw new Error('OAuth secret not configured');
  return new Uint8Array(await crypto.subtle.digest('SHA-256',enc.encode(`story-shorts:${provider==='google'?'oauth':'meta'}:${secret}`)));
}
export async function encrypt(value: string, key: Uint8Array) {
  if(key.length!==32) throw new Error('Invalid encryption key');
  const iv=crypto.getRandomValues(new Uint8Array(16));
  const aes=await crypto.subtle.importKey('raw',key.slice(16),'AES-CBC',false,['encrypt']);
  const cipher=new Uint8Array(await crypto.subtle.encrypt({name:'AES-CBC',iv},aes,enc.encode(value)));
  const data=new Uint8Array(25+cipher.length);data[0]=128;
  new DataView(data.buffer).setBigUint64(1,BigInt(Math.floor(Date.now()/1000)));
  data.set(iv,9);data.set(cipher,25);
  const hmac=await crypto.subtle.importKey('raw',key.slice(0,16),{name:'HMAC',hash:'SHA-256'},false,['sign']);
  const mac=new Uint8Array(await crypto.subtle.sign('HMAC',hmac,data));
  const token=new Uint8Array(data.length+32);token.set(data);token.set(mac,data.length);return b64(token);
}
export async function decrypt(token: string, key: Uint8Array) {
  const data=un64(token);
  if(key.length!==32||data.length<73||data[0]!==128)throw new Error('Invalid encrypted credential');
  const hmac=await crypto.subtle.importKey('raw',key.slice(0,16),{name:'HMAC',hash:'SHA-256'},false,['verify']);
  if(!await crypto.subtle.verify('HMAC',hmac,data.slice(-32),data.slice(0,-32)))throw new Error('Invalid encrypted credential');
  const aes=await crypto.subtle.importKey('raw',key.slice(16),'AES-CBC',false,['decrypt']);
  return new TextDecoder().decode(await crypto.subtle.decrypt({name:'AES-CBC',iv:data.slice(9,25)},aes,data.slice(25,-32)));
}
