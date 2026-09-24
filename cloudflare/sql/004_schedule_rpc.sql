-- One scheduler owner shared by both deployments. Defaults to Render; never enables itself.
CREATE OR REPLACE FUNCTION public.cf_schedule(p_action text,p_task_id text,p_payload jsonb DEFAULT '{}'::jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE control jsonb; cfg jsonb; local_time timestamp; start_time timestamp; run_day text;
  ch record; n integer; count_per integer; per_video numeric; budget numeric; used numeric;
  queued jsonb:='[]'; identity text; result jsonb; model text;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtext('story-shorts:schedule'));
  SELECT value_json::jsonb INTO control FROM app_settings WHERE key='backend_control';
  control:=coalesce(control,'{"scheduler_owner":"render"}'::jsonb);
  SELECT value_json::jsonb INTO cfg FROM app_settings WHERE key='runtime';
  cfg:=coalesce(cfg,'{}'::jsonb);
  IF p_action='verify_guard' THEN
    INSERT INTO app_settings(key,value_json,updated_at) VALUES('backend_control',control||'{"render_guard_verified":true}'::jsonb,now())
      ON CONFLICT(key) DO UPDATE SET value_json=EXCLUDED.value_json,updated_at=now();
    RETURN '{"verified":true}'::jsonb;
  END IF;
  IF p_action='submitted' THEN
    UPDATE videos SET options_json=options_json::jsonb||'{"cf_schedule_submitted":true}'::jsonb
      WHERE id=p_task_id AND options_json->>'cf_schedule_date' IS NOT NULL;
    RETURN '{"saved":true}'::jsonb;
  END IF;
  IF p_action='get' THEN RETURN jsonb_build_object('owner',control->>'scheduler_owner','schedule',coalesce(cfg->'schedule','{}')); END IF;
  IF p_action='save' THEN
    IF p_payload->>'owner' NOT IN ('render','cloudflare') OR p_payload->>'owner' IS NULL THEN RAISE EXCEPTION 'Invalid scheduler owner'; END IF;
    IF p_payload->>'owner'='cloudflare' AND control->>'render_guard_verified' IS DISTINCT FROM 'true'
      THEN RETURN '{"error":"Deploy and verify the Render scheduler ownership guard before switching scheduled runs","status":409}'::jsonb; END IF;
    IF NOT coalesce((p_payload->'schedule') ?& ARRAY['enabled','run_at','videos_per_channel','timezone_offset_minutes','daily_credit_ceiling','channels'],false)
      OR jsonb_typeof(p_payload->'schedule'->'enabled') IS DISTINCT FROM 'boolean'
      OR jsonb_typeof(p_payload->'schedule'->'channels') IS DISTINCT FROM 'array'
      OR (p_payload->'schedule'->>'run_at') !~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'
      OR (p_payload->'schedule'->>'videos_per_channel')::integer NOT BETWEEN 1 AND 20
      OR (p_payload->'schedule'->>'timezone_offset_minutes')::integer NOT BETWEEN -720 AND 840
      OR (p_payload->'schedule'->>'daily_credit_ceiling')::numeric NOT BETWEEN 0 AND 10
      THEN RETURN '{"error":"Invalid schedule limits","status":422}'::jsonb; END IF;
    INSERT INTO app_settings(key,value_json,updated_at) VALUES('backend_control',control||jsonb_build_object('scheduler_owner',p_payload->>'owner'),now())
      ON CONFLICT(key) DO UPDATE SET value_json=EXCLUDED.value_json,updated_at=now();
    UPDATE app_settings SET value_json=jsonb_set(cfg,'{schedule}',coalesce(cfg->'schedule','{}')||(p_payload->'schedule')),updated_at=now() WHERE key='runtime';
    RETURN '{"saved":true}'::jsonb;
  END IF;
  IF p_action<>'tick' THEN RETURN '{"error":"Unknown scheduler action","status":400}'::jsonb; END IF;
  IF control->>'scheduler_owner'<>'cloudflare' OR NOT coalesce((cfg->'schedule'->>'enabled')::boolean,false) THEN RETURN '{"videos":[]}'::jsonb; END IF;
  local_time:=(now() AT TIME ZONE 'UTC')+make_interval(mins=>coalesce((cfg->'schedule'->>'timezone_offset_minutes')::integer,330));
  start_time:=local_time::date+coalesce(cfg->'schedule'->>'run_at','10:00')::time;
  IF local_time<start_time THEN start_time:=start_time-interval '1 day'; END IF;
  IF local_time>=start_time+interval '4 hours' THEN RETURN '{"videos":[]}'::jsonb; END IF;
  run_day:=start_time::date::text;
  IF EXISTS(SELECT 1 FROM schedule_runs WHERE run_date=run_day) THEN
    -- Recover a crash after DB commit but before Workflow submission; deterministic IDs dedupe.
    RETURN jsonb_build_object('videos',coalesce((SELECT jsonb_agg(jsonb_build_object('video_id',id)) FROM videos
      WHERE options_json->>'cf_schedule_date'=run_day AND state='CF_GENERATING'
        AND options_json->>'cf_schedule_submitted' IS DISTINCT FROM 'true'),'[]'::jsonb));
  END IF;
  model:=cfg->'models'->>'image_model';
  SELECT coalesce(sum(credits),0) INTO used FROM provider_calls WHERE provider='pollinations' AND status IN ('settled','reserved','uncertain');
  SELECT greatest(.0001,30*coalesce((x->>'credits')::numeric,.002)) INTO per_video
    FROM jsonb_array_elements(cfg->'models'->'image_catalog') x WHERE x->>'id'=model LIMIT 1;
  per_video:=coalesce(per_video,.06);
  budget:=greatest(0,least(coalesce((cfg->'schedule'->>'daily_credit_ceiling')::numeric,.8),coalesce((cfg->'pricing'->>'credit_balance')::numeric,0)-used));
  count_per:=greatest(1,least(20,coalesce((cfg->'schedule'->>'videos_per_channel')::integer,1)));
  FOR ch IN SELECT slug FROM channels WHERE enabled
    AND coalesce(json_array_length(overrides_json->'languages'),0)=0
    AND (coalesce(jsonb_array_length(cfg->'schedule'->'channels'),0)=0 OR (cfg->'schedule'->'channels') ? slug) ORDER BY slug LOOP
    FOR n IN 1..count_per LOOP
      IF budget<per_video THEN EXIT; END IF;
      identity:=md5('cf-schedule:'||run_day||':'||ch.slug||':'||n);
      result:=cf_generation('create',identity,jsonb_build_object('channel',ch.slug));
      IF result ? 'error' THEN RAISE EXCEPTION 'Scheduled generation refused'; END IF;
      UPDATE videos SET options_json=options_json::jsonb||jsonb_build_object('cf_schedule_date',run_day) WHERE id=identity;
      queued:=queued||jsonb_build_array(jsonb_build_object('video_id',identity));budget:=budget-per_video;
    END LOOP;
  END LOOP;
  INSERT INTO schedule_runs(run_date,status,queued,error,created_at,updated_at) VALUES(run_day,'done',jsonb_array_length(queued),'',now(),now());
  RETURN jsonb_build_object('videos',queued);
END; $$;
REVOKE ALL ON FUNCTION public.cf_schedule(text,text,jsonb) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.cf_schedule(text,text,jsonb) TO service_role;
