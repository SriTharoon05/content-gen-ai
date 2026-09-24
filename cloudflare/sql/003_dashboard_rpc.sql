-- Read-only native dashboard surface. Never expose runtime keys or provider checkpoints.
CREATE OR REPLACE FUNCTION public.cf_dashboard(p_action text,p_task_id text,p_payload jsonb DEFAULT '{}'::jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE data jsonb;
BEGIN
  IF p_action='channels' THEN
    RETURN jsonb_build_object('channels',coalesce((SELECT jsonb_agg(jsonb_build_object(
      'slug',slug,'name',name,'enabled',enabled,'niche',niche,
      'supported',coalesce(strategy_json->>'conversation','false')<>'true' AND coalesce(overrides_json->>'primary_language','en')='en'))
      FROM public.channels),'[]'::jsonb));
  ELSIF p_action='videos' THEN
    SELECT coalesce(jsonb_agg(row_data),'[]'::jsonb) INTO data FROM (
      SELECT jsonb_build_object('id',v.id,'channel',v.channel_slug,'title',v.title,'state',v.state,
        'stage_detail',v.stage_detail,'error',v.error,'output',v.output_path,'created_at',v.created_at,
        'duration_seconds',v.duration_seconds,'image_count',v.image_count,'approved',v.approved,
        'narration_url',v.narration_path,'preview_revision',coalesce((v.options_json->>'preview_revision')::integer,0),
        'active_edit_id',v.options_json->>'active_edit_id',
        'music',jsonb_build_object('music_track',coalesce(v.options_json->'last_edit_input'->>'music_track',v.spec_json->>'music_track',v.options_json->>'music_track',''),
          'music_volume_pct',coalesce(v.options_json::jsonb->'last_edit_input'->'music_volume_pct',v.options_json::jsonb->'music_volume_pct','30'::jsonb),
          'music_start_seconds',coalesce(v.options_json::jsonb->'last_edit_input'->'music_start_seconds','0'::jsonb),
          'music_end_seconds',coalesce(v.options_json::jsonb->'last_edit_input'->'music_end_seconds','0'::jsonb),
          'ducking',coalesce(v.spec_json::jsonb->'ducking','true'::jsonb)),
        'language',v.language,'description',v.description,'instagram_caption',v.instagram_caption,'hashtags',v.hashtags,
        'publishing_mode',v.options_json->>'publishing_mode',
        'publish_error',v.options_json->>'cf_publish_error',
        'publications',coalesce((SELECT jsonb_agg(jsonb_build_object('video_id',p.video_id,'platform',p.platform,'status',p.status,
          'remote_id',p.remote_id,'error',p.error,'actual_privacy',p.actual_privacy)) FROM publications p WHERE p.video_id=v.id),'[]'::jsonb),
        'generation_timing',jsonb_build_object('status',CASE WHEN j.status='cf_done' THEN 'complete' WHEN j.status='cf_failed' THEN 'failed' ELSE 'running' END,
          'started_at',v.created_at,'finished_at',CASE WHEN j.status IN ('cf_done','cf_failed') THEN j.updated_at ELSE NULL END,
          'elapsed_seconds',extract(epoch FROM (CASE WHEN j.status IN ('cf_done','cf_failed') THEN j.updated_at ELSE now() END-v.created_at)))) AS row_data
      FROM public.videos v LEFT JOIN public.jobs j ON j.id=v.id
      WHERE v.options_json->>'execution_backend'='cloudflare-generation'
      ORDER BY v.created_at DESC LIMIT 50
    ) q;
    RETURN jsonb_build_object('videos',data);
  END IF;
  RETURN '{"error":"Unsupported dashboard action","status":400}'::jsonb;
END; $$;
REVOKE ALL ON FUNCTION public.cf_dashboard(text,text,jsonb) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.cf_dashboard(text,text,jsonb) TO service_role;
