// Disposable Docker/PostgreSQL only. No application credentials or production DB.
// Run: node cloudflare/sql/tests/008_news_rpc_race.cjs
const {spawn,spawnSync}=require('node:child_process');
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const assert=require('node:assert/strict');
const container=`cf-news-race-${process.pid}`;
function docker(args,input='') {
  const r=spawnSync('docker',args,{input,encoding:'utf8'});
  if(r.status!==0)throw new Error(r.stderr||r.stdout||`Docker failed: ${r.status}`);
  return r.stdout.trim();
}
function sql(input){return docker(['exec','-i',container,'psql','-XAt','-U','postgres','-v','ON_ERROR_STOP=1'],input);}
function sqlAsync(input){
  const p=spawn('docker',['exec','-i',container,'psql','-XAt','-U','postgres','-v','ON_ERROR_STOP=1']);
  let out='',err='';p.stdout.on('data',v=>out+=v);p.stderr.on('data',v=>err+=v);
  const done=new Promise((resolve,reject)=>{p.on('error',reject);p.on('close',code=>code?reject(new Error(err)):resolve(out));});
  p.stdin.end(input);return done;
}
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{
  try{
    docker(['run','--detach','--rm','--name',container,'-e','POSTGRES_PASSWORD=local-test','postgres:17-alpine']);
    for(let i=0;i<50;i++){
      const ready=spawnSync('docker',['exec',container,'pg_isready','-U','postgres']);
      if(ready.status===0)break;
      await sleep(100);
    }
    const migration=readFileSync(join(__dirname,'../008_news_rpc.sql'),'utf8');
    const suite=readFileSync(join(__dirname,'008_news_rpc_rollback.sql'),'utf8').replace(/\r\n/g,'\n');
    const fixtures=suite.split('DO $$\nDECLARE r jsonb; a jsonb; i integer;')[0];
    assert.ok(fixtures.length<suite.length,'Fixture marker missing');
    sql(fixtures.replace('\\ir ../008_news_rpc.sql',()=>migration)+'\nCOMMIT;');
    // Both variants were cached before the guard existed. Each caller has its
    // own channel and task ID. The first holds the shared admission lock while
    // the second RPC starts, proving the latter rechecks after waiting.
    sql(`INSERT INTO channels VALUES('other','Other',true);
      INSERT INTO cf_news_articles(id,url_key,title_key,article,published_at) VALUES
      ('a','one.example/fire','legacy-a','{"title":"Scientists Discover a Fire Amoeba That Defies Lifes Heat Limit - one.example","url":"https://one.example/fire","description":"Evidence","source":"One"}',now()),
      ('b','two.example/fire','legacy-b','{"title":"Scientists Discover a Fire Amoeba That Defies Lifes Heat Limit","url":"https://two.example/fire","description":"Evidence","source":"Two"}',now());
      UPDATE cf_news_articles SET title_key=lower(regexp_replace(btrim(article->>'title'),'[[:space:][:punct:]]+',' ','g'));`);
    const create=(id,channel,article)=>`SELECT cf_news('create','${id}',jsonb_build_object('channel','${channel}','source',(SELECT article FROM cf_news_articles WHERE id='${article}')));`;
    const first=sqlAsync(`BEGIN; SET application_name='news-race-holder';
      SELECT pg_advisory_xact_lock(hashtext('story-shorts:cf-news'));
      ${create('first','lorehush','a')} SELECT pg_sleep(3); COMMIT;`);
    let observed=false;
    for(let i=0;i<40;i++){
      if(sql("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE application_name='news-race-holder' AND wait_event='PgSleep');")==='t'){observed=true;break;}
      await sleep(50);
    }
    assert.ok(observed,'Did not observe first transaction holding admission lock');
    const second=sqlAsync(create('second','other','b'));
    const [a,b]=await Promise.all([first,second]);
    assert.match(a,/"id": "first"/);assert.match(b,/"status": 409/);
    assert.equal(sql('SELECT count(*) FROM cf_news_posts;'),'1');
    assert.match(sql("BEGIN ISOLATION LEVEL REPEATABLE READ; SELECT cf_news('ingest','','{\"articles\":[]}'); ROLLBACK;"),/READ COMMITTED/);
    console.log('PASS: concurrent cross-channel near-title create admits exactly one; stale snapshot fails closed.');
  }finally{spawnSync('docker',['stop',container],{stdio:'ignore'});}
})().catch(e=>{console.error(e);process.exitCode=1;});
