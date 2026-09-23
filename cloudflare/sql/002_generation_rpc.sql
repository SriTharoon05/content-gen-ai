-- Additive, review-only generation pilot. No production scheduler or publishing mutations.
CREATE OR REPLACE FUNCTION public.cf_generation(p_action text,p_task_id text,p_payload jsonb DEFAULT '{}'::jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public,extensions,pg_temp AS $$
DECLARE v public.videos%ROWTYPE; c public.channels%ROWTYPE; existing public.content_ledger%ROWTYPE;
  k text; data jsonb; entity text; angle text; vec vector(768); nearest double precision; threshold double precision;
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
    IF coalesce(c.strategy_json->>'conversation','false')='true' OR coalesce(c.overrides_json->>'primary_language','en')<>'en'
    THEN RETURN '{"error":"Fresh pilot currently supports English single-narrator channels only; existing Render routes unchanged","status":422}'::jsonb; END IF;
    INSERT INTO public.videos(id,channel_slug,parent_id,language,state,stage_detail,progress,topic,title,description,instagram_caption,hashtags,made_for_kids,premise_json,script_json,visual_json,spec_json,qa_json,options_json,narration_path,output_path,duration_seconds,narration_seconds,image_count,reused_count,credits_spent,text_tokens,voice_json,approved,error,created_at,updated_at)
    VALUES(p_task_id,c.slug,'','en','CF_GENERATING','Fresh Cloudflare review-only test',0,'','','','','[]',false,'{}','{}','{}','{}','{}',
      jsonb_build_object('execution_backend','cloudflare-generation','force_review',true,'publishing_mode','review','auto_publish',false,'cf_steps','{}'::jsonb),
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
    RETURN jsonb_build_object('channel',to_jsonb(c),'settings',coalesce(data,'{}'::jsonb),
      'prior',coalesce((SELECT jsonb_agg(x) FROM (SELECT core_entity,content_angle,core_concept FROM public.content_ledger WHERE status='active' ORDER BY created_at DESC LIMIT 150)x),'[]'::jsonb));
  ELSIF p_action='save' THEN
    k:=p_payload->>'key'; data:=p_payload->'value';
    IF k IS NULL OR k !~ '^[a-z0-9_-]{1,64}$' THEN RETURN '{"error":"Invalid checkpoint","status":400}'::jsonb; END IF;
    UPDATE public.videos SET options_json=jsonb_set(options_json::jsonb,ARRAY['cf_steps',k],data),stage_detail=left(k,160),updated_at=now() WHERE id=p_task_id;
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
  ELSIF p_action='call_start' THEN
    k:=p_payload->>'key';
    SELECT to_jsonb(pc) INTO data FROM public.provider_calls pc WHERE idempotency_key='cf:'||p_task_id||':'||k;
    IF data IS NOT NULL THEN RETURN data; END IF;
    INSERT INTO public.provider_calls(id,video_id,idempotency_key,provider,model,status,credits,usd,units,detail_json,created_at)
    VALUES(replace(gen_random_uuid()::text,'-',''),p_task_id,'cf:'||p_task_id||':'||k,p_payload->>'provider',p_payload->>'model','reserved',coalesce((p_payload->>'credits')::float,0),0,1,'{}',now());
    RETURN '{"new":true}'::jsonb;
  ELSIF p_action='call_finish' THEN
    UPDATE public.provider_calls SET status='settled',detail_json=p_payload->'value'
    WHERE idempotency_key='cf:'||p_task_id||':'||(p_payload->>'key');
    RETURN '{"ok":true}'::jsonb;
  ELSIF p_action='complete' THEN
    IF v.state='AWAITING_APPROVAL' THEN RETURN '{"ok":true}'::jsonb; END IF;
    data:=v.options_json->'cf_steps';
    IF NOT EXISTS(SELECT 1 FROM public.render_tasks WHERE id=data->>'render-task' AND video_id=p_task_id AND status='succeeded')
    THEN RETURN '{"error":"Final render is not complete","status":409}'::jsonb; END IF;
    UPDATE public.videos SET state='AWAITING_APPROVAL',progress=99,stage_detail='Cloudflare fresh generation; review required',approved=false,error='',
      options_json=options_json::jsonb||'{"force_review":true,"publishing_mode":"review","auto_publish":false}'::jsonb,
      output_path=p_payload->>'url',duration_seconds=(p_payload->>'duration')::float,
      narration_path=data->'audio-ready'->>'url',narration_seconds=(data->'audio-ready'->>'duration')::float,
      script_json=data->'script',premise_json=data->'premise',visual_json=data->'visuals',qa_json=data->'qa',voice_json=data->'voice',
      title=data->'copy'->>'youtube_title',description=data->'copy'->>'youtube_description',instagram_caption=data->'copy'->>'instagram_caption',hashtags=data->'copy'->'hashtags',
      image_count=jsonb_array_length(data->'visuals'->'shots'),reused_count=0,
      credits_spent=coalesce((SELECT sum(credits) FROM public.provider_calls WHERE video_id=p_task_id AND status='settled'),0),updated_at=now() WHERE id=p_task_id;
    UPDATE public.jobs SET status='cf_done',updated_at=now() WHERE id=p_task_id;
    RETURN '{"ok":true}'::jsonb;
  ELSIF p_action='fail' THEN
    UPDATE public.videos SET state='CF_FAILED',error=left(p_payload->>'error',500),updated_at=now() WHERE id=p_task_id AND state<>'AWAITING_APPROVAL';
    UPDATE public.jobs SET status='cf_failed',error=left(p_payload->>'error',500),updated_at=now() WHERE id=p_task_id;
    RETURN '{"ok":true}'::jsonb;
  END IF;
  RETURN '{"error":"Unknown generation operation","status":400}'::jsonb;
END; $$;
REVOKE ALL ON FUNCTION public.cf_generation(text,text,jsonb) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.cf_generation(text,text,jsonb) TO service_role;
NOTIFY pgrst,'reload schema';
