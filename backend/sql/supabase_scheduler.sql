-- Enable pg_cron / pg_net, then run once in Supabase SQL Editor.
CREATE EXTENSION IF NOT EXISTS pg_cron;
CREATE EXTENSION IF NOT EXISTS pg_net;
-- Create Vault secrets: story_shorts_api_url (https://...onrender.com),
-- story_shorts_scheduler_token (same as Render SCHEDULER_TOKEN).
CREATE OR REPLACE FUNCTION public.story_shorts_wakeup() RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, extensions AS $$
DECLARE settings jsonb; local_time timestamp; start_time time; base text; token text;
        day_key text; claimed boolean; pending boolean; start_stamp timestamp; last_ping timestamptz;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtext('story-shorts:wakeup'));
  SELECT value_json::jsonb INTO settings FROM public.app_settings WHERE key = 'runtime';
  IF NOT COALESCE((settings->'schedule'->>'enabled')::boolean, false) THEN RETURN; END IF;
  local_time := (now() AT TIME ZONE 'UTC') + make_interval(mins => COALESCE((settings->'schedule'->>'timezone_offset_minutes')::int,330));
  start_time := COALESCE(settings->'schedule'->>'run_at','10:00')::time;
  start_stamp := local_time::date + start_time;
  IF local_time < start_stamp THEN start_stamp := start_stamp - interval '1 day'; END IF;
  IF local_time >= start_stamp + interval '4 hours' THEN RETURN; END IF;
  day_key := to_char(start_stamp,'YYYY-MM-DD');
  SELECT EXISTS(SELECT 1 FROM public.schedule_runs WHERE run_date=day_key) INTO claimed;
  SELECT EXISTS(SELECT 1 FROM public.jobs j JOIN public.videos v ON v.id=j.video_id
    WHERE j.status IN ('queued','running') AND v.options_json->>'scheduled_date'=day_key) INTO pending;
  pending := pending OR EXISTS (SELECT 1 FROM public.jobs WHERE stage='verification_backlog'
    AND status IN ('queued','running') AND created_at > now() - interval '4 hours');
  IF claimed AND NOT pending THEN RETURN; END IF;
  SELECT (value_json->>'at')::timestamptz INTO last_ping FROM public.app_settings WHERE key='scheduler-last-ping';
  IF claimed AND last_ping > now() - interval '10 minutes' THEN RETURN; END IF;
  SELECT decrypted_secret INTO base FROM vault.decrypted_secrets WHERE name='story_shorts_api_url';
  SELECT decrypted_secret INTO token FROM vault.decrypted_secrets WHERE name='story_shorts_scheduler_token';
  IF base IS NULL OR token IS NULL THEN RAISE EXCEPTION 'Missing scheduler Vault secrets'; END IF;
  PERFORM net.http_get(url := rtrim(base,'/') || '/health', timeout_milliseconds := 60000);
  PERFORM net.http_post(url := rtrim(base,'/') || '/api/schedule/tick',
    headers := jsonb_build_object('Content-Type','application/json','Authorization','Bearer ' || token),
    body := '{}'::jsonb, timeout_milliseconds := 60000);
  INSERT INTO public.app_settings(key,value_json,updated_at) VALUES ('scheduler-last-ping',jsonb_build_object('at',now()),now())
    ON CONFLICT(key) DO UPDATE SET value_json=EXCLUDED.value_json,updated_at=now();
END $$;
REVOKE ALL ON FUNCTION public.story_shorts_wakeup() FROM PUBLIC;
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM cron.job WHERE jobname='story-shorts-wakeup') THEN
    PERFORM cron.unschedule('story-shorts-wakeup');
  END IF;
END $$;
-- Check every minute to wake near the requested time; after queueing, ping every 10 minutes.
-- Stop when the scheduled batch finishes or the four-hour window expires.
SELECT cron.schedule('story-shorts-wakeup','* * * * *','SELECT public.story_shorts_wakeup()');
