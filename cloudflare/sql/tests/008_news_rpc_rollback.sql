-- Run with psql in a disposable PostgreSQL database, never production:
-- psql -v ON_ERROR_STOP=1 -f cloudflare/sql/tests/008_news_rpc_rollback.sql
-- Fixtures and migration are rolled back together.
\set ON_ERROR_STOP on
BEGIN;
DO $$ BEGIN
  IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='anon') THEN CREATE ROLE anon; END IF;
  IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN CREATE ROLE authenticated; END IF;
  IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='service_role') THEN CREATE ROLE service_role; END IF;
END $$;
CREATE TABLE public.channels(slug text PRIMARY KEY,name text,enabled boolean);
CREATE TABLE public.meta_connections(channel_slug text,token_encrypted text,login_mode text,token_expires_at timestamptz);
CREATE TABLE public.app_settings(key text PRIMARY KEY,value_json jsonb);
CREATE TABLE public.videos(id text PRIMARY KEY,options_json jsonb,state text,updated_at timestamptz);
CREATE TABLE public.jobs(id text PRIMARY KEY,video_id text,status text,error text,updated_at timestamptz);
INSERT INTO public.channels VALUES ('lorehush','Lorehush',true);
INSERT INTO public.app_settings VALUES('runtime','{"keys":{"secret":"DO_NOT_LEAK"}}');
\ir ../008_news_rpc.sql
DO $$
DECLARE r jsonb; a jsonb; i integer;
BEGIN
  r:=public.cf_news('dashboard');
  ASSERT r#>>'{config,run_at}'='10:00';
  ASSERT r#>>'{usage,requests_today}'='0';
  ASSERT r::text NOT LIKE '%DO_NOT_LEAK%';
  ASSERT public.cf_news('provider_config')#>>'{settings,keys,secret}'='DO_NOT_LEAK';
  ASSERT NOT has_function_privilege('anon','public.cf_news(text,text,jsonb)','EXECUTE');
  ASSERT NOT has_function_privilege('authenticated','public.cf_news(text,text,jsonb)','EXECUTE');
  ASSERT has_function_privilege('service_role','public.cf_news(text,text,jsonb)','EXECUTE');
  ASSERT (SELECT bool_and(relrowsecurity) FROM pg_class WHERE oid IN
    ('public.cf_news_config'::regclass,'public.cf_news_usage'::regclass,'public.cf_news_articles'::regclass,'public.cf_news_posts'::regclass));
  ASSERT public.cf_news('config','','{"fetch_budget":201}')->>'status'='422';
  ASSERT public.cf_news('config','','{"secret":"bad"}')->>'status'='422';
  ASSERT public.cf_news('config','',jsonb_build_object('brand',repeat('x',36)))->>'status'='422';
  FOR i IN 1..6 LOOP ASSERT public.cf_news('reserve_fetch')->>'allowed'='true'; END LOOP;
  ASSERT public.cf_news('reserve_fetch')->>'allowed'='false';
  ASSERT public.cf_news('dashboard')#>>'{usage,requests_today}'='6';
  a:=jsonb_build_object('title','Test title','url','https://example.com/story?utm_source=test',
    'description','A useful description','published_at',now(),'source','Example','category','science');
  ASSERT public.cf_news('ingest','',jsonb_build_object('articles',jsonb_build_array(a)))->>'ingested'='1';
  ASSERT public.cf_news('ingest','',jsonb_build_object('articles',jsonb_build_array(a||'{"url":"https://example.com/story#fragment"}'::jsonb)))->>'ingested'='0';
  ASSERT jsonb_array_length(public.cf_news('candidates')->'articles')=1;
  INSERT INTO public.videos VALUES('test1','{"execution_backend":"cloudflare-generation"}','CF_GENERATING',now());
  INSERT INTO public.jobs VALUES('test1','test1','cf_generating','',now());
  ASSERT public.cf_news('request','test1','{"channel":"lorehush"}')->>'id'='test1';
  ASSERT public.cf_news('request_load','test1')->>'post_created'='false';
  ASSERT public.cf_news('request','test1','{"channel":"other"}')->>'status'='409';
  ASSERT public.cf_news('create','test1',jsonb_build_object('channel','lorehush','source',a||'{"url":"https://fake.example.com/story"}'::jsonb))->>'status'='422';
  r:=public.cf_news('create','test1',jsonb_build_object('channel','lorehush','source',a));
  ASSERT r->>'id'='test1',r::text;
  ASSERT public.cf_news('request_load','test1')->>'post_created'='true';
  ASSERT (SELECT state='NEWS_POST' AND options_json->>'execution_backend'='cloudflare-generation'
    AND options_json->>'news_post'='true' FROM public.videos WHERE id='test1');
  ASSERT public.cf_news('create','test1',jsonb_build_object('channel','lorehush','source',a))->>'id'='test1';
  ASSERT public.cf_news('create','test2',jsonb_build_object('channel','lorehush','source',a))->>'status'='409';
  ASSERT jsonb_array_length(public.cf_news('candidates')->'articles')=0;
  ASSERT public.cf_news('publish_claim','test1')->>'status'='409';
  ASSERT public.cf_news('save','test1','{"secret":true}')->>'status'='422';
  PERFORM public.cf_news('save','test1','{"status":"awaiting_approval","slides":[]}');
  ASSERT public.cf_news('approve','test1','{"revision":1}')->>'status'='409';
  PERFORM public.cf_news('save','test1','{"slides":[{"url":"https://cdn.example.com/slide.jpg"}]}');
  ASSERT (SELECT status='cf_done' FROM public.jobs WHERE id='test1');
  ASSERT public.cf_news('approve','test1','{"revision":2}')->>'status'='409';
  ASSERT public.cf_news('approve','test1','{"revision":1}')->>'status'='approved';
  ASSERT public.cf_news('save','test1','{"title":"changed"}')->>'status'='409';
  ASSERT public.cf_news('publish_claim','test1')->>'fresh'='true';
  ASSERT public.cf_news('publish_claim','test1')->>'fresh'='false';
  PERFORM public.cf_news('publication_patch','test1','{"parent_id":"parent","container_ids":["child"]}');
  ASSERT public.cf_news('load','test1')->>'parent_id'='parent';
  ASSERT public.cf_news('load','test1')->'container_ids'='["child"]'::jsonb;
  ASSERT public.cf_news('publication_patch','test1','{"status":"published","remote_id":"remote"}')->>'status'='published';
  ASSERT public.cf_news('publication_patch','test1','{"status":"approved"}')->>'status'='409';
  ASSERT public.cf_news('tick')->>'due'='false';
  ASSERT public.cf_news('request','selection-failed','{"channel":"lorehush"}')->>'id'='selection-failed';
  ASSERT public.cf_news('save','selection-failed','{"status":"failed","error":"Selection unavailable"}')->>'status'='failed';
  ASSERT public.cf_news('dashboard')->'posts' @> '[{"id":"selection-failed","status":"failed","error":"Selection unavailable"}]'::jsonb;
  ASSERT public.cf_news('tick')->>'remaining'='8';
  FOR i IN 3..20 LOOP
    ASSERT public.cf_news('request','request-'||i,'{"channel":"lorehush"}')->>'status'='queued';
  END LOOP;
  ASSERT public.cf_news('request','over-limit','{"channel":"lorehush"}')->>'status'='429';
  ASSERT public.cf_news('tick')->>'remaining'='0';
  -- Cache retains at most 1000 fresh items and prunes articles older than 30 days.
  SELECT jsonb_agg(jsonb_build_object('title','Story '||x,'url','https://example.com/'||x,
    'description','Useful facts','published_at',now())) INTO r FROM generate_series(1,1000) x;
  PERFORM public.cf_news('ingest','',jsonb_build_object('articles',r));
  ASSERT (SELECT count(*) FROM public.cf_news_articles)=1000;
  UPDATE public.cf_news_articles SET published_at=now()-interval '31 days';
  PERFORM public.cf_news('ingest','','{"articles":[]}');
  ASSERT (SELECT count(*) FROM public.cf_news_articles)=0;
END $$;
ROLLBACK;
