-- Additive media revisions; all media work remains in the canonical CircleCI worker.
CREATE TABLE IF NOT EXISTS public.cf_media_edits (
  id text PRIMARY KEY, video_id text NOT NULL REFERENCES public.videos(id),
  status text NOT NULL DEFAULT 'queued', revision integer NOT NULL,
  input_json jsonb NOT NULL, snapshot_json jsonb NOT NULL,
  result_json jsonb NOT NULL DEFAULT '{}', error text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS cf_one_active_edit ON public.cf_media_edits(video_id) WHERE status IN ('queued','running');
CREATE TABLE IF NOT EXISTS public.cf_music_assets (
  track_id text PRIMARY KEY REFERENCES public.music_tracks(id), sha256 text NOT NULL CHECK(sha256 ~ '^[a-f0-9]{64}$')
);
ALTER TABLE public.cf_media_edits ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.cf_music_assets ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.cf_media_edits,public.cf_music_assets FROM anon,authenticated;

CREATE OR REPLACE FUNCTION public.cf_edit(p_action text,p_task_id text DEFAULT '',p_payload jsonb DEFAULT '{}')
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE e public.cf_media_edits%ROWTYPE; v public.videos%ROWTYPE; t public.render_tasks%ROWTYPE;
  m public.music_tracks%ROWTYPE; inp jsonb; snap jsonb; revision integer; checksum text; manifest jsonb; base_manifest jsonb;
BEGIN
  IF p_action='music-list' THEN
    RETURN jsonb_build_object('tracks',coalesce((SELECT jsonb_agg(to_jsonb(x)) FROM
      (SELECT track.*,a.sha256 FROM public.music_tracks track LEFT JOIN public.cf_music_assets a ON a.track_id=track.id
       WHERE NOT track.archived ORDER BY track.category,track.name)x),'[]'::jsonb),
      'max_intensity',coalesce((SELECT (value_json->'music'->>'max_intensity')::float FROM public.app_settings WHERE key='runtime'),.25),
      'categories',(SELECT jsonb_agg(category ORDER BY category) FROM
        (SELECT unnest(ARRAY['neutral','emotion','drama','suspense','thriller']) AS category UNION SELECT category FROM public.music_tracks WHERE NOT archived)c));
  END IF;
  IF p_task_id !~ '^[a-f0-9]{32}$' THEN RETURN '{"error":"Invalid ID","status":400}'::jsonb;END IF;
  IF p_action='music-checksum' THEN
    IF coalesce(p_payload->>'sha256','') !~ '^[a-f0-9]{64}$' THEN RETURN '{"error":"Invalid checksum","status":400}'::jsonb;END IF;
    PERFORM 1 FROM public.music_tracks WHERE id=p_task_id AND NOT archived AND path=p_payload->>'path';
    IF NOT FOUND THEN RETURN '{"error":"Music changed or was removed","status":409}'::jsonb;END IF;
    INSERT INTO public.cf_music_assets VALUES(p_task_id,p_payload->>'sha256') ON CONFLICT(track_id) DO UPDATE SET sha256=excluded.sha256;
    RETURN '{"ok":true}'::jsonb;
  ELSIF p_action='music-save' THEN
    IF coalesce(p_payload->>'sha256','') !~ '^[a-f0-9]{64}$' OR coalesce(p_payload->>'category','') !~ '^[a-z0-9_-]{1,48}$'
      OR length(coalesce(p_payload->>'name','')) NOT BETWEEN 1 AND 160
      OR (p_payload->>'duration_seconds')::float<=0
      OR (p_payload->>'trim_start')::float<0
      OR (p_payload->>'trim_end')::float>(p_payload->>'duration_seconds')::float
      OR (p_payload->>'trim_end')::float-(p_payload->>'trim_start')::float<3
    THEN RETURN '{"error":"Invalid music metadata","status":400}'::jsonb;END IF;
    INSERT INTO public.music_tracks(id,name,filename,path,category,mood,tempo,duration_seconds,trim_start,trim_end,default_volume_pct,channels,rights_cleared,notes,created_at,archived)
    VALUES(p_task_id,p_payload->>'name',p_task_id,p_payload->>'path',p_payload->>'category','',0,
      (p_payload->>'duration_seconds')::float,(p_payload->>'trim_start')::float,(p_payload->>'trim_end')::float,
      (p_payload->>'default_volume_pct')::integer,'[]',true,'',now(),false)
    ON CONFLICT(id) DO UPDATE SET name=excluded.name,archived=false,default_volume_pct=excluded.default_volume_pct;
    INSERT INTO public.cf_music_assets VALUES(p_task_id,p_payload->>'sha256') ON CONFLICT(track_id) DO NOTHING;
    RETURN jsonb_build_object('id',p_task_id);
  ELSIF p_action='music-delete' THEN
    -- Archive only: existing videos, in-flight snapshots and reusable history keep their source.
    UPDATE public.music_tracks SET archived=true WHERE id=p_task_id;
    RETURN jsonb_build_object('archived',true,'media_preserved',true);
  END IF;
  IF p_action='create' THEN
    -- Same lock names as publication paths: publication must also verify active edits/revision.
    PERFORM pg_advisory_xact_lock(hashtext('publish:'||(p_payload->>'video_id')||':youtube'));
    PERFORM pg_advisory_xact_lock(hashtext('publish:'||(p_payload->>'video_id')||':instagram'));
    SELECT * INTO v FROM public.videos WHERE id=p_payload->>'video_id' FOR UPDATE;
    IF NOT FOUND THEN RETURN '{"error":"Video not found","status":404}'::jsonb;END IF;
    inp:=p_payload->'input';
    SELECT * INTO e FROM public.cf_media_edits WHERE id=p_task_id;
    IF FOUND THEN
      IF e.video_id<>v.id OR e.input_json IS DISTINCT FROM inp THEN RETURN '{"error":"Idempotency key used for another edit","status":409}'::jsonb;END IF;
      RETURN jsonb_build_object('id',e.id,'status',e.status,'revision',e.revision);
    END IF;
    revision:=coalesce((v.options_json->>'preview_revision')::integer,0);
    IF coalesce(inp->>'operation','') NOT IN ('rerender','speed','bgm') OR NOT inp?'revision' OR (inp->>'revision')::integer<>revision
    THEN RETURN '{"error":"Stale preview revision or invalid edit","status":409}'::jsonb;END IF;
    IF inp->>'operation'='speed' AND (NOT inp?'playback_rate' OR (inp->>'playback_rate')::float NOT BETWEEN .75 AND 1.25)
    THEN RETURN '{"error":"Invalid playback rate","status":400}'::jsonb;END IF;
    IF v.state NOT IN ('AWAITING_APPROVAL','READY','CF_EDIT_FAILED') OR coalesce(v.output_path,'')=''
      OR EXISTS(SELECT 1 FROM public.cf_media_edits WHERE video_id=v.id AND status IN ('queued','running'))
      OR EXISTS(SELECT 1 FROM public.publications WHERE video_id=v.id AND status IN ('uploading','pending','committing','uncertain','published'))
      OR EXISTS(SELECT 1 FROM public.jobs WHERE video_id=v.id AND status IN ('queued','running','waiting_render','cf_generating'))
    THEN RETURN '{"error":"Video is generating, editing or publishing; cannot change this revision","status":409}'::jsonb;END IF;
    SELECT * INTO t FROM public.render_tasks WHERE video_id=v.id AND status='succeeded'
      AND manifest_json->>'operation' IN ('assemble','assemble_script','remix')
      AND (result_json->>'url'=v.output_path OR continuation_json->>'backend' IS DISTINCT FROM 'cloudflare-pilot')
      ORDER BY updated_at DESC LIMIT 1;
    IF NOT FOUND THEN RETURN '{"error":"Reusable canonical render checkpoint is unavailable","status":409}'::jsonb;END IF;
    manifest:=t.manifest_json::jsonb;
    IF manifest->>'operation'='remix' AND (manifest->'images' IS NULL OR manifest->'images'='null'::jsonb) THEN
      SELECT previous.manifest_json::jsonb INTO base_manifest FROM public.render_tasks previous
        WHERE previous.video_id=v.id AND previous.status='succeeded' AND previous.manifest_json->>'operation' IN ('assemble','assemble_script')
        AND previous.created_at<=t.created_at ORDER BY previous.updated_at DESC LIMIT 1;
      IF base_manifest IS NULL THEN RETURN '{"error":"Original image checkpoint is missing for this legacy remix","status":409}'::jsonb;END IF;
      manifest:=base_manifest||manifest||jsonb_build_object('files',coalesce(base_manifest->'files','{}'::jsonb)||coalesce(manifest->'files','{}'::jsonb));
    END IF;
    snap:=jsonb_build_object('manifest',manifest,'output',v.output_path,'output_sha256',CASE WHEN t.result_json->>'url'=v.output_path THEN t.result_json->'metrics'->>'sha256' ELSE NULL END,'duration',v.duration_seconds);
    IF coalesce(inp->>'music_track','')<>'' THEN
      SELECT * INTO m FROM public.music_tracks WHERE id=inp->>'music_track' AND NOT archived;
      IF NOT FOUND THEN RETURN '{"error":"Music track unavailable","status":409}'::jsonb;END IF;
      SELECT sha256 INTO checksum FROM public.cf_music_assets WHERE track_id=m.id;
      IF checksum IS NULL THEN RETURN '{"error":"Legacy music needs checksum migration before remote editing","status":409}'::jsonb;END IF;
      snap:=snap||jsonb_build_object('music',to_jsonb(m)||jsonb_build_object('sha256',checksum));
    END IF;
    INSERT INTO public.cf_media_edits(id,video_id,revision,input_json,snapshot_json) VALUES(p_task_id,v.id,revision+1,inp,snap);
    UPDATE public.videos SET state='CF_EDITING',approved=false,error='',stage_detail='Queued asset-only media edit',
      options_json=options_json::jsonb||jsonb_build_object('preview_revision',revision+1,'active_edit_id',p_task_id,'reviewed_output','','force_review',true,'auto_publish',false),updated_at=now() WHERE id=v.id;
    RETURN jsonb_build_object('id',p_task_id,'status','queued','revision',revision+1);
  END IF;
  SELECT * INTO e FROM public.cf_media_edits WHERE id=p_task_id FOR UPDATE;
  IF NOT FOUND THEN RETURN '{"error":"Unknown edit","status":404}'::jsonb;END IF;
  IF p_action='public' THEN RETURN jsonb_build_object('id',e.id,'video_id',e.video_id,'status',e.status,'revision',e.revision,'error',e.error,'result',e.result_json);END IF;
  IF p_action='claim' THEN
    UPDATE public.cf_media_edits SET status=CASE WHEN status='queued' THEN 'running' ELSE status END,updated_at=now() WHERE id=e.id;
    RETURN jsonb_build_object('id',e.id,'video_id',e.video_id,'status',e.status,'revision',e.revision,'input',e.input_json,'snapshot',e.snapshot_json);
  END IF;
  SELECT * INTO v FROM public.videos WHERE id=e.video_id FOR UPDATE;
  IF p_action='complete' THEN
    IF e.status='succeeded' THEN RETURN e.result_json;END IF;
    IF e.status<>'running' OR v.options_json->>'active_edit_id' IS DISTINCT FROM e.id OR (v.options_json->>'preview_revision')::integer<>e.revision
    THEN RETURN '{"error":"Stale media completion","status":409}'::jsonb;END IF;
    SELECT * INTO t FROM public.render_tasks WHERE id=p_payload->>'task_id' AND video_id=e.video_id AND status='succeeded';
    IF NOT FOUND OR t.manifest_json->>'operation' NOT IN ('assemble','assemble_script','remix') THEN
      RETURN '{"error":"Completed video render required","status":409}'::jsonb;END IF;
    -- Bind final task to this edit, not an unrelated successful historical output.
    IF t.id<>substring(pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(e.id||':render','UTF8')),'hex') from 1 for 32)
    THEN RETURN '{"error":"Render belongs to another edit","status":409}'::jsonb;END IF;
    snap:=jsonb_build_object('url',t.result_json->>'url','revision',e.revision,'duration',coalesce(t.result_json->'duration',t.result_json->'metrics'->'duration'),'images_added',0);
    UPDATE public.cf_media_edits SET status='succeeded',result_json=snap,error='',updated_at=now() WHERE id=e.id;
    UPDATE public.videos SET output_path=t.result_json->>'url',duration_seconds=(snap->>'duration')::float,
      narration_path=coalesce(t.manifest_json->'files'->'narration.wav'->>'url',narration_path),narration_seconds=(snap->>'duration')::float,
      approved=false,state='AWAITING_APPROVAL',progress=99,error='',
      spec_json=spec_json::jsonb||jsonb_build_object('music_track',t.manifest_json->'music'->>'id','music_intensity',coalesce(t.manifest_json::jsonb->'intensity','0'::jsonb),'ducking',t.manifest_json->'ducking'),
      stage_detail='Media edit complete; ready for approval',options_json=(options_json::jsonb-'active_edit_id')||jsonb_build_object('preview_required',false,'last_edit_id',e.id,'last_edit_input',e.input_json),updated_at=now() WHERE id=e.video_id;
    RETURN snap;
  ELSIF p_action='fail' THEN
    IF e.status IN ('succeeded','failed') THEN RETURN '{"ok":true}'::jsonb;END IF;
    UPDATE public.cf_media_edits SET status='failed',error=left(p_payload->>'error',450),updated_at=now() WHERE id=e.id;
    UPDATE public.videos SET state='CF_EDIT_FAILED',error=left(p_payload->>'error',450),approved=false,
      options_json=options_json::jsonb-'active_edit_id',updated_at=now() WHERE id=e.video_id AND options_json->>'active_edit_id'=e.id;
    RETURN '{"ok":true}'::jsonb;
  END IF;
  RETURN '{"error":"Unknown edit operation","status":400}'::jsonb;
END; $$;
REVOKE ALL ON FUNCTION public.cf_edit(text,text,jsonb) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.cf_edit(text,text,jsonb) TO service_role;
NOTIFY pgrst,'reload schema';
