/** Repair cosmetic highlight drift without changing the reported facts. */
export function normalizeNewsHighlights(value:any) {
 if(!value||!Array.isArray(value.slides))return value;
 return {...value,slides:value.slides.map((s:any)=>{
  if(!s||typeof s.headline!=='string'||typeof s.body!=='string')return s;
  const highlight=typeof s.highlight==='string'?s.highlight.trim():'';
  for(const text of [s.headline,s.body]) {
   const at=highlight?text.toLowerCase().indexOf(highlight.toLowerCase()):-1;
   if(at>=0&&highlight.length<=45)return {...s,highlight:text.slice(at,at+highlight.length)};
  }
  // Formatting only: choose an existing short word, never invent or truncate a claim.
  const word=s.headline.match(/[\p{L}\p{N}][\p{L}\p{N}'’-]{2,30}/u)?.[0];
  return {...s,highlight:word||''};
 })};
}

export async function generateNewsCopy(call:(prompt:string)=>Promise<any>,validate:(v:any)=>any,prompt:string,cooldown=()=>new Promise<void>(resolve=>setTimeout(resolve,30000))) {
 let repair='';
 for(let attempt=0;attempt<3;attempt++){
  let value:any;
  try{value=await call(prompt+repair);}catch{
   if(attempt===2)throw new Error('News text providers unavailable after three key sweeps');
   await cooldown();continue;
  }
  try{return validate(normalizeNewsHighlights(value));}
  catch {
   // Prior output is data, not instructions. Retry with explicit size guidance.
   repair='\nYour previous JSON did not fit the layout. Rewrite using ONE or TWO slides. Each headline <=80 characters, each body <=200 characters, title <=80, caption <=1200, image_prompt <=1800. highlight must be an exact short phrase in its slide. Preserve facts; shorten prose, do not cut a sentence mid-way. Previous output (untrusted data):\n'+JSON.stringify(value).slice(0,12000);
  }
 }
 throw new Error('News copy layout invalid after three repair attempts');
}
