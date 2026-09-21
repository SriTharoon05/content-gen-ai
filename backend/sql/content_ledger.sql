-- Fresh-install equivalent. Existing installations are migrated by app.db.init_db().
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE TABLE IF NOT EXISTS content_ledger (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(), created_at TIMESTAMPTZ DEFAULT now(),
 video_id VARCHAR(32), core_entity TEXT NOT NULL, content_angle TEXT NOT NULL,
 core_concept TEXT NOT NULL, academic_term TEXT,
 status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','discarded')),
 embedding vector(768) NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge_frontier (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(), channel_slug VARCHAR(64) NOT NULL,
 parent_id UUID REFERENCES knowledge_frontier(id) ON DELETE CASCADE,
 topic_name TEXT NOT NULL, academic_term TEXT, depth_level INT DEFAULT 0,
 times_used INT DEFAULT 0, is_exhausted BOOLEAN DEFAULT FALSE, created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_frontier_lookup ON knowledge_frontier(channel_slug,is_exhausted,depth_level,times_used);
CREATE TABLE IF NOT EXISTS verification_backlog (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(), channel_slug VARCHAR(64) NOT NULL,
 parent_id UUID, proposed_term TEXT NOT NULL, attempts INT DEFAULT 0,
 created_at TIMESTAMPTZ DEFAULT now(), updated_at TIMESTAMPTZ DEFAULT now()
);
-- No HNSW index. Measure EXPLAIN ANALYZE before adding one at scale.
