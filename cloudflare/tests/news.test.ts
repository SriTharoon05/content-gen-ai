import {test} from 'node:test';
import assert from 'node:assert/strict';
import {registerHooks} from 'node:module';
registerHooks({resolve(s,c,n){if(s==='cloudflare:workers')return {url:'data:text/javascript,export class WorkflowEntrypoint {}',shortCircuit:true};return n(s,c);}});
const {normalizeArticle,validateNewsCopy,fetchNews,newsRoute}=await import('../src/news');
const source={title:'Researchers report a new battery design',description:'A team has reported results from a laboratory trial.',link:'https://example.org/news?utm_source=feed',source_id:'example',pubDate:new Date().toISOString()};
test('news metadata preserves attribution and strips tracking, rejects unsafe or stale sources',()=>{
 const a=normalizeArticle(source);assert.equal(a?.url,'https://example.org/news');assert.equal(a?.source,'example');
 assert.equal(normalizeArticle({...source,link:'javascript:alert(1)'}),null);
 assert.equal(normalizeArticle({...source,description:null}),null);
 assert.equal(normalizeArticle({...source,pubDate:'2020-01-01 00:00:00'}),null);
});
test('news copy caps text and refuses detached highlights and oversized carousels',()=>{
 const good={title:'Battery design',caption:'Reported research.',image_prompt:'Conceptual battery illustration',slides:[{headline:'A new battery design',body:'Researchers reported a lab trial.',highlight:'battery'}]};
 assert.equal(validateNewsCopy(good),good);
 assert.throws(()=>validateNewsCopy({...good,slides:Array(4).fill(good.slides[0])}));
 assert.throws(()=>validateNewsCopy({...good,slides:[{...good.slides[0],highlight:'invented'}]}));
 assert.throws(()=>validateNewsCopy({...good,slides:[{...good.slides[0],body:''}]}));
});
test('news fetch budget is reserved before provider access and zero allowance makes no purchase',async()=>{
 const old=fetch;let provider=0;
 globalThis.fetch=async(url,init)=>{if(String(url).includes('newsdata')){provider++;throw Error('must not fetch');}const p=JSON.parse(init!.body as string);return Response.json(p.p_action==='dashboard'?{config:{categories:['science']}}:{allowed:false});};
 try{assert.deepEqual(await fetchNews({NEWSDATA_API_KEY:'fixture',SUPABASE_URL:'https://fixture.supabase.co',SUPABASE_KEY:'fixture'} as any),{cached:0});assert.equal(provider,0);}finally{globalThis.fetch=old;}
});
test('news run dispatches only a durable workflow, never publishes or selects synchronously',async()=>{
 const old=fetch;let created:any;
 globalThis.fetch=async()=>Response.json({id:'a'.repeat(32)});
 try{const r=await newsRoute(new Request('https://fixture/api/news/run',{method:'POST',headers:{'Idempotency-Key':'a'.repeat(32)},body:JSON.stringify({channel:'lorehush'})}),{SUPABASE_URL:'https://fixture.supabase.co',SUPABASE_KEY:'fixture',NEWS_WORKFLOW:{create:async(v:any)=>{created=v;}}} as any);assert.equal(r!.status,202);assert.equal(created.params.phase,'select');}finally{globalThis.fetch=old;}
});
