-- Shared canonical tables; service-role-only control plane. Tokens never go to dashboard.
CREATE OR REPLACE FUNCTION public.cf_publish(p_action text,p_task_id text,p_payload jsonb DEFAULT '{}'::jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE v videos%ROWTYPE; p publications%ROWTYPE; a oauth_attempts%ROWTYPE; target_platform text:=p_payload->>'platform'; opts jsonb; conf jsonb; fresh boolean:=false;
BEGIN
 IF p_action='pending_dispatch' THEN
  RETURN jsonb_build_object('videos',coalesce((SELECT jsonb_agg(id) FROM (SELECT id FROM videos WHERE options_json->>'execution_backend'='cloudflare-generation' AND options_json->>'cf_publish_ready'='true' AND state IN ('READY','AWAITING_APPROVAL') ORDER BY created_at LIMIT 1) q),'[]'::jsonb));
 ELSIF p_action='dispatched' THEN
  UPDATE videos SET options_json=coalesce(options_json::jsonb,'{}')||jsonb_build_object('cf_publish_ready',NOT coalesce((p_payload->>'clear')::boolean,false),'cf_publish_error',coalesce(p_payload->>'error','')) WHERE id=p_task_id;
  RETURN '{}'::jsonb;
 ELSIF p_action='connections' THEN
  RETURN jsonb_build_object('youtube_privacy',coalesce((SELECT value_json->'publishing'->>'youtube_privacy' FROM app_settings WHERE key='runtime'),'private'),
   'connections',coalesce((SELECT jsonb_agg(jsonb_build_object('channel',c.slug,
   'youtube',coalesce(s.refresh_token_encrypted,'')<>'','youtube_channel_name',s.remote_channel_name,'youtube_channel_id',s.remote_channel_id,
   'instagram',coalesce(m.token_encrypted,'')<>'' AND m.login_mode='instagram' AND m.token_expires_at>now(),
   'instagram_account_name',m.account_name,'instagram_account_id',m.account_id,'token_expires_at',m.token_expires_at))
   FROM channels c LEFT JOIN social_connections s ON s.channel_slug=c.slug LEFT JOIN meta_connections m ON m.channel_slug=c.slug),'[]'::jsonb));
 ELSIF p_action='credentials' THEN
  RETURN jsonb_build_object('youtube',(SELECT to_jsonb(s) FROM social_connections s WHERE channel_slug=p_task_id),
    'instagram',(SELECT to_jsonb(m) FROM meta_connections m WHERE channel_slug=p_task_id));
 ELSIF p_action='oauth_ticket' THEN
  IF NOT EXISTS(SELECT 1 FROM channels WHERE slug=p_payload->>'channel') THEN RETURN '{"status":404,"error":"Unknown channel"}'::jsonb; END IF;
  DELETE FROM oauth_attempts WHERE expires_at<now();
  INSERT INTO oauth_attempts(id,channel_slug,verifier,phase,expires_at) VALUES(p_task_id,p_payload->>'channel','',p_payload->>'phase',now()+interval '10 minutes');
  RETURN '{}'::jsonb;
 ELSIF p_action='oauth_start' OR p_action='oauth_consume' THEN
  SELECT * INTO a FROM oauth_attempts WHERE id=p_task_id FOR UPDATE;
  IF a.id IS NULL OR a.phase<>p_payload->>'phase' OR a.expires_at<now() OR
    (p_payload ? 'nonce' AND a.verifier<>p_payload->>'nonce') THEN RETURN '{"status":400,"error":"OAuth session expired or browser mismatch"}'::jsonb; END IF;
  DELETE FROM oauth_attempts WHERE id=a.id;
  IF p_action='oauth_start' THEN
   INSERT INTO oauth_attempts(id,channel_slug,verifier,phase,expires_at) VALUES(p_payload->>'state',a.channel_slug,p_payload->>'verifier',p_payload->>'next_phase',now()+interval '10 minutes');
  END IF;
  RETURN jsonb_build_object('channel',a.channel_slug,'verifier',a.verifier);
 ELSIF p_action='oauth_save' THEN
  IF target_platform='youtube' THEN
   INSERT INTO social_connections(channel_slug,refresh_token_encrypted,remote_channel_id,remote_channel_name,updated_at)
    VALUES(p_task_id,p_payload->>'encrypted',p_payload->>'remote_id',p_payload->>'name',now())
    ON CONFLICT(channel_slug) DO UPDATE SET refresh_token_encrypted=excluded.refresh_token_encrypted,remote_channel_id=excluded.remote_channel_id,remote_channel_name=excluded.remote_channel_name,updated_at=now();
  ELSIF target_platform='instagram' THEN
   INSERT INTO meta_connections(channel_slug,token_encrypted,account_id,account_name,page_id,login_mode,token_expires_at,pending_encrypted,pending_until,updated_at)
    VALUES(p_task_id,p_payload->>'encrypted',p_payload->>'remote_id',p_payload->>'name','','instagram',(p_payload->>'expires_at')::timestamptz,'',NULL,now())
    ON CONFLICT(channel_slug) DO UPDATE SET token_encrypted=excluded.token_encrypted,account_id=excluded.account_id,account_name=excluded.account_name,login_mode='instagram',page_id='',token_expires_at=excluded.token_expires_at,pending_encrypted='',pending_until=NULL,updated_at=now();
  ELSE RETURN '{"status":400,"error":"Invalid platform"}'::jsonb; END IF;
  RETURN '{}'::jsonb;
 ELSIF p_action='policy' THEN
  SELECT * INTO v FROM videos WHERE id=p_task_id;
  SELECT value_json INTO conf FROM app_settings WHERE key='runtime';
  RETURN jsonb_build_object('options',v.options_json,'approved',v.approved,'publishing',conf->'publishing','schedule',conf->'schedule');
 ELSIF p_action='status' THEN
  RETURN jsonb_build_object('publications',coalesce((SELECT jsonb_agg(to_jsonb(x)-'session_url') FROM publications x WHERE video_id=p_task_id),'[]'::jsonb));
 ELSIF p_action IN ('approve','request') THEN
  SELECT * INTO v FROM videos WHERE id=p_task_id FOR UPDATE;
  IF v.id IS NULL OR v.state NOT IN ('READY','AWAITING_APPROVAL') OR coalesce(v.output_path,'')='' THEN RETURN '{"status":409,"error":"Video is not ready for publishing"}'::jsonb; END IF;
  IF EXISTS(SELECT 1 FROM jobs WHERE video_id=v.id AND status IN ('queued','running','waiting_render','cf_editing') AND stage NOT IN ('produce_video','publish'))
   OR EXISTS(SELECT 1 FROM render_tasks WHERE video_id=v.id AND status IN ('queued','running')) THEN RETURN '{"status":409,"error":"Wait for active video edits"}'::jsonb; END IF;
  opts:=coalesce(v.options_json,'{}');
  SELECT coalesce(value_json->'publishing','{}') INTO conf FROM app_settings WHERE key='runtime';
  IF p_action='approve' THEN
   IF p_payload->>'output' IS DISTINCT FROM v.output_path THEN RETURN '{"status":409,"error":"Preview changed; review the latest output"}'::jsonb; END IF;
   UPDATE videos SET approved=true,state='READY',progress=100,options_json=opts||jsonb_build_object('preview_required',false,'reviewed_output',v.output_path),updated_at=now() WHERE id=v.id;
   RETURN '{"approved":true,"state":"READY"}'::jsonb;
  END IF;
  IF target_platform NOT IN ('youtube','instagram') OR target_platform IS NULL THEN RETURN '{"status":400,"error":"Invalid platform"}'::jsonb; END IF;
  IF NOT v.approved AND (coalesce((opts->>'force_review')::boolean,false) OR opts->>'publishing_mode'='review' OR (coalesce(opts->>'publishing_mode','settings')<>'direct' AND coalesce((conf->>'review_before_upload')::boolean,true))) THEN RETURN '{"status":409,"error":"Review and approve first"}'::jsonb; END IF;
  IF coalesce((opts->>'preview_required')::boolean,false) AND opts->>'reviewed_output' IS DISTINCT FROM v.output_path THEN RETURN '{"status":409,"error":"Review the latest soundtrack preview"}'::jsonb; END IF;
  IF (target_platform='youtube' AND NOT EXISTS(SELECT 1 FROM social_connections WHERE channel_slug=v.channel_slug AND refresh_token_encrypted<>'')) OR
   (target_platform='instagram' AND NOT EXISTS(SELECT 1 FROM meta_connections WHERE channel_slug=v.channel_slug AND token_encrypted<>'' AND login_mode='instagram' AND token_expires_at>now())) THEN RETURN '{"status":409,"error":"Connect this platform first"}'::jsonb; END IF;
  INSERT INTO publications(id,video_id,platform,status,remote_id,session_url,error,requested_privacy,actual_privacy,updated_at)
   VALUES(md5(v.id||':'||target_platform),v.id,target_platform,'pending','','','',CASE WHEN conf->>'youtube_privacy' IN ('private','public','unlisted') THEN conf->>'youtube_privacy' ELSE 'private' END,'',now()) ON CONFLICT(video_id,platform) DO NOTHING;
  UPDATE videos SET state='READY' WHERE id=v.id;
  SELECT * INTO p FROM publications x WHERE x.video_id=v.id AND x.platform=p_payload->>'platform';
  RETURN to_jsonb(p)-'session_url';
 ELSIF p_action IN ('load','claim','update') THEN
  SELECT * INTO p FROM publications WHERE id=p_task_id FOR UPDATE;
  IF p.id IS NULL THEN RETURN '{"status":404,"error":"Publication not found"}'::jsonb; END IF;
  IF p_action='claim' AND p.status='pending' THEN fresh:=true; UPDATE publications SET status='uploading',updated_at=now() WHERE id=p.id RETURNING * INTO p;
  ELSIF p_action='update' THEN
   UPDATE publications SET status=coalesce(p_payload->>'status',status),session_url=coalesce(p_payload->>'session_url',session_url),remote_id=coalesce(p_payload->>'remote_id',remote_id),actual_privacy=coalesce(p_payload->>'actual_privacy',actual_privacy),error=coalesce(p_payload->>'error',error),updated_at=now() WHERE id=p.id RETURNING * INTO p;
  END IF;
  SELECT * INTO v FROM videos WHERE id=p.video_id;
  RETURN jsonb_build_object('fresh',fresh,'publication',to_jsonb(p),'video',jsonb_build_object('id',v.id,'channel',v.channel_slug,'output',v.output_path,'title',v.title,'description',v.description,'instagram_caption',v.instagram_caption,'hashtags',v.hashtags,'made_for_kids',v.made_for_kids));
 END IF;
 RETURN '{"status":400,"error":"Unknown publishing action"}'::jsonb;
END; $$;
REVOKE ALL ON FUNCTION public.cf_publish(text,text,jsonb) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.cf_publish(text,text,jsonb) TO service_role;
