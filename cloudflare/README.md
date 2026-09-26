# Native Cloudflare backend / Cloudinary media

**Status: native implementation present; production readiness remains unverified.**
The Worker and frontend implement generation, publishing/OAuth, scheduling, editing,
settings/channel administration and analytics. Implementation is not evidence of a
completed production cutover. Keep Render available and scheduling ownership on Render
until the deployment gates below are satisfied. The runtime is TypeScript
Workers/Workflows; canonical Python/FFmpeg media processing runs on CircleCI.
See [frontend status](FRONTEND_STATUS.md) for UI coverage and current verification.

## Current verification snapshot (2026-09-27)

- Fresh review-only run `fe7d16a3614a45ebb179210cd8171b5f` completed in
  **13m 26.7s**; final FFmpeg took **111.326s**. Output is accessible (HTTP 200),
  88.066667s at 30fps. API fixes were deployed during this run; it is not an
  uninterrupted benchmark. Human content review remains.
- Post-fix checks passed: **70 TypeScript, 8 frontend and 8 Python tests**,
  TypeScript checking and the Vite production build.
- Live YouTube analytics and Instagram insights returned HTTP 200.
- Browser soundtrack preview and asset-only CircleCI edit passed: saved 15% drama
  music, zero new provider calls, accessible final MP4 and approval enabled.
- Production Vercel origin is unknown; frontend deployment/CORS remain unverified.
- Render scheduler ownership guard deployment is unverified; the Render health check timed out.
  Scheduler owner remains **Render**. Dual-backend availability is not verified.
- Google/Meta callbacks still need provider-console allowlisting.
- No live Cloudflare public publishing test has been completed.
- Additional-language variant fan-out is unsupported. No free-tier fit is guaranteed.

## Implemented path

### Fresh-generation live-test path

`POST /migration/generations` with admin bearer, a new 32-hex `Idempotency-Key`,
and `{"channel":"lorehush"}` starts a **new review-only** video using the channel's
primary-language and conversation configuration. `GET /migration/generations/ID`
returns progress. Runtime prerequisites include all Workflow bindings, SQL migrations
and the CircleCI checkout branch described below.

Generation runs on Cloudflare: five candidate concepts, permanent pgvector reservation,
premise, script, independent QA/revisions, voice direction, free Gemini narration with
Groq fallback, Groq word timestamps, per-scene fresh images, edit decisions and publishing copy.
CircleCI performs audio preparation, then canonical caption/timeline construction and rendering.
All new media/checkpoints go to Cloudinary. The review-only route completes into
`AWAITING_APPROVAL`, with no automatic publishing. Native dashboard generation via
`POST /api/channels/:slug/run` supports review, direct and settings-based publishing
policies, plus destination and topic selection. Generation requires a 32-hex
`Idempotency-Key`; retry a lost response using the same key. Render does not claim
Cloudflare's `cf_*` jobs.

Native generation supports single-narrator and Alex/Sam duo conversations, including
speaker-aware speech generation, and the configured primary language. Non-English
narration uses translated English phrase captions. This does not implement
additional-language variant fan-out or establish live quality across every language.
Free text keys are swept independently (Gemini then Groq), with at most two Workflow
retries after cooldown; no paid Gemini fallback in this live-test path. An uncertain
image purchase is not automatically billed again. Review its provider reservation.

Canonical nonsecret defaults, Pydantic JSON schemas and editorial instructions are
exported with `python cloudflare/scripts/export_contract.py` into `src/contract.json`.
Refresh this build artifact when canonical schemas/defaults/skills change.
Worker secrets stay inside individual steps and are never saved in Workflow results.
Free-plan live testing exposed a 50-subrequest budget. Provider steps now execute in
separate `GenerationStageWorkflow` instances, notify the coordinator with durable
events, and the coordinator chains a continuation after eight new checkpoints.
It does not poll each child repeatedly. Media completion also uses an event, with
only ten-minute database reconciliation if a callback is lost. No paid CPU/subrequest
override is configured. Actual daily step usage still needs measuring before scaling.
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

No FFmpeg, NumPy audio processing or image decoding occurs in Workers. The fresh
generation adapter checksums bounded uploads using WebCrypto; CircleCI verifies every
downloaded asset SHA-256 again. Never send binary assets as Workflow payloads or step results.

