-- Additive pilot-only API; no table, data, or RLS changes.
CREATE OR REPLACE FUNCTION public.cf_media_task(p_action text,p_task_id text DEFAULT '',p_payload jsonb DEFAULT '{}'::jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  t public.render_tasks%ROWTYPE;
  m jsonb;
  v text;
  desired text;
BEGIN
  IF p_action='check' THEN
    RETURN jsonb_build_object('ok',true,'transport','supabase_https','render_tasks_exists',to_regclass('public.render_tasks') IS NOT NULL,
      'pgvector_installed',EXISTS(SELECT 1 FROM pg_catalog.pg_extension WHERE extname='vector'));
  END IF;
  IF p_task_id !~ '^[a-f0-9]{32}$' THEN RETURN '{"error":"Invalid task ID","status":400}'::jsonb; END IF;
  IF p_action='create' THEN
    v:=p_payload->>'video_id';m:=p_payload->'manifest';
    PERFORM 1 FROM public.videos WHERE id=v FOR UPDATE;
    IF NOT FOUND THEN RETURN '{"error":"Source video does not exist","status":404}'::jsonb; END IF;
    SELECT * INTO t FROM public.render_tasks WHERE id=p_task_id FOR UPDATE;
    IF FOUND THEN
      IF t.video_id<>v OR t.continuation_json->>'backend' IS DISTINCT FROM 'cloudflare-pilot' OR t.manifest_json::jsonb IS DISTINCT FROM m
      THEN RETURN '{"error":"Idempotency key already used with different inputs","status":409}'::jsonb; END IF;
      RETURN jsonb_build_object('id',p_task_id);
    END IF;
    INSERT INTO public.jobs(id,video_id,stage,status,attempts,max_attempts,payload_json,error,created_at,updated_at)
    VALUES(p_task_id,v,'cloudflare_media','cf_waiting',0,1,jsonb_build_object('render_task_id',p_task_id,'backend','cloudflare-pilot'),'',now(),now());
    INSERT INTO public.render_tasks(id,job_id,video_id,status,manifest_json,continuation_json,result_json,owner_hash,pipeline_id,error,trigger_attempts,next_trigger_at,deadline,created_at,updated_at)
    VALUES(p_task_id,p_task_id,v,'queued',m,'{"backend":"cloudflare-pilot"}','{}','','','',0,'infinity',now()+interval '4 hours',now(),now());
    RETURN jsonb_build_object('id',p_task_id);
  END IF;
  IF p_action='load' THEN
    SELECT * INTO t FROM public.render_tasks WHERE id=p_task_id AND continuation_json->>'backend'='cloudflare-pilot';
  ELSE
    SELECT * INTO t FROM public.render_tasks WHERE id=p_task_id AND continuation_json->>'backend'='cloudflare-pilot' FOR UPDATE;
  END IF;
  IF NOT FOUND THEN RETURN '{"error":"Unknown Cloudflare task","status":404}'::jsonb; END IF;
  IF p_action='load' THEN RETURN to_jsonb(t); END IF;
  IF p_action='claim' THEN
    IF (p_payload->>'owner_hash') IS NULL OR (p_payload->>'owner_hash') !~ '^[a-f0-9]{64}$'
    THEN RETURN '{"error":"Invalid owner","status":400}'::jsonb; END IF;
    IF t.deadline>now() AND (t.status='queued' OR (t.status='running' AND t.owner_hash=p_payload->>'owner_hash')) THEN
      UPDATE public.render_tasks SET status='running',owner_hash=p_payload->>'owner_hash',error='',updated_at=now() WHERE id=p_task_id;
      RETURN jsonb_build_object('manifest',t.manifest_json);
    END IF;
    RETURN '{}'::jsonb;
  ELSIF p_action='expire' THEN
    IF t.status IN ('queued','running') AND t.deadline<=now() THEN
      UPDATE public.render_tasks SET status='failed',error='Media task deadline exceeded',updated_at=now() WHERE id=p_task_id;
      UPDATE public.jobs SET status='cf_failed',error='Media task deadline exceeded',updated_at=now() WHERE id=t.job_id;
    END IF;
  ELSIF p_action IN ('finish','heartbeat') THEN
    IF t.owner_hash IS DISTINCT FROM p_payload->>'owner_hash' OR t.deadline<=now() THEN RETURN '{"error":"Task not owned or expired","status":409}'::jsonb; END IF;
    IF p_action='heartbeat' THEN
      IF t.status='running' THEN UPDATE public.render_tasks SET updated_at=now() WHERE id=p_task_id; END IF;
    ELSE
      desired:=p_payload->>'status';
      IF desired IS NULL OR desired NOT IN ('succeeded','failed') THEN RETURN '{"error":"Invalid completion status","status":400}'::jsonb; END IF;
      IF t.status=desired THEN RETURN '{"ok":true}'::jsonb; END IF;
      IF t.status<>'running' THEN RETURN '{"error":"Task already completed","status":409}'::jsonb; END IF;
      UPDATE public.render_tasks SET status=desired,result_json=coalesce(p_payload->'result','{}'::jsonb),error=left(coalesce(p_payload->>'error',''),500),updated_at=now() WHERE id=p_task_id;
      UPDATE public.jobs SET status=CASE WHEN desired='succeeded' THEN 'cf_done' ELSE 'cf_failed' END,error=left(coalesce(p_payload->>'error',''),500),updated_at=now() WHERE id=t.job_id;
    END IF;
  ELSIF p_action='triggered' THEN
    UPDATE public.render_tasks SET pipeline_id=p_payload->>'pipeline_id',trigger_attempts=trigger_attempts+1,updated_at=now() WHERE id=p_task_id;
  ELSIF p_action='trigger_failed' THEN
    IF t.status='queued' THEN UPDATE public.render_tasks SET error='CircleCI trigger unavailable; workflow will retry',updated_at=now() WHERE id=p_task_id; END IF;
  ELSE RETURN '{"error":"Unknown operation","status":400}'::jsonb;
  END IF;
  RETURN '{"ok":true}'::jsonb;
END;
$$;
REVOKE ALL ON FUNCTION public.cf_media_task(text,text,jsonb) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.cf_media_task(text,text,jsonb) TO service_role;
NOTIFY pgrst,'reload schema';
