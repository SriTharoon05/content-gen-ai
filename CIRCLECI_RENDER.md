# CircleCI media worker setup

Only media processing moves. FastAPI, generation, alignment, scheduling, review and publishing
remain on Render. PostgreSQL and the existing Supabase buckets remain the source of truth.
No CircleCI project or credentials were needed to implement/test this integration.

## 1. Deploy the code safely

Commit/push these changes to `main` in `SriTharoon05/content-gen-ai`. Deploy Render initially with
`RENDER_EXECUTION_BACKEND=local` and `RENDER_PROFILE=low_memory`; this does not switch live work.
Startup creates the `render_tasks` table and protects it with RLS. No new database is needed.
Do not switch execution backend mid-job. Wait for currently running jobs to finish first.

## 2. Create the CircleCI project and pipeline

Connect the **GitHub App** integration and give it access to this repository. In the project,
open **Project Settings → Pipelines → Add Pipeline** (or edit the pipeline created during setup).

| UI field | Value |
|---|---|
| Pipeline name | `story-shorts-media` |
| Repository/config source | GitHub / `SriTharoon05/content-gen-ai` |
| Config source branch | `main` |
| Config path | `.circleci/config.yml` |
| Checkout source | GitHub / `SriTharoon05/content-gen-ai` |
| Checkout branch | `main` |
| Executor/resource class | Already in config: Python 3.12 Docker / `medium` (2 CPUs / 4 GB RAM) |
| Push, pull-request, tag triggers | Do not add; remove any auto-created VCS trigger |
| Schedule triggers | None; scheduling stays on Render/Supabase |
| API triggering | Render calls the pipeline `/pipeline/run` endpoint |
| Auto-cancel redundant workflows | Disable; different videos on `main` must not cancel each other |
| Secrets for forked builds | Keep disabled |

The YAML additionally requires an API trigger and a nonempty `render_task_id`, so an ordinary
push cannot execute the media job even if a VCS trigger is accidentally left enabled.
Do not use `[skip ci]` / `[ci skip]` on the selected branch's latest commit: these can also suppress API pipelines.
Do not test by manually starting an empty pipeline: it intentionally does nothing.

Copy the **pipeline definition ID** from Pipelines, the **project ID** from Project Settings,
and the **organization ID** from Organization Settings. These are not repository names.
Create a CircleCI personal API token for the account allowed to trigger this project.
No ID/token needs to be committed into the repository.

## 3. CircleCI environment variables

Project Settings → Environment Variables:

```dotenv
RENDER_API_URL=https://story-shorts-api.onrender.com
RENDER_WORKER_TOKEN=<new random secret shared with Render>
```

Generate the shared secret locally, for example:

```sh
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Only these two variables are needed. **Do not copy** DATABASE_URL, SUPABASE_KEY, ADMIN_TOKEN,
Gemini/Groq/image keys, Google/Meta secrets or the CircleCI trigger token into this project.
CircleCI receives short-lived, task-specific Supabase download/upload URLs through the authenticated
Render API. The worker actively rejects database connections.

`RENDER_PROFILE=auto`, `RENDER_EXECUTION_BACKEND=local` and the non-secret `RENDER_TASK_ID`
are supplied by the YAML. The latter selects a task, not arbitrary shell arguments.
The worker installs the same FFmpeg/libass package and caption font as the backend Dockerfile.

## 4. Render environment variables

Keep all existing Supabase, model, OAuth, CORS and scheduler settings. Add:

```dotenv
RENDER_EXECUTION_BACKEND=circleci
RENDER_PROFILE=low_memory
CIRCLECI_TOKEN=<CircleCI personal API token>
CIRCLECI_PROJECT_SLUG=circleci/<organization-id>/<project-id>
CIRCLECI_PIPELINE_DEFINITION_ID=<pipeline definition ID>
CIRCLECI_BRANCH=main
RENDER_WORKER_TOKEN=
REMOTE_RENDER_TIMEOUT_MINUTES=240
RUN_WORKER=1
```

Leave `RUN_SCHEDULER` as currently configured: Supabase can continue calling `/api/schedule/tick`.
`RUN_WORKER=1` is required to generate inputs and reconcile remote completions. The first-install
blueprint deliberately defaults it to `0`, so check this explicitly. Keep one Uvicorn process and
the existing worker concurrency of one. Redeploy Render after saving the environment variables.

Run the updated `backend/sql/supabase_scheduler.sql` in Supabase SQL Editor once. It retains your
Vault secrets, schedule and four-hour window; the pending-job test now includes `waiting_render`.
Deploy Vercel for the small Jobs-page change (CircleCI status/task ID); no new Vercel env vars.

## 5. Verify one existing video, without generation

1. Choose an existing video with saved audio, images and caption alignment.
2. Dashboard → Reuse assets → Re-render.
3. Jobs should show **CircleCI rendering** and a task ID. Render must not log video encodes.
4. CircleCI should show one `render-request` workflow / `render-media` job.
5. Completion uploads the MP4 to Supabase; Render finalizes the existing job and the dashboard
   shows the new preview. Review mode remains gated; re-render does not auto-publish by itself.
6. Review the video/audio sync. Then test a scheduled generation using your existing workflow;
   autonomous publishing is still controlled by the existing publishing settings.

The worker logs actual wall time, peak memory, CPU, FFmpeg passes and validated output metadata.
Missing saved alignment/translation causes a clear error during reuse instead of silently calling
Gemini/Groq. Speed edits scale saved timestamps and English translation timing; no ASR/TTS is called.

## Operations and reliability

| Operation | Where |
|---|---|
| Initial video, transitions, zoom, caption burn-in, final encoding/validation | CircleCI |
| Re-render, manual edit, image/narration/script regeneration's final video pass, language/comparison final pass | CircleCI |
| Speed change's video pass | CircleCI |
| Applying/replacing BGM, music crop for mixing, final remix/validation | CircleCI |
| Script, TTS, ASR, translation, image generation | Render |
| Lightweight audio pacing/resampling, waveform analysis, library-upload audio crop, ffprobe | Render |
| Scheduling, review/approval, YouTube/Instagram publishing | Render |

The canonical renderer remains `backend/app/media.py`; `render_bundle.py` only deserializes the
prepared inputs and calls it (or the existing video-copy BGM mixer). No duplicate renderer.

Task state: `queued → running → succeeded/failed`. Job state is `waiting_render` while offloaded.
Claiming uses a PostgreSQL row lock and a unique random worker owner; duplicate pipelines do no
rendering. Lost trigger responses may create duplicate pipelines, but only one can claim the task.
Inputs have task-specific immutable object keys and SHA-256 verification. Rendering never uses live
dashboard settings: it uses a non-secret settings/font snapshot. Final output keys are task-specific.

Render retries transient trigger failures with backoff. A running task is **never automatically
reclaimed**, avoiding overlapping workers. Abandoned jobs expire after the configured deadline;
an explicit dashboard Retry creates a new task using the same immutable inputs, without generation.
A failed completion notification is retried. If all retries fail, the task can expire even if an
orphan MP4 exists; the UI does not falsely claim success. Keep task input assets for later retries.
CircleCI sends authenticated heartbeats while rendering; the existing Supabase wake window also
includes waiting renders. Pending edits block publishing until remote finalization completes.

## Profiles and benchmark method

`low_memory` retains the sequential, two-input implementation and its existing codec/quality
settings. `auto` reads Linux cgroup v1/v2 CPU/memory limits and affinity, chooses useful threads,
shares zoomed frames within bounded batches and removes redundant full-scene encodes. At 2 GiB,
its live-memory ceiling is 1.5 GiB. A 50-ms monitor aborts a fast pass on pressure and falls back to
the proven low-memory renderer using the same inputs. Memory is a ceiling, not an allocation target.

Benchmark inputs use the existing smoke fixture generators: 25 synthetic 1080x1920 images,
~54-second synthetic narration, English captions and mixed non-directional transitions; output
720x1280 / 30 fps, H.264 CRF 21 / veryfast and AAC 160k. Cold intermediate cache for every run.
The original smoke CLI suppresses transitions in low-memory mode; this benchmark deliberately
keeps the **same mixed transitions** in both profiles for a fair comparison.

Run inside the backend Docker image, using `--network none --memory 2g --memory-swap 2g --cpus 1`:

```sh
python scripts/benchmark_render.py --profile low_memory --output-dir /results
python scripts/benchmark_render.py --profile auto --output-dir /results
```

Measurements include media assembly and full output decode validation, not package installation,
CircleCI queueing, asset transfers, source-fixture creation or generation. Peak RAM is sampled cgroup
resident usage (excluding reclaimable inactive file cache); CPU includes Python and FFmpeg children.
Linux Docker measurements are **not real CircleCI completion-time guarantees**. CircleCI credits,
account limits, service policy and job timeouts still apply; this is not unlimited free hosting.

### Measured comparison (2026-09-22)

Both main results below used 1 CPU / 2 GiB, FFmpeg 7.1.5 on Linux. Final auto repeat reported:

| Metric | low_memory | auto |
|---|---:|---:|
| Total media render + validation | 182.260 s | 123.501 s |
| Peak resident RAM | 364.2 MiB | 350.3 MiB |
| CPU utilization (one CPU) | 99.66% | 99.27% |
| FFmpeg passes, including validation | 64 | 27 |
| Encoding invocations | 63 | 26 |
| Output duration / frames | 54.366667 s / 1,631 | 54.366667 s / 1,631 |
| Output FPS | 30 | 30 |
| Output bytes | 4,082,669 | 4,037,606 |

Auto was **32.2% faster** in this comparison. The first one-scene auto run was 116.394 s.
Four-scene batching used 957.4 MiB / 118.751 s; it did not consistently beat the one-scene path
on one CPU. Two-scene batching took 142.428 s / 603.3 MiB while another benchmark ran on the
same host, so that result is not a clean isolated ranking. The selected one-CPU path shares
body/boundary frames and avoids redundant encoding; multi-CPU runners may use larger batches.
It permits **1.5 GiB**, but these synthetic inputs did not benefit from filling that budget.

The separate **512 MiB / 1 CPU** safety run also passed: 194.053 s, 312.3 MiB peak, same duration,
FPS and output bytes as low_memory above. All sample outputs passed full decode validation.
See `backend/benchmarks/render_profiles_2026-09-22.json` for raw numbers and sampling caveats.

### Verification and file inventory

- 120 backend tests passed, including five real locking/outbox/retry tests against disposable
  local PostgreSQL. No production database was migrated during development.
- Real FFmpeg frame-count/sync checks at 24/25/30 fps cover low-memory and auto paths;
  memory-pressure fallback, reuse-without-providers and publishing guards have offline tests.
- Backend Docker build and Vercel frontend production build passed.
- CircleCI YAML/API contracts tested with mocked IDs/token. **No hosted CircleCI project,
  actual signed Supabase transfer or deployed end-to-end run has been claimed as tested.**

New files:

```text
.circleci/config.yml
CIRCLECI_RENDER.md
backend/app/circleci.py
backend/app/remote_render.py
backend/app/render_api.py
backend/app/render_bundle.py
backend/app/render_metrics.py
backend/app/render_profile.py
backend/scripts/circleci_render.py
backend/scripts/benchmark_render.py
backend/tests/test_remote_render.py
backend/tests/test_render_postgres.py
backend/benchmarks/render_profiles_2026-09-22.json
```

Updated files:

```text
.gitignore
backend/.env.example
backend/requirements.txt
backend/app/api.py
backend/app/config.py
backend/app/db.py
backend/app/models.py
backend/app/pipeline.py
backend/app/regenerate.py
backend/app/music_edit.py
backend/app/media.py
backend/app/settings_store.py
backend/app/storage.py
backend/app/english_captions.py
backend/app/worker.py
backend/app/scheduler.py
backend/app/social.py
backend/sql/supabase_scheduler.sql
backend/tests/test_production.py
backend/tests/test_render_cfr.py
frontend/src/pages/Jobs.jsx
PRODUCTION.md (existing edits preserved)
```

## Rollback

Let active tasks finish, then set Render `RENDER_EXECUTION_BACKEND=local` and keep
`RENDER_PROFILE=low_memory`. Existing Supabase assets remain reusable. Do not delete the new table
or disable the worker while waiting tasks need reconciliation.

References: [CircleCI trigger API](https://circleci.com/docs/guides/orchestrate/triggers-overview/),
[pipeline parameters](https://circleci.com/docs/guides/orchestrate/pipeline-variables/),
[small resources](https://circleci.com/docs/guides/execution-managed/using-docker/),
[Supabase signed uploads](https://supabase.com/docs/reference/javascript/storage-from-createsigneduploadurl).