The current `medium` runner (2 CPUs / 4 GB) and dynamic `auto` resource profile are
preserved. The existing `low_memory` code is not changed or duplicated.

## Production safety / same database

- No D1, Redis, Celery or new production database is required. SQL files `001`–`007`
  install media, generation, dashboard, schedule, publishing, editing and admin RPCs
  in the existing Supabase database, plus `cf_image_rate`, `cf_music_assets` and
  `cf_media_edits` support tables with restricted access.
- Existing PostgreSQL + pgvector tables remain intact. No uniqueness logic is replaced.
- Pilot `jobs.status` values are `cf_waiting`, `cf_done`, `cf_failed`; Render does not claim them.
- Pilot render tasks use `next_trigger_at = 2100-01-01`; Render's outbox does not dispatch them and Python can decode their timestamps.
- `continuation_json.backend = cloudflare-pilot` scopes all worker claims.
- Isolated media-task results live in `render_tasks.result_json`; submitting an existing
  manifest alone does not replace its source video. Native generation, editing,
  approval, publishing and admin routes intentionally update their corresponding
  records in the shared database. Settings/channel changes can affect both backends.
- Idempotency-Key is required. Retry submission with the same ID after a network failure.
- Duplicate CircleCI runs cannot claim a task owned by another worker. A lost claim
  response can be retried by the same owner. Running tasks are never automatically reclaimed.
- The media deadline is four hours. Workflow polls the DB every ten minutes when
  completion event delivery fails. A stopped/crashed Workflow requires operator recovery.
- Wrangler configures a once-per-minute cron for scheduling and publishing reconciliation.
  Generation scheduling uses shared ownership; selecting Cloudflare requires the
  deployed Render `/health` to attest `scheduler_owner_guard: true`. That guard is
  currently not deployed, so keep owner `render`. Publishing reconciliation is separate
  from scheduler ownership and can dispatch eligible Cloudflare videos.
- The checked-in `ENABLE_MEDIA_PILOT` is `true`; it gates HTTP routes after health,
  not the cron handler. Deploying does not itself transfer scheduler ownership.

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
Fresh generation also binds `GENERATION_WORKFLOW` (`GenerationWorkflow`,
`story-shorts-generation-pilot`) and `GENERATION_STAGE` (`GenerationStageWorkflow`,
`story-shorts-generation-stage`). Native editing binds `EDITING_WORKFLOW`
(`EditingWorkflow`, `story-shorts-editing`); publishing binds `PUBLISH_WORKFLOW`
(`PublishingWorkflow`, `story-shorts-publishing`). All five deploy together via Wrangler.
No D1/KV/R2/Queues/Hyperdrive binding is required by this pilot.
Database operations use Supabase's existing HTTPS Data API. The pilot-specific
RPCs keep claims and multi-table updates atomic inside PostgreSQL.
Only `service_role` can execute it; PUBLIC, anon and authenticated are explicitly denied.
No raw SQL endpoint is exposed. A direct TCP driver was removed because this pooler's
private CA chain failed Workers TLS verification and surfaced as reconnect/subrequest
exhaustion. TLS is NOT disabled. No Hyperdrive or other service is added.

Before a deployment, review/install SQL files `001`–`007` from a local **backend** terminal:

```powershell
venv\Scripts\python.exe ..\cloudflare\scripts\install_rpc.py
venv\Scripts\python.exe ..\cloudflare\scripts\install_rpc.py --apply
```

The first command tests then rolls back everything. The second installs the SQL
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
| GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET | Google OAuth app credentials for YouTube and analytics |
| META_APP_ID / META_APP_SECRET | Meta OAuth app credentials for Instagram and insights |
| OAUTH_ENCRYPTION_KEY | Optional explicit credential-encryption key; preserve compatibility with stored connections or reconnect |

Nonsecret vars are in `wrangler.jsonc`: branch, CORS origins, Cloudinary prefix,
enable flag, `RENDER_API_ORIGIN`, `GOOGLE_REDIRECT_URI`, `META_REDIRECT_URI` and
`META_API_VERSION`. The current CORS list contains only localhost origins. Configure
credentials and the dedicated CircleCI pipeline/context before deployment. The
deployment command is `npm run deploy`; these instructions do not establish that
the current source or configuration is deployed.
Do not add paid-plan CPU overrides without explicitly choosing a paid plan.

## Separate CircleCI setup

Keep the existing production pipeline and project env untouched.
Create another GitHub App pipeline:

