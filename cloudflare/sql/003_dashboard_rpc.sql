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
        'duration_seconds',v.duration_seconds,'image_count',v.image_count,
        'generation_timing',jsonb_build_object('status',CASE WHEN v.state='AWAITING_APPROVAL' THEN 'complete' WHEN v.state='CF_FAILED' THEN 'failed' ELSE 'running' END,
          'started_at',v.created_at,'finished_at',CASE WHEN v.state IN ('AWAITING_APPROVAL','CF_FAILED') THEN j.updated_at ELSE NULL END,
          'elapsed_seconds',extract(epoch FROM (CASE WHEN v.state IN ('AWAITING_APPROVAL','CF_FAILED') THEN j.updated_at ELSE now() END-v.created_at)))) AS row_data
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
