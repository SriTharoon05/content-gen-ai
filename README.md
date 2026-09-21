# Story Shorts

Five-channel short-video factory: FastAPI on Render, React/Vite on Vercel, PostgreSQL + pgvector and media on Supabase. See [PRODUCTION.md](PRODUCTION.md) for deployment and validation limits.

The fixed pipeline selects a source-backed unique concept, writes and reviews a 20–25-beat script, tries free Gemini 2.5 Flash Preview TTS with Groq fallback when selected, obtains Groq word timestamps from the final waveform, creates distinct images, and renders a 30 fps portrait video. New scripts target 100–115 words at a relaxed pace; normal generation permits 45–65 seconds instead of aggressively accelerating speech. Explicit dashboard speed changes may extend the duration further. DeepAgents/LangChain dependencies and runtime have been removed.

All text generation uses **gemini-3.1-flash-lite**. Available `GEMINI_FREE_KEYS` are rotated first; paid keys, including `GEMINI_AUDIO_PAID_KEY`, are tried only after free requests fail or all free keys are cooling down. Per-call metering records the actual tier used. TTS has a separate model dropdown in Settings.

## Local setup

1. Copy `backend/.env.example` to `backend/.env` and provide PostgreSQL and provider credentials.
2. Install Python 3.12, FFmpeg with libass/libx264, and Node.js 20+.
3. From `backend`, create a virtual environment and run `pip install -r requirements.txt`.
4. Run `python scripts/setup_caption_assets.py` and `python scripts/check_setup.py`.
5. Run `uvicorn app.api:app --host 127.0.0.1 --port 8000`.
6. From `frontend`, run `npm ci` and `npm run dev`.

Runtime settings are stored in PostgreSQL. Environment keys seed missing values; updating an already saved key is done in Settings. Production requires an admin token and Supabase Storage. Never put service-role/provider credentials in frontend environment variables.

## Uniqueness and grounding

`app/story_selection.py` returns exactly `id`, `core_entity`, `content_angle`, `core_concept`, `academic_term`. TaleMorrow, NowSift and LoreHush use the official Wikimedia daily-event feed. CurioNerve and NeuraScify use a breadth-first topic frontier, depth capped at 3, three uses per node, then five verified children. Roots exclude all historical root titles.

Grounding uses exact academic terms. Set `SERPAPI_KEY` for Google search through SerpAPI; otherwise official Wikipedia article search is used. Missing search evidence fails closed or goes to the verification backlog. Sunday scheduled batches queue backlog maintenance; rejected terms receive at most two weekly rephrasing/reverification cycles.

The permanent content ledger checks active entity history using 768-dimensional vectors and the attached percentile-based collision query, under transaction locks. Exact entity/angle repeats are also rejected. Five candidates plus one pivot are allowed; exhaustion raises an error for manual review. Concepts remain active even if downstream video production fails. Only an explicit discard unblocks an abandoned concept.

`text-embedding-004` returned 404 from the configured live Google API in the earlier test. The deployed default is `gemini-embedding-001` with explicit 768-dimensional output. Text generation remains 3.1 Flash-Lite; embeddings necessarily use an embedding model. SQLAlchemy's pgvector type plus explicit vector literals handle encoding; this project does not use asyncpg.

## Rendering and persistence

The low-memory renderer uses sequential scene clips and the concat demuxer, one FFmpeg thread, 720×1280, 30 fps, and hard cuts. It does not open 25 video decoders in one filter graph. Captions use measured Groq transcription timestamps, without global timing compression or script-derived display timings. Smooth, restrained scale animation replaces the large 22%→138% pop.

Final audio is paced before transcription. The limiter compensates its lookahead latency. Timing is limited by ASR accuracy, 10 ms ASS precision and 33.3 ms video frames; microsecond or phoneme-perfect alignment cannot be promised. Unavailable ASR fails the job instead of silently generating guessed captions.

Supabase holds videos, images, narration, scripts, timeline/caption files and music. Local disk is only processing scratch. A hashed media manifest supports restoring jobs and regenerations after a Render restart. Source files are checkpointed during production and uploaded before successful-job scratch cleanup. Derived FFmpeg clips are reproducible cache.

## Checks

From `backend`: `python -m unittest discover -s tests -v`, `python scripts/check_setup.py`, and `python scripts/smoke_render.py --shots 25 --seconds 50`.

From `frontend`: `npm run build`.

`python scripts/test_one_video.py` intentionally spends provider credits and creates exactly one LoreHush English video with no variants. It refuses to run with auto-publishing enabled. Do not run this script during a deployment build.

## Publishing

The Publishing & analytics dashboard shows per-channel connection status, completed-video upload controls, publication records and 28-day YouTube analytics. Credentials are server-side environment variables. Configure the exact channel mapping before enabling Auto-publish in Settings. YouTube defaults to private; uncertain uploads require operator reconciliation to avoid duplicates. No uploads or analytics access can be live-verified until account credentials and permissions are supplied..
