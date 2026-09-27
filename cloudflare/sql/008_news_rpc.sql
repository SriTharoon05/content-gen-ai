-- Standalone news storage/API. Does not create or modify bridge videos.
CREATE TABLE IF NOT EXISTS public.cf_news_config (
  singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
  config jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS public.cf_news_usage (
  utc_day date PRIMARY KEY,
  requests integer NOT NULL DEFAULT 0 CHECK (requests >= 0)
);
CREATE TABLE IF NOT EXISTS public.cf_news_articles (
  id text PRIMARY KEY,
  url_key text NOT NULL UNIQUE,
  title_key text NOT NULL UNIQUE,
  article jsonb NOT NULL,
  published_at timestamptz NOT NULL,
  cached_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.cf_news_posts (
  id text PRIMARY KEY,
  channel text NOT NULL,
  source jsonb NOT NULL,
  url_key text NOT NULL UNIQUE,
  title_key text NOT NULL UNIQUE,
  status text NOT NULL DEFAULT 'generating' CHECK (status IN
    ('queued','planning','imaging','rendering','generating','awaiting_approval','approved','publishing','published','uncertain','failed')),
  revision integer NOT NULL DEFAULT 1 CHECK (revision = 1),
  checkpoint jsonb NOT NULL DEFAULT '{}',
  publication jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.cf_news_requests (
  id text PRIMARY KEY,
  channel text NOT NULL,
  status text NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','created','failed')),
  error text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS cf_news_articles_fresh ON public.cf_news_articles(published_at DESC);
CREATE INDEX IF NOT EXISTS cf_news_posts_created ON public.cf_news_posts(created_at);
ALTER TABLE public.cf_news_config ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.cf_news_usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.cf_news_articles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.cf_news_posts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.cf_news_requests ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.cf_news_config,public.cf_news_usage,public.cf_news_articles,public.cf_news_posts,public.cf_news_requests FROM PUBLIC,anon,authenticated;

-- Compare source titles at read time as well as admission time: historical rows
-- retain their original title_key and need no destructive re-key/backfill.
CREATE OR REPLACE FUNCTION public.cf_news_title_key(article jsonb)
RETURNS text LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE title text:=btrim(coalesce(article->>'title','')); suffix text; base text;
BEGIN
  -- Only recognize a domain or this article's declared outlet after a separator.
  -- Do not strip arbitrary subtitles (which can contain the distinguishing fact).
  suffix:=substring(title FROM '[[:space:]]+[-–—|][[:space:]]+([^|]+)$');
  IF suffix IS NOT NULL AND (
    suffix ~* '^(www\.)?[a-z0-9][a-z0-9.-]*\.[a-z]{2,24}$'
    OR lower(regexp_replace(suffix,'[^[:alnum:]]','','g')) =
       nullif(lower(regexp_replace(coalesce(article->>'source',''),'[^[:alnum:]]','','g')),'')) THEN
    title:=left(title,length(title)-length(suffix));
    title:=regexp_replace(title,'[[:space:]]+[-–—|][[:space:]]+$','');
  END IF;
  base:=replace(replace(lower(title),'’',''),'''','');
  RETURN btrim(regexp_replace(base,'[^[:alnum:]]+',' ','g'));
END $$;

CREATE OR REPLACE FUNCTION public.cf_news_titles_duplicate(a jsonb,b jsonb)
RETURNS boolean LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,public AS $$
DECLARE ka text:=public.cf_news_title_key(a); kb text:=public.cf_news_title_key(b);
  wa text[]; wb text[]; shared integer;
  stop constant text[]:=ARRAY['a','an','the','and','or','of','to','in','on','at','for','by','with','from','that','this','is','are','was','were','as','its'];
BEGIN
  IF ka='' OR kb='' THEN RETURN false; END IF;
  IF ka=kb THEN RETURN true; END IF;
  SELECT array_agg(DISTINCT w ORDER BY w) INTO wa FROM regexp_split_to_table(ka,' ') w WHERE NOT w=ANY(stop);
  SELECT array_agg(DISTINCT w ORDER BY w) INTO wb FROM regexp_split_to_table(kb,' ') w WHERE NOT w=ANY(stop);
  IF coalesce(cardinality(wa),0)<6 OR coalesce(cardinality(wb),0)<6 THEN RETURN false; END IF;
  -- Numbers and negation change the claim, even when most words are shared.
  IF ARRAY(SELECT w FROM unnest(wa) w WHERE w ~ '[0-9]' OR w IN ('no','not','never','without','fails','failed'))
    IS DISTINCT FROM ARRAY(SELECT w FROM unnest(wb) w WHERE w ~ '[0-9]' OR w IN ('no','not','never','without','fails','failed')) THEN RETURN false; END IF;
  SELECT count(*) INTO shared FROM unnest(wa) w WHERE w=ANY(wb);
  -- Deliberately conservative: all meaningful words in the shorter headline
  -- must occur in the longer one, with >=85% coverage of the longer headline.
  -- This catches small additions/reordering without conflating changed entities.
  RETURN shared=least(cardinality(wa),cardinality(wb))
    AND shared::numeric/greatest(cardinality(wa),cardinality(wb))>=0.85;
END $$;
REVOKE ALL ON FUNCTION public.cf_news_title_key(jsonb),public.cf_news_titles_duplicate(jsonb,jsonb) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.cf_news_title_key(jsonb),public.cf_news_titles_duplicate(jsonb,jsonb) TO service_role;

CREATE OR REPLACE FUNCTION public.cf_news(p_action text,p_task_id text DEFAULT '',p_payload jsonb DEFAULT '{}')
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
<<news>>
DECLARE
  defaults constant jsonb := '{"enabled":false,"run_at":"10:00","timezone_offset_minutes":330,"posts_per_day":10,"fetch_budget":6,"channel":"lorehush","brand":"STORY BRIEF","categories":["technology","business","science"]}';
  cfg jsonb; data jsonb; item jsonb; article jsonb; patch jsonb;
  post public.cf_news_posts%ROWTYPE;
  req public.cf_news_requests%ROWTYPE;
  stamp timestamptz := statement_timestamp();
  utc_day date := (statement_timestamp() AT TIME ZONE 'UTC')::date;
  local_now timestamp; day_start timestamptz; scheduled timestamp;
  published timestamptz; url text; url_key text; title_key text; article_id text;
  k text; n integer; remaining integer; inserted integer := 0; fresh boolean := false;
BEGIN
  IF p_payload IS NULL OR jsonb_typeof(p_payload)<>'object' OR octet_length(p_payload::text)>2097152 THEN
    RETURN '{"status":422,"error":"Invalid news payload"}';
  END IF;
  -- Config, fetch reservations, cache pruning and post creation share one lock.
  -- Limits are checked and consumed in the same transaction, including failed fetches.
  IF p_action IN ('config','reserve_fetch','ingest','create','tick','request','save') THEN
    -- A fixed old snapshot cannot see a competing committed near-duplicate.
    -- Fail closed rather than claim race safety outside READ COMMITTED.
    IF p_action IN ('ingest','create') AND current_setting('transaction_isolation')<>'read committed' THEN
      RETURN '{"status":409,"error":"News admission requires READ COMMITTED; retry in a new transaction"}';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtext('story-shorts:cf-news'));
  END IF;
  SELECT defaults || coalesce((SELECT jsonb_object_agg(key,value)
    FROM jsonb_each(c.config) WHERE defaults ? key),'{}') INTO cfg
    FROM public.cf_news_config c WHERE singleton;
  cfg := coalesce(cfg,defaults);

  IF p_action IN ('request','request_load') THEN
    SELECT * INTO req FROM public.cf_news_requests WHERE id=p_task_id;
    IF FOUND THEN
      IF p_action='request' AND req.channel IS DISTINCT FROM p_payload->>'channel' THEN
        RETURN '{"status":409,"error":"Request ID already used for another channel"}';
      END IF;
      RETURN jsonb_build_object('id',req.id,'channel',req.channel,'status',req.status,'error',req.error,
        'post_created',EXISTS(SELECT 1 FROM public.cf_news_posts WHERE id=p_task_id));
    END IF;
    IF p_action='request_load' THEN RETURN '{"status":404,"error":"News request not found"}'; END IF;
    IF coalesce(p_task_id,'') !~ '^[A-Za-z0-9_-]{1,128}$' OR NOT EXISTS(
      SELECT 1 FROM public.channels WHERE slug=p_payload->>'channel' AND enabled) THEN
      RETURN '{"status":422,"error":"Invalid request ID or channel"}';
    END IF;
    SELECT count(*) INTO n FROM (
      SELECT id,created_at FROM public.cf_news_requests
      UNION ALL SELECT p.id,p.created_at FROM public.cf_news_posts p
        WHERE NOT EXISTS(SELECT 1 FROM public.cf_news_requests r WHERE r.id=p.id)
    ) q WHERE q.created_at>=utc_day::timestamp AT TIME ZONE 'UTC'
      AND q.created_at<(utc_day+1)::timestamp AT TIME ZONE 'UTC';
    IF n>=20 THEN RETURN '{"status":429,"error":"Daily post limit reached"}'; END IF;
    INSERT INTO public.cf_news_requests(id,channel) VALUES(p_task_id,p_payload->>'channel');
    RETURN jsonb_build_object('id',p_task_id,'channel',p_payload->>'channel','status','queued');
  ELSIF p_action='provider_config' THEN
    -- Internal provider bootstrap only. Never expose through a dashboard route.
    RETURN jsonb_build_object('settings',coalesce((SELECT value_json::jsonb FROM public.app_settings WHERE key='runtime'),'{}'));
  ELSIF p_action='config' THEN
    IF EXISTS(SELECT 1 FROM jsonb_object_keys(p_payload) x WHERE NOT defaults ? x) THEN
      RETURN '{"status":422,"error":"Unknown config field"}';
    END IF;
    cfg := cfg || p_payload;
    IF jsonb_typeof(cfg->'enabled') IS DISTINCT FROM 'boolean'
      OR jsonb_typeof(cfg->'run_at') IS DISTINCT FROM 'string'
      OR (cfg->>'run_at') !~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'
      OR jsonb_typeof(cfg->'channel') IS DISTINCT FROM 'string'
      OR (cfg->>'channel') !~ '^[a-z][a-z0-9-]{1,63}$'
      OR jsonb_typeof(cfg->'brand') IS DISTINCT FROM 'string'
      OR length(btrim(cfg->>'brand')) NOT BETWEEN 1 AND 35 THEN
      RETURN '{"status":422,"error":"Invalid config"}';
    END IF;
    FOREACH k IN ARRAY ARRAY['timezone_offset_minutes','posts_per_day','fetch_budget'] LOOP
      IF jsonb_typeof(cfg->k) IS DISTINCT FROM 'number' OR (cfg->>k) !~ '^-?[0-9]{1,4}$' THEN
        RETURN '{"status":422,"error":"Config limits must be integers"}';
      END IF;
    END LOOP;
    IF (cfg->>'timezone_offset_minutes')::integer NOT BETWEEN -840 AND 840
      OR (cfg->>'posts_per_day')::integer NOT BETWEEN 1 AND 20
      OR (cfg->>'fetch_budget')::integer NOT BETWEEN 0 AND 200 THEN
      RETURN '{"status":422,"error":"Config limits out of range"}';
    END IF;
    IF jsonb_typeof(cfg->'categories') IS DISTINCT FROM 'array' THEN
      RETURN '{"status":422,"error":"Invalid categories"}';
    END IF;
    IF jsonb_array_length(cfg->'categories') NOT BETWEEN 1 AND 20 OR EXISTS(
      SELECT 1 FROM jsonb_array_elements(cfg->'categories') x
      WHERE jsonb_typeof(x)<>'string' OR (x#>>'{}') !~ '^[a-z][a-z0-9_-]{0,49}$') THEN
      RETURN '{"status":422,"error":"Invalid categories"}';
    END IF;
    INSERT INTO public.cf_news_config VALUES(true,cfg)
      ON CONFLICT(singleton) DO UPDATE SET config=excluded.config;
    RETURN jsonb_build_object('config',cfg);
  ELSIF p_action='reserve_fetch' THEN
    INSERT INTO public.cf_news_usage VALUES(utc_day,0) ON CONFLICT DO NOTHING;
    UPDATE public.cf_news_usage u SET requests=u.requests+1
      WHERE u.utc_day=news.utc_day AND u.requests<least(200,greatest(0,(cfg->>'fetch_budget')::integer));
    RETURN jsonb_build_object('allowed',FOUND);
  ELSIF p_action='dashboard' THEN
    SELECT coalesce(jsonb_agg(q.data ORDER BY q.created_at DESC),'[]') INTO data FROM (
      SELECT p.created_at,jsonb_build_object('id',p.id,'channel',p.channel,'source',p.source,
        'status',p.status,'revision',p.revision,'created_at',p.created_at,'updated_at',p.updated_at,
        'publication',p.publication) || (p.checkpoint-'task_ids'-'hero'-'copy') || (p.publication-'status') AS data
      FROM public.cf_news_posts p
      UNION ALL SELECT r.created_at,jsonb_build_object('id',r.id,'channel',r.channel,'status',r.status,
        'error',r.error,'created_at',r.created_at,'updated_at',r.updated_at,'slides','[]'::jsonb,'revision',1)
      FROM public.cf_news_requests r WHERE NOT EXISTS(SELECT 1 FROM public.cf_news_posts p WHERE p.id=r.id)
      ORDER BY created_at DESC LIMIT 100) q;
    RETURN jsonb_build_object('posts',data,'config',cfg,'usage',jsonb_build_object('requests_today',
      coalesce((SELECT u.requests FROM public.cf_news_usage u WHERE u.utc_day=news.utc_day),0)),
      'channels',coalesce((SELECT jsonb_agg(jsonb_build_object('slug',c.slug,'name',c.name,'enabled',c.enabled,
        'instagram_connected',EXISTS(SELECT 1 FROM public.meta_connections m WHERE m.channel_slug=c.slug
          AND coalesce(m.token_encrypted,'')<>'' AND m.login_mode='instagram' AND m.token_expires_at>stamp)) ORDER BY c.name)
        FROM public.channels c),'[]'));
  ELSIF p_action='tick' THEN
    local_now := (stamp AT TIME ZONE 'UTC') + make_interval(mins=>(cfg->>'timezone_offset_minutes')::integer);
    day_start := (local_now::date::timestamp - make_interval(mins=>(cfg->>'timezone_offset_minutes')::integer)) AT TIME ZONE 'UTC';
    scheduled := local_now::date + (cfg->>'run_at')::time;
    IF local_now<scheduled THEN scheduled:=scheduled-interval '1 day'; END IF;
    SELECT greatest(0,least(20,(cfg->>'posts_per_day')::integer)-count(*)::integer) INTO remaining
      FROM (SELECT id,created_at FROM public.cf_news_requests
        UNION ALL SELECT p.id,p.created_at FROM public.cf_news_posts p WHERE NOT EXISTS(
          SELECT 1 FROM public.cf_news_requests r WHERE r.id=p.id)) q
      WHERE created_at>=day_start AND created_at<day_start+interval '1 day';
    RETURN jsonb_build_object('due',(cfg->>'enabled')::boolean AND remaining>0
      AND local_now>=scheduled AND local_now<scheduled+interval '4 hours',
      'remaining',remaining,'channel',cfg->>'channel','day',local_now::date);
  ELSIF p_action IN ('ingest','create') THEN
    IF p_action='ingest' THEN
      IF jsonb_typeof(p_payload->'articles') IS DISTINCT FROM 'array' THEN
        RETURN '{"status":422,"error":"articles must be an array"}';
      END IF;
      IF jsonb_array_length(p_payload->'articles')>1000 THEN RETURN '{"status":422,"error":"Too many articles"}'; END IF;
      data := p_payload->'articles';
    ELSE
      IF coalesce(p_task_id,'') !~ '^[A-Za-z0-9_-]{1,128}$'
        OR jsonb_typeof(p_payload->'source') IS DISTINCT FROM 'object'
        OR NOT EXISTS(SELECT 1 FROM public.channels WHERE slug=p_payload->>'channel') THEN
        RETURN '{"status":422,"error":"Invalid post ID, source or channel"}';
      END IF;
      data := jsonb_build_array(p_payload->'source');
    END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(data) LOOP
      IF jsonb_typeof(item)<>'object' THEN
        IF p_action='create' THEN RETURN '{"status":422,"error":"Invalid article"}'; END IF;
        CONTINUE;
      END IF;
      url := btrim(coalesce(item->>'url',''));
      -- Strip fragments, common tracking parameters and trailing slashes. Preserve
      -- meaningful query parameters and case-sensitive paths; normalize host/scheme.
      url_key := regexp_replace(url,'#.*$','');
      url_key := regexp_replace(url_key,'([?&])(utm_[^=&]*|fbclid|gclid|mc_cid|mc_eid)=[^&]*','\1','gi');
      url_key := regexp_replace(url_key,'&+','&','g');
      url_key := replace(url_key,'?&','?');
      url_key := regexp_replace(url_key,'[?&]+$','');
      url_key := regexp_replace(url_key,'/+(\?|$)','\1','g');
      url_key := lower(substring(url_key FROM '^https?://[^/?]+')) || regexp_replace(url_key,'^https?://[^/?]+','','i');
      url_key := regexp_replace(url_key,'^https?://(www\.)?','');
      title_key := lower(regexp_replace(btrim(coalesce(item->>'title','')),'[[:space:][:punct:]]+',' ','g'));
      title_key := btrim(title_key);
      IF url !~* '^https?://[^[:space:]/?#]+[^[:space:]]*$' OR length(url)>4096
        OR url_key IS NULL OR length(title_key) NOT BETWEEN 1 AND 1000 THEN
        IF p_action='create' THEN RETURN '{"status":422,"error":"Invalid article URL or title"}'; END IF;
        CONTINUE;
      END IF;
      BEGIN
        published := coalesce(nullif(item->>'published_at','')::timestamptz,stamp);
      EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN
        published := stamp;
      END;
      IF NOT isfinite(published) OR published>stamp THEN published:=stamp; END IF;
      article_id := md5(url_key);
      article := jsonb_build_object('id',article_id,'title',left(btrim(item->>'title'),1000),
        'description',left(coalesce(item->>'description',''),10000),'url',url,
        'source',left(coalesce(item->>'source',''),300),'published_at',published,
        'category',left(coalesce(item->>'category','general'),50));
      IF p_action='create' THEN
        SELECT * INTO req FROM public.cf_news_requests WHERE id=p_task_id;
        IF FOUND AND req.channel IS DISTINCT FROM p_payload->>'channel' THEN
          RETURN '{"status":409,"error":"Request channel mismatch"}';
        END IF;
        SELECT * INTO post FROM public.cf_news_posts WHERE id=p_task_id;
        IF FOUND THEN
          IF post.channel IS DISTINCT FROM p_payload->>'channel' OR post.url_key IS DISTINCT FROM url_key
            OR post.title_key IS DISTINCT FROM title_key THEN
            RETURN '{"status":409,"error":"Post ID already used with different inputs"}';
          END IF;
          RETURN jsonb_build_object('id',post.id);
        END IF;
        -- Check before cache validation as well: a duplicate filtered at ingest
        -- must report 409 (select another candidate), not an unknown-source 422.
        IF EXISTS(SELECT 1 FROM public.cf_news_posts p WHERE p.url_key=news.url_key
          OR public.cf_news_titles_duplicate(p.source,item)) THEN
          RETURN '{"status":409,"error":"Source already used"}';
        END IF;
        -- The cache is authoritative: never persist caller-supplied article text.
        SELECT a.article INTO article FROM public.cf_news_articles a
          WHERE a.url_key=news.url_key AND a.title_key=news.title_key
            AND a.article->>'url'=item->>'url' AND a.article->>'title'=item->>'title'
            AND a.published_at>=stamp-interval '48 hours' AND a.published_at<=stamp
            AND length(btrim(a.article->>'description'))>0;
        IF NOT FOUND THEN RETURN '{"status":422,"error":"Source must match a fresh cached article"}'; END IF;
        IF EXISTS(SELECT 1 FROM public.cf_news_posts p WHERE p.url_key=news.url_key OR p.title_key=news.title_key
          OR public.cf_news_titles_duplicate(p.source,news.article)) THEN
          RETURN '{"status":409,"error":"Source already used"}';
        END IF;
        SELECT count(*) INTO n FROM (
          SELECT id,created_at FROM public.cf_news_requests WHERE id<>p_task_id
          UNION ALL SELECT p.id,p.created_at FROM public.cf_news_posts p WHERE NOT EXISTS(
            SELECT 1 FROM public.cf_news_requests r WHERE r.id=p.id)
        ) q WHERE created_at>=utc_day::timestamp AT TIME ZONE 'UTC'
          AND created_at<(utc_day+1)::timestamp AT TIME ZONE 'UTC';
        IF n>=20 THEN RETURN '{"status":429,"error":"Daily post limit reached"}'; END IF;
        -- Older requests can finish today; also enforce the actual creation cap.
        IF (SELECT count(*) FROM public.cf_news_posts p
          WHERE p.created_at>=utc_day::timestamp AT TIME ZONE 'UTC'
            AND p.created_at<(utc_day+1)::timestamp AT TIME ZONE 'UTC')>=20 THEN
          RETURN '{"status":429,"error":"Daily post limit reached"}';
        END IF;
        INSERT INTO public.cf_news_posts(id,channel,source,url_key,title_key)
          VALUES(p_task_id,p_payload->>'channel',article,url_key,title_key);
        UPDATE public.videos SET options_json=coalesce(options_json::jsonb,'{}')||
          '{"news_post":true,"music_enabled":false}'::jsonb,state='NEWS_POST',updated_at=stamp
          WHERE id=p_task_id;
        UPDATE public.jobs SET status='cf_news',updated_at=stamp WHERE id=p_task_id AND video_id=p_task_id;
        UPDATE public.cf_news_requests SET status='created',error='',updated_at=stamp WHERE id=p_task_id;
        RETURN jsonb_build_object('id',p_task_id);
      END IF;
      IF published>=stamp-interval '30 days' THEN
        IF EXISTS(SELECT 1 FROM public.cf_news_posts p WHERE p.url_key=news.url_key
            OR public.cf_news_titles_duplicate(p.source,news.article))
          OR EXISTS(SELECT 1 FROM public.cf_news_articles a WHERE a.published_at>=stamp-interval '30 days'
            AND public.cf_news_titles_duplicate(a.article,news.article)) THEN CONTINUE; END IF;
        INSERT INTO public.cf_news_articles(id,url_key,title_key,article,published_at)
          VALUES(article_id,url_key,title_key,article,published) ON CONFLICT DO NOTHING;
        IF FOUND THEN inserted:=inserted+1; END IF;
      END IF;
    END LOOP;
    DELETE FROM public.cf_news_articles WHERE published_at<stamp-interval '30 days';
    DELETE FROM public.cf_news_articles WHERE id IN
      (SELECT id FROM public.cf_news_articles ORDER BY published_at DESC,id OFFSET 1000);
    RETURN jsonb_build_object('ingested',inserted);
  ELSIF p_action='candidates' THEN
    RETURN jsonb_build_object('articles',coalesce((SELECT jsonb_agg(q.article ORDER BY q.quality DESC,q.published_at DESC) FROM (
      SELECT a.article,a.published_at,length(btrim(a.article->>'description')) AS quality FROM public.cf_news_articles a
      WHERE a.published_at>=stamp-interval '48 hours' AND a.published_at<=stamp
        AND length(btrim(a.article->>'description'))>0
        AND NOT EXISTS(SELECT 1 FROM public.cf_news_posts p WHERE p.url_key=a.url_key OR p.title_key=a.title_key
          OR public.cf_news_titles_duplicate(p.source,a.article))
      ORDER BY quality DESC,a.published_at DESC,a.id LIMIT 100) q),'[]'));
  END IF;

  SELECT * INTO post FROM public.cf_news_posts WHERE id=p_task_id FOR UPDATE;
  IF NOT FOUND THEN
    IF p_action='save' AND p_payload->>'status'='failed'
      AND NOT EXISTS(SELECT 1 FROM jsonb_object_keys(p_payload) x WHERE x NOT IN ('status','error'))
      AND (NOT p_payload?'error' OR jsonb_typeof(p_payload->'error')='string') THEN
      UPDATE public.cf_news_requests SET status='failed',error=left(coalesce(p_payload->>'error',''),2000),updated_at=stamp
        WHERE id=p_task_id RETURNING * INTO req;
      IF FOUND THEN
        UPDATE public.videos SET state='NEWS_POST',options_json=coalesce(options_json::jsonb,'{}')||'{"news_post":true}',updated_at=stamp WHERE id=p_task_id;
        UPDATE public.jobs SET status='cf_failed',error=req.error,updated_at=stamp WHERE id=p_task_id AND video_id=p_task_id;
        RETURN jsonb_build_object('id',req.id,'channel',req.channel,'status',req.status,'error',req.error);
      END IF;
    END IF;
    RETURN '{"status":404,"error":"News post not found"}';
  END IF;
  IF p_action='save' THEN
    IF EXISTS(SELECT 1 FROM jsonb_object_keys(p_payload) x WHERE x NOT IN
      ('status','title','caption','slides','copy','hero','task_ids','error')) THEN
      RETURN '{"status":422,"error":"Unknown checkpoint field"}';
    END IF;
    IF post.status NOT IN ('queued','planning','imaging','rendering','generating','failed','awaiting_approval') THEN
      RETURN '{"status":409,"error":"Post is locked after approval"}';
    END IF;
    IF p_payload?'status' AND coalesce(p_payload->>'status','') NOT IN ('queued','planning','imaging','rendering','generating','awaiting_approval','failed') THEN
      RETURN '{"status":422,"error":"Invalid checkpoint status"}';
    END IF;
    FOREACH k IN ARRAY ARRAY['title','caption','error'] LOOP
      IF p_payload?k AND jsonb_typeof(p_payload->k)<>'string' THEN RETURN '{"status":422,"error":"Invalid checkpoint text"}'; END IF;
    END LOOP;
    IF p_payload?'slides' AND jsonb_typeof(p_payload->'slides')<>'array'
      OR p_payload?'task_ids' AND jsonb_typeof(p_payload->'task_ids') NOT IN ('array','object') THEN
      RETURN '{"status":422,"error":"Invalid checkpoint collection"}';
    END IF;
    UPDATE public.cf_news_posts SET checkpoint=checkpoint||(p_payload-'status'),
      status=coalesce(p_payload->>'status',status),updated_at=stamp WHERE id=post.id RETURNING * INTO post;
    UPDATE public.videos SET state='NEWS_POST',updated_at=stamp WHERE id=post.id;
    UPDATE public.jobs SET status=CASE WHEN post.status='failed' THEN 'cf_failed'
      WHEN post.status='awaiting_approval' THEN 'cf_done' ELSE 'cf_news' END,
      error=coalesce(post.checkpoint->>'error',''),updated_at=stamp WHERE id=post.id AND video_id=post.id;
  ELSIF p_action='approve' THEN
    IF p_payload->'revision' IS DISTINCT FROM '1'::jsonb OR post.status<>'awaiting_approval' THEN
      RETURN '{"status":409,"error":"Review revision 1 of an awaiting post"}';
    END IF;
    IF jsonb_typeof(post.checkpoint->'slides') IS DISTINCT FROM 'array' THEN
      RETURN '{"status":409,"error":"Slides are required"}';
    END IF;
    IF jsonb_array_length(post.checkpoint->'slides')=0 THEN RETURN '{"status":409,"error":"Slides are required"}'; END IF;
    UPDATE public.cf_news_posts SET status='approved',updated_at=stamp WHERE id=post.id RETURNING * INTO post;
  ELSIF p_action='publish_claim' THEN
    IF post.status='approved' THEN
      -- Only persisted, reviewed slides may be published; caller URLs are ignored.
      IF jsonb_typeof(post.checkpoint->'slides') IS DISTINCT FROM 'array' THEN
        RETURN '{"status":409,"error":"Persisted slides required"}';
      END IF;
      IF jsonb_array_length(post.checkpoint->'slides')=0 OR EXISTS(
        SELECT 1 FROM jsonb_array_elements(post.checkpoint->'slides') s
        WHERE jsonb_typeof(s)<>'object' OR coalesce(s->>'url','') !~ '^https://[^[:space:]/?#]+/[^[:space:]]*') THEN
        RETURN '{"status":409,"error":"Persisted HTTPS slide URLs required"}';
      END IF;
      fresh:=true;
      UPDATE public.cf_news_posts SET status='publishing',updated_at=stamp WHERE id=post.id RETURNING * INTO post;
    ELSIF post.status NOT IN ('publishing','published','uncertain') THEN
      RETURN '{"status":409,"error":"Approve before publishing"}';
    END IF;
  ELSIF p_action='publication_patch' THEN
    IF EXISTS(SELECT 1 FROM jsonb_object_keys(p_payload) x WHERE x NOT IN
      ('status','container_ids','parent_id','remote_id','error')) THEN
      RETURN '{"status":422,"error":"Unknown publication field"}';
    END IF;
    IF p_payload?'status' AND coalesce(p_payload->>'status','') NOT IN ('approved','publishing','published','uncertain','failed') THEN
      RETURN '{"status":422,"error":"Invalid publication status"}';
    END IF;
    IF post.status NOT IN ('approved','publishing','published','uncertain','failed')
      OR (post.status='failed' AND NOT post.publication?'status')
      OR (post.status='published' AND coalesce(p_payload->>'status','published')<>'published') THEN
      RETURN '{"status":409,"error":"Invalid publication transition"}';
    END IF;
    FOREACH k IN ARRAY ARRAY['parent_id','remote_id','error'] LOOP
      IF p_payload?k AND jsonb_typeof(p_payload->k)<>'string' THEN RETURN '{"status":422,"error":"Invalid publication text"}'; END IF;
    END LOOP;
    IF p_payload?'container_ids' AND jsonb_typeof(p_payload->'container_ids')<>'array' THEN
      RETURN '{"status":422,"error":"Invalid container IDs"}';
    END IF;
    UPDATE public.cf_news_posts SET publication=publication||p_payload,
      status=coalesce(p_payload->>'status',status),updated_at=stamp WHERE id=post.id RETURNING * INTO post;
  ELSIF p_action<>'load' THEN RETURN '{"status":400,"error":"Unknown news action"}';
  END IF;
  RETURN jsonb_build_object('id',post.id,'channel',post.channel,'source',post.source,'status',post.status,
    'revision',post.revision,'publication',post.publication,'created_at',post.created_at,'updated_at',post.updated_at,
    'fresh',fresh) || post.checkpoint || (post.publication-'status');
END;
$$;
REVOKE ALL ON FUNCTION public.cf_news(text,text,jsonb) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.cf_news(text,text,jsonb) TO service_role;
NOTIFY pgrst,'reload schema';
