-- Run after first app startup. Prevent direct browser/API access to application tables.
DO $$ DECLARE name text; BEGIN
  FOREACH name IN ARRAY ARRAY['app_settings','channels','videos','registry_assets','assets','music_tracks',
      'provider_calls','jobs','decisions','story_history','content_ledger','schedule_runs','channel_memory',
      'knowledge_frontier','verification_backlog','publications'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',name);
    EXECUTE format('REVOKE ALL ON TABLE public.%I FROM anon, authenticated',name);
  END LOOP;
END $$;
