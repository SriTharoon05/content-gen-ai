# Isolated Cloudflare / Cloudinary media migration pilot

**Status: media pilot only, NOT a complete replacement for the Render backend.**
All source changes are inside `cloudflare/`. The additive pilot database functions
below must be installed in Supabase. Do not change the frontend API URL, remove
Render, enable a second production scheduler, or change the existing CircleCI pipeline.
The runtime is TypeScript Workers/Workflows; native Python/FFmpeg runs only on CircleCI.

## Implemented path

### Fresh-generation live-test path

`POST /migration/generations` with admin bearer, a new 32-hex `Idempotency-Key`,
and `{"channel":"lorehush"}` starts a **new** English single-narrator video.
`GET /migration/generations/ID` returns progress. Only use after deploying both
Workflows, applying both SQL functions and pushing the CircleCI checkout branch.

Generation runs on Cloudflare: five candidate concepts, permanent pgvector reservation,
premise, script, independent QA/revisions, voice direction, free Gemini narration with
Groq fallback, Groq word timestamps, per-scene fresh images, edit decisions and publishing copy.
CircleCI performs audio preparation, then canonical caption/timeline construction and rendering.
All new media/checkpoints go to Cloudinary. Completion writes the new Video as
`AWAITING_APPROVAL`, with no automatic publishing. Render does not claim its `cf_*` jobs.

This path is deliberately limited to **English single-narrator review-only testing**.
It does not claim conversation, multilingual, scheduling, OAuth, dashboard-route,
music-editor or publishing migration parity. Keep existing Render routes enabled.
Free text keys are swept independently (Gemini then Groq), with at most two Workflow
retries after cooldown; no paid Gemini fallback in this live-test path. An uncertain
image purchase is not automatically billed again. Review its provider reservation.

Canonical nonsecret defaults, Pydantic JSON schemas and editorial instructions are
exported with `python cloudflare/scripts/export_contract.py` into `src/contract.json`.
Refresh this build artifact when canonical schemas/defaults/skills change.
Worker secrets stay inside individual steps and are never saved in Workflow results.
The isolated pilot currently checks out `codex/cloudflare-fresh-generation`; after
merging, change `CIRCLECI_BRANCH` to `main` and redeploy. No VCS trigger is enabled.

Admin submits an existing immutable manifest → Worker inserts a pilot job/task into
the existing Supabase PostgreSQL tables → Workflow triggers a separate CircleCI pipeline
→ atomic claim → download assets → canonical Python media code → Cloudinary upload
→ Worker verifies Cloudinary output and completes the Supabase task → Workflow observes completion.

Supported media operations:

| Operation | Execution |
|---|---|
| Initial/reuse-assets final assembly with prepared inputs | Existing `app.render_bundle.execute` / `app.media.assemble` on CircleCI |
| BGM crop/mix/re-encode | Existing `app.music_edit` through the same canonical bundle on CircleCI |
| Audio normalization, tempo, silence trimming before ASR | Existing `app.audio.pace_and_trim` on CircleCI, operation `prepare_audio` |
| Metadata checks, output validation | Python worker / canonical ffprobe on CircleCI |
| Claims, upload signatures, status, database transitions | Cloudflare Worker |
| Trigger retries, completion wait, deadline recovery | Cloudflare Workflow |

No FFmpeg, NumPy audio processing, image decoding or media hashing occurs in Workers.
Worker hashes only small credentials/upload-signature strings. Asset SHA-256 is checked
by CircleCI. Never send binary assets as Workflow payloads or step results.

The current `medium` runner (2 CPUs / 4 GB) and dynamic `auto` resource profile are
preserved. The existing `low_memory` code is not changed or duplicated.

## Production safety / same database

- No D1, Redis, Celery, new production database or table changes. One additive
  `public.cf_media_task` function is installed; no existing table/RLS/schema layout changes.
- Existing PostgreSQL + pgvector tables remain intact. No uniqueness logic is replaced.
- Pilot `jobs.status` values are `cf_waiting`, `cf_done`, `cf_failed`; Render does not claim them.
- Pilot render tasks use `next_trigger_at = 2100-01-01`; Render's outbox does not dispatch them and Python can decode their timestamps.
- `continuation_json.backend = cloudflare-pilot` scopes all worker claims.
- Pilot results live in `render_tasks.result_json`; **source Video records, assets,
  publishing and approval state are never changed**. Thus production can keep serving them.