| UI field | Value |
|---|---|
| Name | `story-shorts-cloudflare-media-pilot` |
| Config repository / checkout repository | `SriTharoon05/content-gen-ai` |
| Config path | `cloudflare/circleci/config.yml` |
| Branch / checkout branch | `codex/cloudflare-fresh-generation` during isolated testing; `main` only after merge |
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

## Native dashboard/API coverage

| Capability | Implementation |
|---|---|
| Dashboard generation | Channel runs, recent videos, progress, timing and Cloudinary previews |
| Publishing | Version-specific approval, YouTube resumable upload, Instagram Reel workflow, publication status and duplicate/uncertain-upload protection |
| Accounts and analytics | Google/Meta OAuth connection routes, YouTube analytics and Instagram insights |
| Scheduling | Schedule read/save, daily batch claiming, shared owner guard and cron dispatch |
| Editing | Saved-asset re-render, speed adjustment with new alignment/captions, BGM crop/mix/ducking through CircleCI |
| Music | Signed Cloudinary upload, catalog registration/removal and legacy checksum registration |
| Administration | Validated settings updates and channel create/read/update/delete |
| Narration | Single narrator or Alex/Sam duo; configured primary language and translated English captions |

The native UI calls Cloudflare routes directly; it does not proxy unsupported actions
to Render. Additional-language fan-out remains unsupported. Full production parity,
complete accounting and editorial/quality regression coverage are not established
by this implementation inventory or the historical unit-test counts.

## Production checklist (pending gates)

- [x] Re-run relevant checks and verify the fresh review run reaches success with
  accessible output, duration/FPS and render metrics. Human editorial review remains.
- [ ] Identify the production Vercel origin, configure both API bases and exact CORS
  allowlists, and verify frontend operation against each backend.
- [ ] Deploy/verify the Render scheduler guard before transferring ownership;
  confirm `/health` reports `scheduler_owner_guard: true` and only one scheduler owns
  generation. Current owner stays Render.
- [ ] Allowlist the configured `/auth/google/callback` and `/auth/meta/callback` URLs
  in the respective provider consoles; verify account connection and required access.
- [ ] Verify current SQL, five Workflows, secrets and CircleCI checkout/config agree
  with the intended release; keep the existing Render pipeline available.
- [ ] Validate editing, approval and publishing end to end on intended destinations,
  including a controlled live public publish and status reconciliation. None is claimed
  complete by the current fresh review run.
- [ ] Measure actual usage and costs against account limits before increasing load.

## Free-tier gate

Image stages run in batches of up to three isolated child Workflows per video.
Batches drain before the eight-stage continuation boundary or failure handling.
The Supabase `cf_image_rate` gate shares request pacing across all pilot videos and
keys per model: 1.1 seconds between starts for FLUX/Z-Image (below 60 RPM), and
0.22 seconds for DreamShaper (below 300 RPM). Existing Render requests do not use
this pilot gate: do not assume it coordinates a simultaneous Render workload.
This is bounded parallel network I/O, not image processing inside Workers.
Rate contention and uncertain purchases still fail closed after bounded attempts.
No new live speed or CPU benchmark has been performed for this change.

Measure deployed CPU, subrequests, Workflow steps, CircleCI consumption and provider
usage against the actual account plans. Dry-run bundling and mocked tests cannot prove
free-tier compliance; there is no guarantee this workload fits free limits.

Cloudinary credits cover storage, delivery and transformations. Use canonical CircleCI
outputs and check account upload limits, source music sizes, retention and
preview/publishing delivery costs. No automatic deletion is enabled.

Official references:
- [Cloudflare Workflow limits](https://developers.cloudflare.com/workflows/reference/limits/)
- [Cloudflare Workflow pricing](https://developers.cloudflare.com/workflows/reference/pricing/)
- [Cloudinary billing and plans](https://cloudinary.com/documentation/billing_and_plans)
- [Cloudinary plan comparison](https://cloudinary.com/pricing/compare-plans)

## Local verification

```powershell
npm run check
npm test
npm run build
..\backend\venv\Scripts\python.exe -m unittest discover -s tests -p test_media_worker.py -v
```

These commands cover Worker checks/build and the media-worker Python tests; they are
not the full cross-repository regression suite. Mocked tests do not verify live OAuth,
publishing or production limits. See the current verification snapshot above.
