-- Native channel/settings administration. Service role only; API caller authenticates admin.
CREATE OR REPLACE FUNCTION public.cf_admin(p_action text,p_task_id text,p_payload jsonb DEFAULT '{}'::jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE c public.channels%ROWTYPE; current_config jsonb; result_config jsonb; patch jsonb;
  secret_keys jsonb; incoming jsonb; resolved jsonb; item jsonb; k text; section text;
  revision text; idx integer; data jsonb;
BEGIN
  IF jsonb_typeof(p_payload)<>'object' OR octet_length(p_payload::text)>131072 THEN
    RETURN '{"status":422,"error":"Invalid admin payload"}'::jsonb;
  END IF;
  IF p_action IN ('settings_get','settings_save') THEN
    PERFORM pg_advisory_xact_lock(hashtext('story-shorts:runtime-settings'));
    SELECT value_json::jsonb,updated_at::text INTO current_config,revision FROM public.app_settings WHERE key='runtime' FOR UPDATE;
    current_config:=coalesce(current_config,'{}'::jsonb);
    IF p_action='settings_save' THEN
      IF (p_payload->>'revision') IS DISTINCT FROM revision THEN RETURN '{"status":409,"error":"Settings changed; reload and retry"}'::jsonb; END IF;
      patch:=p_payload->'patch';
      IF jsonb_typeof(patch)<>'object' OR EXISTS(SELECT 1 FROM jsonb_object_keys(patch) x WHERE x NOT IN
        ('keys','models','video','voice','languages','align','music','reuse','pricing','runtime','schedule','publishing')) THEN
        RETURN '{"status":422,"error":"Invalid settings fields"}'::jsonb;
      END IF;
      -- Schedule ownership is handled exclusively by cf_schedule.
      IF patch?'schedule' AND (coalesce(current_config->'schedule','{}')||(patch->'schedule')) IS DISTINCT FROM current_config->'schedule' THEN
        RETURN '{"status":422,"error":"Use the schedule endpoint"}'::jsonb;
      END IF;
      secret_keys:=coalesce(current_config->'keys','{}'::jsonb);
      IF patch?'keys' THEN
        IF jsonb_typeof(patch->'keys')<>'object' OR EXISTS(SELECT 1 FROM jsonb_object_keys(patch->'keys') x WHERE x NOT IN
          ('gemini_free','gemini_paid','gemini_audio_paid','pollinations','groq')) THEN RETURN '{"status":422,"error":"Invalid credential fields"}'::jsonb; END IF;
        FOR k,incoming IN SELECT * FROM jsonb_each(patch->'keys') LOOP
          IF incoming='null'::jsonb THEN CONTINUE; END IF;
          IF k='gemini_audio_paid' THEN
            IF jsonb_typeof(incoming)<>'string' THEN RETURN '{"status":422,"error":"Invalid credential value"}'::jsonb; END IF;
            IF incoming#>>'{}'='__keep__' THEN CONTINUE; END IF;
            IF left(incoming#>>'{}',8)='__keep__' THEN RETURN '{"status":422,"error":"Unknown credential placeholder"}'::jsonb; END IF;
            secret_keys:=jsonb_set(secret_keys,ARRAY[k],incoming);
          ELSE
            IF jsonb_typeof(incoming)<>'array' OR jsonb_array_length(incoming)>50 THEN RETURN '{"status":422,"error":"Invalid credential list"}'::jsonb; END IF;
            resolved:='[]'::jsonb;
            FOR item IN SELECT value FROM jsonb_array_elements(incoming) LOOP
              IF jsonb_typeof(item)<>'string' THEN RETURN '{"status":422,"error":"Invalid credential value"}'::jsonb; END IF;
              IF left(item#>>'{}',8)='__keep__' THEN
                IF (item#>>'{}') !~ '^__keep__:[0-9]{1,3}$' THEN RETURN '{"status":422,"error":"Unknown credential placeholder"}'::jsonb; END IF;
                idx:=split_part(item#>>'{}',':',2)::integer;
                IF secret_keys->k->idx IS NULL THEN RETURN '{"status":409,"error":"Credential list changed; reload"}'::jsonb; END IF;
                resolved:=resolved||jsonb_build_array(secret_keys->k->idx);
              ELSIF btrim(item#>>'{}')<>'' THEN resolved:=resolved||jsonb_build_array(btrim(item#>>'{}')); END IF;
            END LOOP;
            secret_keys:=jsonb_set(secret_keys,ARRAY[k],resolved);
          END IF;
        END LOOP;
      END IF;
      FOR section,incoming IN SELECT * FROM jsonb_each(patch-'keys'-'schedule') LOOP
        IF jsonb_typeof(incoming)<>'object' THEN RETURN '{"status":422,"error":"Invalid settings section"}'::jsonb; END IF;
        current_config:=jsonb_set(current_config,ARRAY[section],coalesce(current_config->section,'{}')||incoming);
      END LOOP;
      current_config:=jsonb_set(current_config,'{keys}',secret_keys);
      INSERT INTO public.app_settings(key,value_json,updated_at) VALUES('runtime',current_config,clock_timestamp())
      ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at;
      SELECT updated_at::text INTO revision FROM public.app_settings WHERE key='runtime';
    END IF;
    -- Do not send secrets even to the Worker. Array placeholders refer to existing positions.
    result_config:=current_config-'keys';secret_keys:='{}'::jsonb;
    FOREACH k IN ARRAY ARRAY['gemini_free','gemini_paid','pollinations','groq'] LOOP
      SELECT coalesce(jsonb_agg('__keep__:'||(ordinality-1)::text),'[]'::jsonb) INTO incoming
        FROM jsonb_array_elements(coalesce(current_config->'keys'->k,'[]')) WITH ORDINALITY;
      secret_keys:=jsonb_set(secret_keys,ARRAY[k],incoming);
    END LOOP;
    secret_keys:=secret_keys||jsonb_build_object('gemini_audio_paid',CASE WHEN coalesce(current_config#>>'{keys,gemini_audio_paid}','')<>'' THEN '__keep__' ELSE '' END);
    RETURN jsonb_build_object('settings',result_config||jsonb_build_object('keys',secret_keys),'revision',revision);
  END IF;
  IF p_action='channels' THEN
    SELECT coalesce(jsonb_agg(public.cf_admin('channel_get',slug)->'channel' ORDER BY name),'[]') INTO data FROM public.channels;
    RETURN jsonb_build_object('channels',data);
  END IF;
  IF p_task_id !~ '^[a-z][a-z0-9-]{1,63}$' THEN RETURN '{"status":422,"error":"Invalid channel identifier"}'::jsonb; END IF;
  IF p_action='channel_create' THEN
    IF coalesce(length(btrim(p_payload->>'name')),0) NOT BETWEEN 1 AND 120 OR coalesce(length(btrim(p_payload->>'niche')),0) NOT BETWEEN 1 AND 3000 THEN RETURN '{"status":422,"error":"Name and niche required"}'::jsonb; END IF;
    INSERT INTO public.channels(slug,name,tagline,niche,enabled,strategy_json,overrides_json,updated_at)
      VALUES(p_task_id,btrim(p_payload->>'name'),coalesce(p_payload->>'tagline',''),btrim(p_payload->>'niche'),true,
        jsonb_build_object('instructions',coalesce(p_payload->>'instructions',''),'topic_seeds',coalesce(p_payload->'topic_seeds','[]'),
          'conversation',coalesce(p_payload->'conversation','false'),'audience','general adult'),'{}',now()) ON CONFLICT(slug) DO NOTHING;
    IF NOT FOUND THEN RETURN '{"status":409,"error":"Channel identifier already exists"}'::jsonb; END IF;
    -- Apply remaining optional fields consistently with update.
    p_action:='channel_update';
  END IF;
  SELECT * INTO c FROM public.channels WHERE slug=p_task_id FOR UPDATE;
  IF NOT FOUND THEN RETURN '{"status":404,"error":"Unknown channel"}'::jsonb; END IF;
  IF p_action='channel_delete' THEN
    -- Historical videos, connections and memories are never cascaded by this endpoint.
    IF EXISTS(SELECT 1 FROM public.social_connections WHERE channel_slug=p_task_id)
      OR EXISTS(SELECT 1 FROM public.meta_connections WHERE channel_slug=p_task_id)
      OR EXISTS(SELECT 1 FROM public.oauth_attempts WHERE channel_slug=p_task_id AND expires_at>now())
      OR EXISTS(SELECT 1 FROM public.channel_memory WHERE channel_slug=p_task_id)
      OR EXISTS(SELECT 1 FROM public.story_history WHERE channel_slug=p_task_id)
      OR EXISTS(SELECT 1 FROM public.registry_assets WHERE channel_slug=p_task_id)
      OR EXISTS(SELECT 1 FROM public.knowledge_frontier WHERE channel_slug=p_task_id)
      OR EXISTS(SELECT 1 FROM public.verification_backlog WHERE channel_slug=p_task_id) THEN
      RETURN '{"status":409,"error":"Channel has linked records; disable it instead"}'::jsonb;
    END IF;
    DELETE FROM public.channels WHERE slug=p_task_id;
    RETURN '{"deleted":true}'::jsonb;
  ELSIF p_action='channel_update' THEN
    IF p_payload?'name' THEN c.name:=p_payload->>'name'; END IF;
    IF p_payload?'tagline' THEN c.tagline:=p_payload->>'tagline'; END IF;
    IF p_payload?'niche' THEN c.niche:=p_payload->>'niche'; END IF;
    IF p_payload?'enabled' THEN c.enabled:=(p_payload->>'enabled')::boolean; END IF;
    IF p_payload?'overrides' THEN c.overrides_json:=p_payload->'overrides'; END IF;
    data:=coalesce(c.strategy_json::jsonb,'{}');
    FOREACH k IN ARRAY ARRAY['instructions','conversation','topic_seeds','voice_name'] LOOP
      IF p_payload?k THEN data:=jsonb_set(data,ARRAY[k],p_payload->k); END IF;
    END LOOP;
    UPDATE public.channels SET name=c.name,tagline=c.tagline,niche=c.niche,enabled=c.enabled,
      strategy_json=data,overrides_json=c.overrides_json,updated_at=now() WHERE slug=p_task_id;
    c.strategy_json:=data;
  ELSIF p_action<>'channel_get' THEN RETURN '{"status":400,"error":"Unsupported admin action"}'::jsonb;
  END IF;
  RETURN jsonb_build_object('channel',jsonb_build_object('slug',c.slug,'name',c.name,'tagline',c.tagline,'niche',c.niche,
    'enabled',c.enabled,'strategy',(SELECT coalesce(jsonb_object_agg(key,value),'{}') FROM jsonb_each(coalesce(c.strategy_json::jsonb,'{}')) WHERE key IN
      ('instructions','narrative','visual_style','voice','audience','voice_name','conversation','topic_seeds')
      AND (jsonb_typeof(value)='string' AND key NOT IN ('conversation','topic_seeds')
        OR key='conversation' AND jsonb_typeof(value)='boolean'
        OR key='topic_seeds' AND jsonb_typeof(value)='array' AND NOT jsonb_path_exists(value,'$[*] ? (@.type() != "string")'))),
    'overrides',(SELECT coalesce(jsonb_object_agg(key,value),'{}') FROM jsonb_each(coalesce(c.overrides_json::jsonb,'{}')) WHERE key IN
      ('publishing_mode','publish_platforms','primary_language','speech_tempo','music_volume_pct','min_shots','max_shots','target_seconds','languages','voice','pace_note','tone','style_note','must_include','must_avoid','agent_directs_voice','music_enabled','music_track','ducking')
      AND (key IN ('publish_platforms','languages') AND jsonb_typeof(value)='array' AND NOT jsonb_path_exists(value,'$[*] ? (@.type() != "string")')
        OR key IN ('speech_tempo','music_volume_pct','min_shots','max_shots','target_seconds') AND jsonb_typeof(value)='number'
        OR key IN ('agent_directs_voice','music_enabled','ducking') AND jsonb_typeof(value)='boolean'
        OR key IN ('publishing_mode','primary_language','voice','pace_note','tone','style_note','must_include','must_avoid','music_track') AND jsonb_typeof(value)='string')),
    'stats',jsonb_build_object('videos',(SELECT count(*) FROM public.videos WHERE channel_slug=c.slug),
      'ready',(SELECT count(*) FROM public.videos WHERE channel_slug=c.slug AND state='READY'))));
EXCEPTION WHEN foreign_key_violation THEN RETURN '{"status":409,"error":"Channel has linked records; disable it instead"}'::jsonb;
END; $$;
REVOKE ALL ON FUNCTION public.cf_admin(text,text,jsonb) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.cf_admin(text,text,jsonb) TO service_role;