- Idempotency-Key is required. Retry submission with the same ID after a network failure.
- Duplicate CircleCI runs cannot claim a task owned by another worker. A lost claim
  response can be retried by the same owner. Running tasks are never automatically reclaimed.
- The deadline is four hours. Workflow polls the DB every ten minutes when completion
  event delivery fails. A stopped/crashed Workflow requires operator recovery; this pilot
  deliberately installs no global cron that might compete with production scheduling.
- `ENABLE_MEDIA_PILOT=false` by default. No production cutover occurs on deployment.

## Storage compatibility

All new pilot uploads go to Cloudinary. `POST /migration/assets/upload` issues a signed
multipart upload capability for `image`, `audio`, `music`, or `checkpoint`; caller sends
`{kind, sha256, extension}` then uploads directly to the returned Cloudinary URL with its
`fields` and a `file` part. Persist the returned URL and SHA-256 in the render manifest.
Checkpoint files use Cloudinary `raw`; audio/music use its `video` resource type.

Old manifest `{key,sha256}` entries remain readable through Supabase signed URLs.
`{url,sha256}` entries may point to the configured Cloudinary or Supabase account only.
Existing Supabase files are never deleted or bulk migrated. Keep Supabase Storage
credentials while old assets still exist. All manifests/checkpoints must be immutable
for the duration of a task; mismatch fails before rendering.

## Cloudflare configuration

From this folder:

```powershell
npm ci
npm run check
npm test
npm run build
npx wrangler login
```

Worker name: `story-shorts-cloudflare-pilot`.
Workflow binding: `MEDIA_WORKFLOW`; class `MediaWorkflow`;
Workflow name: `story-shorts-media-pilot` (declared in `wrangler.jsonc`).
No D1/KV/R2/Queues/Hyperdrive binding is required by this pilot.
Database operations use Supabase's existing HTTPS Data API. The pilot-specific
`cf_media_task` RPC keeps claims and multi-table updates atomic inside PostgreSQL.
Only `service_role` can execute it; PUBLIC, anon and authenticated are explicitly denied.
No raw SQL endpoint is exposed. A direct TCP driver was removed because this pooler's
private CA chain failed Workers TLS verification and surfaced as reconnect/subrequest
exhaustion. TLS is NOT disabled. No Hyperdrive or other service is added.

Before deploying, install the function from a local **backend** terminal:

```powershell
venv\Scripts\python.exe ..\cloudflare\scripts\install_rpc.py
venv\Scripts\python.exe ..\cloudflare\scripts\install_rpc.py --apply
```

The first command tests then rolls back everything. The second installs the function
but still rolls back fixture jobs/tasks. Existing backend/.env supplies DATABASE_URL
only to this local installer; it is no longer required by the Worker.

Add secrets with `npx wrangler secret put NAME` (never commit values):

| Name | Value |
|---|---|
| ADMIN_TOKEN | Strong secret for pilot admin API; separate from production recommended |
| MEDIA_WORKER_TOKEN | New secret shared ONLY with the pilot CircleCI context |
| CLOUDINARY_CLOUD_NAME | Cloudinary cloud name |
| CLOUDINARY_API_KEY | Cloudinary API key |
| CLOUDINARY_API_SECRET | Cloudinary API secret |
| SUPABASE_URL | Existing Supabase project URL, for database RPC and legacy reads |
| SUPABASE_KEY | Server-only service-role key, for RPC and signing legacy downloads |
| CIRCLECI_TOKEN | Personal token permitted to trigger the project |
| CIRCLECI_PROJECT_SLUG | Copy the project slug verbatim from CircleCI |
| CIRCLECI_PIPELINE_DEFINITION_ID | ID of the NEW pilot pipeline below, not the Render pipeline |

Nonsecret vars are in `wrangler.jsonc`: branch, CORS origins, Cloudinary prefix,
and pilot enable flag. Change `ENABLE_MEDIA_PILOT` to `true` only after credentials
and the dedicated CircleCI pipeline/context are configured. Deploy with `npm run deploy`.
Do not add paid-plan CPU overrides without explicitly choosing a paid plan.

## Separate CircleCI setup

Keep the existing production pipeline and project env untouched.
Create another GitHub App pipeline:

| UI field | Value |
|---|---|
| Name | `story-shorts-cloudflare-media-pilot` |
| Config repository / checkout repository | `SriTharoon05/content-gen-ai` |
| Config path | `cloudflare/circleci/config.yml` |
| Branch / checkout branch | `main` |
| VCS / scheduled triggers | None; API only |
| Resource class | Already `medium` in YAML |

Create CircleCI context **`story-shorts-cloudflare`** (restrict access to this project)
with only these variables:

```dotenv
CF_MEDIA_API_URL=https://story-shorts-cloudflare-pilot.YOUR-SUBDOMAIN.workers.dev
CF_MEDIA_WORKER_TOKEN=<same secret as Cloudflare MEDIA_WORKER_TOKEN>
```

No DB, Cloudinary, Gemini/Groq, publishing or Supabase secrets are needed by the job.
All changes, including this folder, must be pushed before CircleCI can check them out.

## Test using an existing video (no generation / publishing)

Choose an existing **succeeded** row in `render_tasks`. In a local backend terminal,
set `CF_MEDIA_API_URL` and `CF_ADMIN_TOKEN` privately, then:

```powershell
venv\Scripts\python.exe ..\cloudflare\scripts\submit_existing.py --source-task EXISTING_TASK_ID
```

The command prints a new task ID/idempotency key. Keep it. For a lost response, use
`--idempotency-key THAT_SAME_ID` rather than creating another job.

Check `GET /migration/media-tasks/TASK_ID` with the pilot admin bearer token.
`GET /migration/database-check` with the same token verifies database access,
the render_tasks table and pgvector without changing any data.
Success contains `result.url`, byte count, duration and renderer metrics. Inspect that
Cloudinary URL and the new CircleCI pipeline. This pilot does NOT replace the dashboard
video; manually verify the separate output. Check Cloudflare CPU and step usage as well.

For audio preparation, POST a manifest to `/migration/media-tasks`, with a fresh
32-hex `Idempotency-Key`, existing `video_id`, and:

```json
{
  "version": 1,
  "operation": "prepare_audio",
  "playback_rate": 0.94,
  "settings": {},
  "files": {
    "source.audio": {
      "url": "https://res.cloudinary.com/YOUR-CLOUD/video/upload/source.wav",
      "sha256": "REPLACE_WITH_ACTUAL_64_CHARACTER_SHA256"
    }
  }
}
```

Wrap the example as `{"video_id":"...","manifest":{...}}` in the POST body.
ASR must consume this prepared audio, not raw TTS; otherwise captions will drift.

## Still on Render / NOT implemented in this pilot

- All existing frontend API routes, settings/channel CRUD and dashboard integration.
- Gemini/Groq/image generation, key rotation/cooldowns and cost ledger writes.
- Story selection, pgvector uniqueness reservation and generation checkpoint recovery.
- ASR, caption generation/translation and timeline creation after prepared audio.
- Google/Meta OAuth, review approval, YouTube/Instagram publishing and analytics.
- Scheduling and full end-to-end generation/re-render/speed-edit orchestration.

These are required parity gates before calling this a production migration. Do not
remove Render or repoint Vercel just because the isolated media pilot succeeds.
The current pilot does not proxy unimplemented routes to Render or silently run local FFmpeg.

## Free-tier gate

Cloudflare Free has a small CPU allowance and a 3,000 Workflow steps/day allowance;
measure real deployed CPU before deciding whether it fits. Dry-run bundling cannot
prove free-tier CPU compliance. No paid subscription is activated by these files.

Cloudinary's 25 credits are shared across storage, delivery and transformations, not
25 GB plus free bandwidth. Avoid transformation URLs; use canonical CircleCI outputs.
Check account upload limits (Free: 10 MB image/raw, 100 MB video), source music sizes,
retention and preview/publishing delivery costs. No automatic deletion is enabled.

Official references:
- https://developers.cloudflare.com/workflows/reference/limits/
- https://developers.cloudflare.com/workflows/reference/pricing/
- https://cloudinary.com/documentation/billing_and_plans
- https://cloudinary.com/pricing/compare-plans

## Local verification

```powershell
npm run check
npm test
npm run build
..\backend\venv\Scripts\python.exe -m unittest discover -s tests -p test_media_worker.py -v
```

Tests use fake credentials and mocked providers. No hosted Cloudflare/Cloudinary
end-to-end result is claimed until that actual run has succeeded.
