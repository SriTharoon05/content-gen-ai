-- Additive, review-only generation pilot. No production scheduler or publishing mutations.
CREATE TABLE IF NOT EXISTS public.cf_image_rate (
  model text PRIMARY KEY, next_at timestamptz NOT NULL
);
ALTER TABLE public.cf_image_rate ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.cf_image_rate FROM PUBLIC,anon,authenticated;
-- Shared with the editor; old tracks acquire a checksum through its registration UI.
CREATE TABLE IF NOT EXISTS public.cf_music_assets (
  track_id text PRIMARY KEY REFERENCES public.music_tracks(id),sha256 text NOT NULL CHECK(sha256 ~ '^[a-f0-9]{64}$')
);
ALTER TABLE public.cf_music_assets ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.cf_music_assets FROM PUBLIC,anon,authenticated;
CREATE OR REPLACE FUNCTION public.cf_generation(p_action text,p_task_id text,p_payload jsonb DEFAULT '{}'::jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public,extensions,pg_temp AS $$
DECLARE v public.videos%ROWTYPE; c public.channels%ROWTYPE; existing public.content_ledger%ROWTYPE;
  k text; data jsonb; entity text; angle text; vec vector(768); nearest double precision; threshold double precision;
  cfg jsonb; opts jsonb; music jsonb; ready boolean; cost numeric; spent numeric; daily numeric;
BEGIN
  IF p_task_id !~ '^[a-f0-9]{32}$' THEN RETURN '{"error":"Invalid video ID","status":400}'::jsonb; END IF;
  PERFORM pg_advisory_xact_lock(hashtext('cf-generation:'||p_task_id));
  SELECT * INTO v FROM public.videos WHERE id=p_task_id FOR UPDATE;
  IF p_action='create' THEN
    IF FOUND THEN
      IF v.options_json->>'execution_backend' IS DISTINCT FROM 'cloudflare-generation' OR v.channel_slug IS DISTINCT FROM p_payload->>'channel'
      THEN RETURN '{"error":"Idempotency conflict","status":409}'::jsonb; END IF;
      RETURN jsonb_build_object('id',v.id);
    END IF;
    SELECT * INTO c FROM public.channels WHERE slug=p_payload->>'channel' AND enabled;
    IF NOT FOUND THEN RETURN '{"error":"Unknown or disabled channel","status":404}'::jsonb; END IF;
    SELECT value_json INTO cfg FROM public.app_settings WHERE key='runtime';
    opts:=coalesce(c.overrides_json::jsonb,'{}'::jsonb);
    IF p_payload->>'publishing_mode' IN ('review','direct') THEN opts:=opts||jsonb_build_object('publishing_mode',p_payload->>'publishing_mode'); END IF;
    IF p_payload ? 'publish_platforms' THEN opts:=opts||jsonb_build_object('publish_platforms',p_payload->'publish_platforms'); END IF;
    IF p_payload ? 'topic' THEN opts:=opts||jsonb_build_object('topic',left(p_payload->>'topic',1000)); END IF;
    -- Explicit per-run review overrides channel/global defaults. Default remains review.
    IF p_payload ? 'review_required' AND jsonb_typeof(p_payload->'review_required')<>'boolean'
    THEN RETURN '{"error":"review_required must be boolean","status":422}'::jsonb; END IF;
    ready:=CASE WHEN p_payload ? 'review_required' AND p_payload->>'publishing_mode' IS DISTINCT FROM 'settings' THEN NOT (p_payload->>'review_required')::boolean
      WHEN opts->>'auto_publish'='false' THEN false
      WHEN opts->>'publishing_mode'='review' OR coalesce((opts->>'force_review')::boolean,false) THEN false
      WHEN opts->>'publishing_mode'='direct' THEN true
      ELSE coalesce((cfg->'schedule'->>'auto_publish')::boolean,false) AND NOT coalesce((cfg->'publishing'->>'review_before_upload')::boolean,true) END;
    INSERT INTO public.videos(id,channel_slug,parent_id,language,state,stage_detail,progress,topic,title,description,instagram_caption,hashtags,made_for_kids,premise_json,script_json,visual_json,spec_json,qa_json,options_json,narration_path,output_path,duration_seconds,narration_seconds,image_count,reused_count,credits_spent,text_tokens,voice_json,approved,error,created_at,updated_at)
    VALUES(p_task_id,c.slug,'',coalesce(opts->>'primary_language',cfg->'languages'->>'primary','en'),'CF_GENERATING','Cloudflare generation',0,'','','','','[]',false,'{}','{}','{}','{}','{}',
      opts||jsonb_build_object('execution_backend','cloudflare-generation','force_review',NOT ready,'publishing_mode',CASE WHEN ready THEN 'direct' ELSE 'review' END,'auto_publish',ready,'cf_steps','{}'::jsonb),
      '','',0,0,0,0,0,0,'{}',false,'',now(),now());
    INSERT INTO public.jobs(id,video_id,stage,status,attempts,max_attempts,payload_json,error,created_at,updated_at)
    VALUES(p_task_id,p_task_id,'cf_generation','cf_generating',1,1,'{}','',now(),now());
    RETURN jsonb_build_object('id',p_task_id);
  END IF;
  IF v.id IS NULL OR v.options_json->>'execution_backend' IS DISTINCT FROM 'cloudflare-generation'
  THEN RETURN '{"error":"Unknown generation","status":404}'::jsonb; END IF;
  IF p_action='status' THEN
    RETURN jsonb_build_object('id',v.id,'state',v.state,'detail',v.stage_detail,'error',v.error,'output',v.output_path,'steps',v.options_json->'cf_steps');
  ELSIF p_action='config' THEN
    SELECT * INTO c FROM public.channels WHERE slug=v.channel_slug;
    SELECT value_json INTO data FROM public.app_settings WHERE key='runtime';
    SELECT to_jsonb(m)||jsonb_build_object('sha256',a.sha256) INTO music FROM public.music_tracks m LEFT JOIN public.cf_music_assets a ON a.track_id=m.id
      WHERE m.id=coalesce(v.options_json->>'music_track',data->'music'->>'default_track','') AND NOT m.archived;
    IF coalesce(v.options_json->>'music_enabled','true')<>'false' AND coalesce(v.options_json->>'music_track',data->'music'->>'default_track','')<>'' AND music IS NULL
    THEN RETURN '{"error":"Selected default music is missing or archived","status":422}'::jsonb; END IF;
    RETURN jsonb_build_object('channel',to_jsonb(c),'settings',coalesce(data,'{}'::jsonb),'language',v.language,'options',v.options_json::jsonb-'cf_steps','music_track',music,
      'prior',coalesce((SELECT jsonb_agg(x) FROM (SELECT core_entity,content_angle,core_concept FROM public.content_ledger WHERE status='active' ORDER BY created_at DESC LIMIT 150)x),'[]'::jsonb));
  ELSIF p_action='save' THEN
    k:=p_payload->>'key'; data:=p_payload->'value';
    IF k IS NULL OR k !~ '^[a-z0-9_-]{1,64}$' THEN RETURN '{"error":"Invalid checkpoint","status":400}'::jsonb; END IF;
    UPDATE public.videos SET options_json=jsonb_set(options_json::jsonb,ARRAY['cf_steps',k],data),stage_detail=left(k,160),
      state=CASE WHEN state='CF_FAILED' THEN 'CF_GENERATING' ELSE state END,
      error=CASE WHEN state='CF_FAILED' THEN '' ELSE error END,updated_at=now() WHERE id=p_task_id;
    UPDATE public.jobs SET status='cf_generating',error='',updated_at=now() WHERE id=p_task_id AND status='cf_failed';
    RETURN data;
  ELSIF p_action='reserve' THEN
    -- Same permanent entity/angle and adaptive pgvector collision policy as content_ledger.py.
    PERFORM pg_advisory_xact_lock(hashtext('concept-video:'||p_task_id));
    SELECT * INTO existing FROM public.content_ledger WHERE video_id=p_task_id AND status='active' LIMIT 1;
    IF FOUND THEN RETURN to_jsonb(existing)-'embedding'; END IF;
    entity:=lower(regexp_replace(trim(p_payload->>'core_entity'),'\s+',' ','g'));
    angle:=lower(regexp_replace(trim(p_payload->>'content_angle'),'\s+',' ','g'));
    vec:=(p_payload->>'embedding')::vector(768);
    PERFORM pg_advisory_xact_lock(hashtext('story-shorts:content:'||entity));
    IF EXISTS(SELECT 1 FROM public.content_ledger WHERE core_entity=entity AND content_angle=angle AND status='active') THEN RETURN '{"collision":true}'::jsonb; END IF;
    SELECT CASE WHEN count(*)<10 THEN .85 ELSE percentile_cont(.9) WITHIN GROUP(ORDER BY sim) END INTO threshold
    FROM (SELECT 1-(a.embedding<=>b.embedding) sim FROM public.content_ledger a JOIN public.content_ledger b ON a.core_entity=b.core_entity AND a.id<b.id WHERE a.core_entity=entity AND a.status='active' AND b.status='active') pairs;
    SELECT max(1-(embedding<=>vec)) INTO nearest FROM public.content_ledger WHERE core_entity=entity AND status='active';
    IF coalesce(nearest,0)>coalesce(threshold,.85) THEN RETURN '{"collision":true}'::jsonb; END IF;
    INSERT INTO public.content_ledger(id,video_id,core_entity,content_angle,core_concept,status,embedding,created_at)
    VALUES(gen_random_uuid(),p_task_id,entity,angle,p_payload->>'core_concept','active',vec,now()) RETURNING * INTO existing;
    RETURN to_jsonb(existing)-'embedding';
  ELSIF p_action='image_permit' THEN
    k:=p_payload->>'model';
    PERFORM pg_advisory_xact_lock(hashtext('cf-image-rate:'||k));
    INSERT INTO public.cf_image_rate(model,next_at) VALUES(k,clock_timestamp()) ON CONFLICT DO NOTHING;
    SELECT jsonb_build_object('wait_ms',greatest(0,ceil(extract(epoch FROM (next_at-clock_timestamp()))*1000)))
      INTO data FROM public.cf_image_rate WHERE model=k;
    IF (data->>'wait_ms')::integer>0 THEN RETURN data; END IF;
    UPDATE public.cf_image_rate SET next_at=clock_timestamp()+
      CASE WHEN k='lykon/dreamshaper-8-lcm' THEN interval '220 milliseconds' ELSE interval '1100 milliseconds' END
      WHERE model=k;
    RETURN '{"wait_ms":0}'::jsonb;
  ELSIF p_action='call_start' THEN
    k:=p_payload->>'key';
    SELECT to_jsonb(pc) INTO data FROM public.provider_calls pc WHERE idempotency_key='cf:'||p_task_id||':'||k;
    IF data IS NOT NULL THEN RETURN data; END IF;
    -- Serialize all new Cloudflare reservations across channels and manual/scheduled jobs.
    PERFORM pg_advisory_xact_lock(hashtext('story-shorts:provider-credit-budget'));
    cost:=coalesce((p_payload->>'credits')::numeric,0);
    IF cost<0 OR cost>10 THEN RETURN '{"error":"Invalid provider credit cost","status":422}'::jsonb; END IF;
    SELECT value_json INTO cfg FROM public.app_settings WHERE key='runtime';
    SELECT coalesce(sum(credits),0) INTO spent FROM public.provider_calls WHERE status IN ('settled','reserved','uncertain');
    IF cost>greatest(0,coalesce((cfg->'pricing'->>'credit_balance')::numeric,0)-spent)
    THEN RETURN '{"error":"Insufficient available image credits; no purchase attempted","status":409}'::jsonb; END IF;
    IF coalesce(v.options_json->>'cf_schedule_date','')<>'' THEN
      SELECT coalesce(sum(pc.credits),0) INTO daily FROM public.provider_calls pc JOIN public.videos x ON x.id=pc.video_id
        WHERE pc.status IN ('settled','reserved','uncertain') AND x.options_json->>'cf_schedule_date'=v.options_json->>'cf_schedule_date';
      IF daily+cost>coalesce((cfg->'schedule'->>'daily_credit_ceiling')::numeric,0)
      THEN RETURN '{"error":"Scheduled daily image credit ceiling reached; no purchase attempted","status":409}'::jsonb; END IF;
    END IF;
    INSERT INTO public.provider_calls(id,video_id,idempotency_key,provider,model,status,credits,usd,units,detail_json,created_at)
    VALUES(replace(gen_random_uuid()::text,'-',''),p_task_id,'cf:'||p_task_id||':'||k,p_payload->>'provider',p_payload->>'model','reserved',coalesce((p_payload->>'credits')::float,0),0,1,'{}',now());
    RETURN '{"new":true}'::jsonb;
  ELSIF p_action='call_finish' THEN
    UPDATE public.provider_calls SET status='settled',detail_json=p_payload->'value'
    WHERE idempotency_key='cf:'||p_task_id||':'||(p_payload->>'key');
    RETURN '{"ok":true}'::jsonb;
  ELSIF p_action='complete' THEN
    IF v.state IN ('AWAITING_APPROVAL','READY','PUBLISHED','PUBLISHING') THEN RETURN jsonb_build_object('ok',true,'state',v.state,'publish_ready',coalesce((v.options_json->>'cf_publish_ready')::boolean,false)); END IF;
    data:=v.options_json->'cf_steps';
    IF NOT EXISTS(SELECT 1 FROM public.render_tasks WHERE id=data->>'render-task' AND video_id=p_task_id AND status='succeeded')
    THEN RETURN '{"error":"Final render is not complete","status":409}'::jsonb; END IF;
    INSERT INTO public.assets(id,video_id,shot_id,kind,path,registry_id,reused,metadata_json,created_at)
    SELECT md5(p_task_id||e.key),p_task_id,substring(e.key from 7),'image',e.value->>'url','',false,
      jsonb_build_object('sha256',e.value->>'sha256','storage_provider','cloudinary'),now()
    FROM jsonb_each(data) e WHERE e.key ~ '^image-s[0-9]{3}$'
    ON CONFLICT(id) DO NOTHING;
    INSERT INTO public.assets(id,video_id,shot_id,kind,path,registry_id,reused,metadata_json,created_at)
    VALUES(md5(p_task_id||'narration'),p_task_id,'','narration',data->'audio-ready'->>'url','',false,
      jsonb_build_object('sha256',data->'audio-ready'->>'sha256','storage_provider','cloudinary'),now())
    ON CONFLICT(id) DO NOTHING;
    ready:=coalesce((v.options_json->>'auto_publish')::boolean,false) AND NOT coalesce((v.options_json->>'force_review')::boolean,true);
    UPDATE public.videos SET state=CASE WHEN ready THEN 'READY' ELSE 'AWAITING_APPROVAL' END,progress=99,stage_detail=CASE WHEN ready THEN 'Cloudflare generation complete; ready for publishing' ELSE 'Cloudflare generation complete; review required' END,approved=ready,error='',
      options_json=options_json::jsonb||jsonb_build_object('cf_publish_ready',ready),
      output_path=p_payload->>'url',duration_seconds=(p_payload->>'duration')::float,
      narration_path=data->'audio-ready'->>'url',narration_seconds=(data->'audio-ready'->>'duration')::float,
      script_json=data->'script',premise_json=data->'premise',visual_json=data->'visuals',qa_json=data->'qa',voice_json=data->'voice',
      title=data->'copy'->>'youtube_title',description=data->'copy'->>'youtube_description',instagram_caption=data->'copy'->>'instagram_caption',hashtags=coalesce(data->'copy'->'hashtags','[]'::jsonb),
      image_count=jsonb_array_length(data->'visuals'->'shots'),reused_count=0,
      credits_spent=coalesce((SELECT sum(credits) FROM public.provider_calls WHERE video_id=p_task_id AND status IN ('settled','uncertain')),0),updated_at=now() WHERE id=p_task_id;
    UPDATE public.jobs SET status='cf_done',updated_at=now() WHERE id=p_task_id;
    RETURN jsonb_build_object('ok',true,'state',CASE WHEN ready THEN 'READY' ELSE 'AWAITING_APPROVAL' END,'publish_ready',ready);
  ELSIF p_action='fail' THEN
    UPDATE public.videos SET state='CF_FAILED',error=left(p_payload->>'error',500),updated_at=now() WHERE id=p_task_id AND state NOT IN ('AWAITING_APPROVAL','READY','PUBLISHING','PUBLISHED');
    UPDATE public.jobs SET status='cf_failed',error=left(p_payload->>'error',500),updated_at=now() WHERE id=p_task_id AND status<>'cf_done';
    RETURN '{"ok":true}'::jsonb;
  END IF;
  RETURN '{"error":"Unknown generation operation","status":400}'::jsonb;
END; $$;
REVOKE ALL ON FUNCTION public.cf_generation(text,text,jsonb) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.cf_generation(text,text,jsonb) TO service_role;
NOTIFY pgrst,'reload schema';
