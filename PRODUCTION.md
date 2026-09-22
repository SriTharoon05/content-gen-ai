# Deploying to Render + Vercel + Supabase

## Optional CircleCI media execution

See [CIRCLECI_RENDER.md](CIRCLECI_RENDER.md) for the complete setup, environment variables,
benchmark method and rollback. The default remains local/low-memory. CircleCI is opt-in and runs
only checkpointed media tasks; generation, scheduling and publishing remain on Render.

## FFmpeg transition frame-rate fix (2026-09-22)

- Both rendering paths now normalize timestamps, constant frame rate and time base before
  transitions, including after concatenation. This fixes unknown `1/0` rates rejected by `xfade`.
  The low-memory renderer still processes only two scene inputs per transition.
- Synthetic FFmpeg regression tests cover the unknown-rate failure and both rendering modes
  at 24, 25 and 30 fps, checking frame counts and audio/video duration. Docker builds now run
  these tests against the image's Linux FFmpeg without database or provider access.
- Push these changes and redeploy **Render only**; no Vercel change or database migration is needed.
  Linux/hosted verification remains pending until that build and a hosted render succeed.
- After deployment, open each failed video in the dashboard and use **Reuse assets → Re-render**
  to reuse its saved images and narration. Do not start a new content-generation run just to repair
  this error. Missing alignment/translation caches may still require caption-provider requests.

## Instagram publishing and dashboard update (2026-09-22)

- The old local worker sent an empty Instagram bearer token and marked the resulting local protocol
  failure as `uncertain`. Restart/redeploy the backend to load the saved direct-Instagram credentials.
- Preparation/container failures are now `failed` and explicitly retryable. An interrupted final
  publish remains `uncertain` to prevent duplicate posts. Publication claims use a database row lock.
- Three existing videos were published and independently read back from Instagram:
  [NowSift](https://www.instagram.com/reel/Ddj7g4IDKoL/),
  [LoreHush](https://www.instagram.com/reel/Ddj7miGgIui/),
  [CurioNerve](https://www.instagram.com/reel/Ddj7sZcDrwt/).
  No new content was generated. Re-approving the published NowSift record created zero extra jobs.
- Video editor and Publishing → Review & publish share one destination selector and approval control.
  Account connections/insights and publication history have their own tabs. Instagram history includes
  Check Instagram → Open Instagram. Channels shows one selected workspace instead of six repeated forms.
- Settings → Publishing & schedule → Publishing destinations supports YouTube, Instagram or both.
  Existing installations default to YouTube only; Instagram automation is opt-in. Single-channel and batch
  runs can override destinations and choose review/direct/global workflow. Review and comparison guards remain.
  Destination lists use existing JSON settings/options columns; no new schema migration is required for this update.
- Redeploy both Render and Vercel. Local tests do not establish that hosted workers have the new version.
  Keep one production worker deployment and do not run an unrestricted local worker against the same queue.
  New automatic Instagram routing is covered offline; no additional scheduled generation was run for this update.
- Verification: 94 offline backend tests, production frontend build, 12 live local API reads against Supabase,
  duplicate-approval guard, desktop page audit and mobile publishing layout passed. Live Instagram uploads
  were scoped to the three existing videos above. These checks are not a Render Free load/RAM certification.

## Meta connection setup (2026-09-22)

The app implements **Instagram API with Instagram Login** for professional accounts.
No Facebook Login or linked Facebook Page is required. No live Meta consent or upload has been tested.

1. Deploy the updated backend and frontend. Backend startup creates `meta_connections` and blocks
   Supabase browser roles from reading it, including outside production mode.
2. Render Environment: set `META_APP_ID`, `META_APP_SECRET`,
   `META_REDIRECT_URI=https://story-shorts-api.onrender.com/auth/meta/callback`, and `META_API_VERSION=v25.0`.
   A local `.env` does not configure Render. Never expose these values through Vercel `VITE_` variables.
3. Meta developer app: configure Instagram Login and register the exact HTTPS callback above.
   Use the Instagram App ID and secret from Instagram API setup, not Facebook Login credentials.
   Requested scopes: `instagram_business_basic`, `instagram_business_content_publish`,
   `instagram_business_manage_insights` (availability/access must be confirmed in your Meta app).
   Development-mode users need the appropriate app roles; broader access may require Meta review,
   business verification, a privacy policy and data-deletion instructions. The app does not implement
   a Meta data-deletion callback; do not register the OAuth callback as a deletion callback.
4. Publishing & analytics → channel → Connect Instagram directly → sign in → connected.
   There is no extra account-selection step. Connecting does not publish.
5. Publishing → Review & publish → choose Instagram → Approve & publish → confirm the destination.
   Review, job and duplicate guards remain active. To automate future Instagram uploads, explicitly select
   Instagram in Settings publishing destinations and configure review/automatic mode.

Instagram user tokens are encrypted at rest. OAuth links/state expire after ten minutes, are one-use,
and callback state is browser-bound. Saved tokens are refreshed when fewer than seven days remain before
publishing or fetching insights. Legacy Facebook connections must reconnect; environment Instagram token
mappings are not used. Reconnect on revocation or expiration. Keep `OAUTH_ENCRYPTION_KEY` stable if configured; otherwise Meta encryption derives
from `META_APP_SECRET`, separately from the existing Google-secret encryption. Rotating that secret requires
Meta reconnection. Setting a new shared encryption key also requires reconnecting existing YouTube accounts.

Local validation: 74 offline tests and frontend build passed. These do not establish Meta app approval or
provider-side upload success. Reference: [Meta's Instagram Login API collection](https://www.postman.com/meta/instagram/folder/6raa77c/instagram-api-with-instagram-login).

## Dashboard-owned channels and manual publishing

Channels & Runs → Create new channel accepts a stable unique lowercase ID, display name, tagline, niche, topic ideas, instructions and narrator/conversation format. Existing profiles show effective channel instructions read-only; Edit channel → Save channel persists changes immediately (separate from global Save All). Startup preserves those edits. Custom instructions feed idea selection and every creative role; generation limits and safety constraints remain fixed. A one-run topic and direction are passed to idea selection instead of being ignored.

Use Connect / change YouTube account on the channel card; each new channel uses the existing per-channel OAuth connection and appears in Publishing & analytics. A manual run can use Review and approve, Publish directly, or global settings. Direct publishing requires a connected channel and uses the saved YouTube visibility. BGM edits still force fresh preview approval regardless of the original run mode. These modes apply to the primary run output; language variants remain available for manual review/upload.

New renders show English captions for every audio language. English narration keeps measured word timing; other languages use English translations displayed over measured source phrase intervals. Translation uses the configured text-model key fallback and caches by source text/timing. Translation errors fail the render rather than silently dropping captions. Existing videos are unchanged until Re-render; images and narration can be reused.

Low-memory rendering preserves the editor's non-directional transitions by rendering short two-input boundaries and concatenating scene bodies, rather than opening every decoder together. `zoom_out` blends into an incoming centered pull-back. No lateral swipes are allowed. Extra local encoding takes longer than all hard cuts; the synthetic regression test validates frame count and audio duration but is not a Render Free RAM/load certification. Fact/science prompts now require concrete curiosity and payoff; fiction/TaleMorrow uses scene-led hooks.

## Expressive narration and live soundtrack preview

Updated soundtrack approval flow: manual track selection works across all channels; channel preferences only guide automatic choices. Apply soundtrack, wait for processing, then Publish to YouTube. Replaying and separately confirming a preview is no longer required (supersedes the older preview-confirmation instructions below). Pending edits and unsaved music changes still block publishing; a manual soundtrack edit never auto-publishes. 60 backend tests and the frontend build passed for this change.

New scripts target fluent 45–75-second performances (absolute 90 seconds), 20–30 contextual images, and curiosity hooks. Dialogue uses explicit speaker turns spanning multiple visual beats; visual cuts no longer force speaker switches. Saved defaults are 65 seconds and 1.0x speech tempo. Existing videos/scripts are unchanged until explicitly regenerated.

Video → Background music editor → select an uploaded track → Load live preview → play → adjust intensity/crop/ducking. The browser mixes clean narration and music; no render or provider call is made while auditioning. The original video audio is muted to prevent doubled music. Browser ducking/limiting are approximate, so Apply once when satisfied, inspect and confirm the saved final mix, then approve/upload. Final mixing copies the video stream unchanged and encodes the selected soundtrack only. Unchanged already-saved videos can upload without another mix. Browser live audio needs Supabase media CORS access and clean narration in storage; missing assets show an error instead of mixing over old music.

## Content generation policy (latest owner preference)

External fact-checking is disabled. Gemini 3.1 Flash Lite creates concepts and scripts directly without Wikipedia, verified topic-tree searches or daily event feeds. Existing concept reservations and permanent duplicate/semantic uniqueness checks are retained. Writing, safety, retention and media QA remain; they do not require source evidence. Generated content must not be represented as externally verified. The old verification backlog is retained but no longer processed automatically or by its legacy endpoint. Factual accuracy and current-news freshness are not verified by this workflow; use manual review when those matter.

## 1. Supabase

### Current release: BGM editor and first hosted test (2026-09-21)

- Music / BGM: upload rights-cleared music, choose category, preview/crop before upload, set default track/intensity/ducking, then Save All. Audio and track metadata persist in Supabase, not Render's temporary disk. Deleting a track removes it from the active library but retains its audio for edit history. Choose another default before deleting the current default.
- Open an unpublished finished video: select category/track, crop/intensity/ducking → Apply in video → play the new preview → confirm reviewed → Approve & upload. The video stream and captions are copied unchanged; clean narration is remixed without image/TTS/ASR calls. Each revision is saved. Subsequent edits invalidate approval. Autonomous new videos use the saved default; manually edited videos always require review.
- Before deployment: Jobs currently contains **four old queued/stale jobs**. Review and cancel unwanted queued/expired jobs with the new control before enabling the hosted worker. No old jobs were cancelled automatically. Do not run local and Render workers simultaneously against the same database.
- For safe first boot, override Render `RUN_WORKER=0` and `RUN_SCHEDULER=0`; after queue review and configuration, set both to `1`. Do not copy local `WORKER_CREATED_AFTER` to Render.
- Google OAuth: set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `GOOGLE_REDIRECT_URI=https://YOUR_RENDER_SERVICE.onrender.com/auth/google/callback`. Add that exact callback in Google Cloud. Reuse the existing client secret/encryption configuration to retain encrypted connections; if changing `OAUTH_ENCRYPTION_KEY`, reconnect every channel.
- One-video schedule test: Settings → Scheduled channels = LoreHush only, Videos per channel = 1, run time = five minutes ahead, timezone offset = 330, Daily schedule ON. For automatic public upload, publishing ON, review-before-upload OFF, privacy public. Save All. Do not click Run now; wait for the scheduled job. Check Jobs, the final preview, and the returned YouTube link/actual privacy. Disable daily scheduling afterwards if this was only a test. A date is claimed once; moving its time does not create a second scheduled batch that day.
- Acceptance after hosting is still required: restart the backend and verify BGM/previous previews persist; test one external Cron-triggered run, then one review-mode music change. No hosted run has been claimed as passed.
- Current local verification: 38 tests passed (including physical crop upload, preview approval guards, default music, midnight scheduling and real FFmpeg stream-preserving remixes), frontend production build passed, `/health` and music library API passed, and the Supabase wakeup function compiled in a rolled-back transaction. Docker Desktop was not running, so the updated Linux image was not retested in this session. No music was uploaded into the live library by these tests.

Use an existing Supabase project and copy its PostgreSQL session-pooler connection string (IPv4 compatible, port 5432), requiring SSL. Use `postgresql+psycopg://.../postgres?sslmode=require`. Do not use a Render free PostgreSQL database: this application expects Supabase.

Enable `vector` and `pgcrypto`. The backend's idempotent startup migration creates tables and upgrades old ledger statuses: reserved/published → active; failed → discarded. Back up an existing database before deploying schema changes. Never change embedding models after accumulating production vectors without re-embedding all active rows in a controlled migration.

Before moving an existing local installation, run `python scripts/migrate_media.py` from `backend` to upload its old source files, finished videos and music. It retains original files. Check Supabase storage capacity first. New production jobs checkpoint automatically.

Set `SUPABASE_URL` and server-side `SUPABASE_KEY`. Startup creates `videos`, `music`, and `assets` public buckets when absent. Objects are intentionally publicly fetchable for the dashboard and Instagram; do not put private media in these buckets. Uploads require the server service-role key. Keep Storage write policies restricted. App database tables should have RLS enabled with no anon/authenticated policies; the backend's PostgreSQL owner connection performs the queries. Run `backend/sql/secure_tables.sql` after first startup.

## 2. Render

Push this directory to a private Git repository and create a Render Blueprint from `render.yaml`. It creates one **free Docker web service**, no persistent disk. Build context is `backend`; health check is `/health`. Use the Dockerfile so Linux FFmpeg/libass/font dependencies are installed consistently.

Set these server environment variables:

| Variable | Value |
|---|---|
| `DATABASE_URL` | Supabase session-pooler PostgreSQL URL |
| `SUPABASE_URL`, `SUPABASE_KEY` | Project URL and server service-role key |
| `ADMIN_TOKEN` | Long random dashboard access token |
| `SCHEDULER_TOKEN` | Separate long random token for Supabase Cron |
| `CORS_ORIGINS` | Exact Vercel production origin, no trailing slash |
| `GEMINI_FREE_KEYS` | Comma-separated free keys |
| `GEMINI_AUDIO_PAID_KEY` | Paid narration key, also text fallback |
| `GEMINI_PAID_KEYS` | Optional additional paid fallback keys |
| `POLLINATIONS_API_KEYS`, `GROQ_API_KEYS` | Comma-separated provider keys |
| `WIKIMEDIA_USER_AGENT` | `StoryShorts/1.0 (mailto:YOUR_REAL_CONTACT)` |
| `SERPAPI_KEY` | Optional grounding search key |

The blueprint sets `APP_ENV=production`, `RUN_WORKER=1`, `RUN_SCHEDULER=1`, and all scratch paths under `/tmp/story-shorts`. Keep worker concurrency at one, image concurrency at two, output at 720×1280/30 fps, low-memory rendering enabled. Hard cuts are used in this profile. Do not add multiple Uvicorn workers or a second standalone worker.

Render's free services may restart at any time, have no persistent disk, and sleep after 15 minutes without inbound traffic. Pinging addresses idle sleep only; it does not guarantee RAM, uptime, credits or completion within four hours. This implementation reduces FFmpeg memory and restores checkpoints, but actual Linux container memory/CPU limits must be measured after deployment. A free tier cannot honestly be certified crash-free or production-SLA reliable. See [Render's limits](https://render.com/docs/free).

## 3. Vercel

Import the same repository with Root Directory `frontend`, framework Vite, build `npm run build`, output `dist`. Set `VITE_API_BASE=https://YOUR_RENDER_SERVICE.onrender.com/api`. Redeploy whenever this value changes. `vercel.json` provides SPA rewrites. In Settings, enter the same admin token as Render. No provider key or Supabase service-role key belongs in Vercel frontend variables.

## 4. Daily 10:00 IST wakeup

In dashboard Settings, enable Daily schedule, set `run_at=10:00`, timezone offset `330`, one video per channel, and enough image credits. Twenty-five images × five channels × 0.004 credits = 0.5 image credits worst case per daily batch (audio/text/search have separate billing). Save All. Leave auto-publish off until account setup and verification are complete.

In Supabase, enable Cron (`pg_cron`), `pg_net`, and Vault. Create Vault secrets:

- `story_shorts_api_url`: `https://YOUR_RENDER_SERVICE.onrender.com`
- `story_shorts_scheduler_token`: same value as Render `SCHEDULER_TOKEN`

Run `backend/sql/supabase_scheduler.sql` in SQL Editor. Cron checks every minute and wakes the service near the scheduled time. Once the batch is claimed, it sends GET `/health` and authenticated POST `/api/schedule/tick` every ten minutes while associated jobs are queued/running. It stops after those jobs finish/fail, or four hours after scheduled start; windows crossing midnight are supported. Backend queue creation and the daily claim commit in one transaction, so duplicate ticks cannot queue duplicate batches. Cold-start latency means work may begin about a minute late; it is not an exact wall-clock SLA. Scheduled channels can be restricted in Settings; no selection means all enabled channels.

Check Cron run history and `net._http_response`. A 401 means the two scheduler tokens differ. Inspect Jobs for individual errors. Expired quotas, suspended projects, failed wakeups and the four-hour cutoff require operator attention; monitor these in Supabase/Render. Sunday batches also queue verification-backlog retries. To run maintenance manually, POST `/api/story-selection/retry-backlog` with the admin token.

## 5. Connect publishing and analytics later

Create a Google Cloud project, enable YouTube Data API v3 and YouTube Analytics API, configure OAuth consent and obtain offline refresh tokens for each destination channel. Request `https://www.googleapis.com/auth/youtube.upload` and `https://www.googleapis.com/auth/yt-analytics.readonly`. Complete Google's app verification/audit requirements when applicable. OAuth testing-mode token expiration and unverified upload privacy restrictions are provider limitations; entering an API key alone is not sufficient.

Set `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, and `SOCIAL_CHANNELS_JSON` on Render. Example structure (use real secrets only in Render):

```json
{"talemorrow":{"youtube_refresh_token":"TOKEN_A"},"lorehush":{"youtube_refresh_token":"TOKEN_B"}}
```

For Instagram, follow the direct Instagram Login setup above. The adapter uses `graph.instagram.com`, creates a REELS container from the public Supabase video, polls processing, then publishes. Set `INSTAGRAM_GRAPH_VERSION` (or `META_API_VERSION`) to your supported Graph version. Facebook Login tokens are not interchangeable with direct Instagram Login tokens.

Open Publishing & analytics to connect each YouTube channel and request metrics. `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` are accepted alongside the `YOUTUBE_` names. Set `GOOGLE_REDIRECT_URI` to the exact registered callback (`http://localhost:8000/auth/google/callback` locally; `https://<your-render-host>/auth/google/callback` in production). Enable YouTube Data API v3 and YouTube Analytics API in the matching Google project. Select **Connect YouTube** on the correct channel card and grant upload, channel-read and analytics access. Confirm the returned YouTube channel name/ID before publishing. A client ID/secret alone is not a channel authorization.

OAuth uses expiring one-use state, browser binding and PKCE. Refresh tokens are encrypted in the server-only `social_connections` table; RLS and grants block Supabase browser roles. Prefer a dedicated `OAUTH_ENCRYPTION_KEY` (Fernet format), set before connecting channels. If absent, encryption derives from the Google client secret: rotating that secret requires reconnecting channels. Never put secrets in Vercel `VITE_` variables. Keep `ADMIN_TOKEN` mandatory in production.

In Settings → Publishing & schedule, **Review before upload** ON makes new edits wait for explicit approval; OFF permits autonomous uploads to the selected destinations when **Enable publishing after generation** is also ON. The worker persists finished assets before queuing the upload. Existing waiting videos are not automatically swept up when settings change. Comparison runs with `auto_publish=false` stay excluded unless explicitly submitted through Publishing. You can send an existing finished video through the current workflow without regenerating content. Instagram is opt-in for automatic publishing; see the latest release checks above.

YouTube visibility is selectable in Settings. Fresh installations default to private; the requested local evaluation uses public. Publication history stores both requested and verified actual visibility, with **Check YouTube** to refresh status. Unverified Google API projects can be restricted to private uploads even when requesting public; an API compliance audit may be required. Google consent verification and the YouTube API upload audit are not interchangeable.

Publication rows prevent repeated button clicks from creating duplicate upload jobs. A network failure after submission becomes `uncertain`, and automatic retry is disabled because remote success may be unknown. Inspect the remote account and saved publication session before any manual reconciliation. Do not blindly delete the publication row and retry.

## 6. Release checks

### YouTube review/autonomous workflow update

The 2026-09-20 publishing update adds encrypted Google OAuth, per-channel consent, review/automatic routing, duplicate prevention and actual YouTube visibility verification. All 31 backend checks and the frontend production build pass. Offline coverage includes review gating, automatic routing, explicit approval, edit-in-progress blocking, browser-state validation, token encryption, and YouTube's forced-private response. The existing LoreHush output was successfully placed into review through the real API; a direct upload before approval returned 422 and created no publication.

After the owner completed OAuth, both live LoreHush tests passed: review/approve uploaded `EIzC8LH4jyo`; autonomous routing of the existing FLUX variant uploaded `nnp524uHCyo`. YouTube Data API independently reported `privacyStatus=public` and `processingStatus=succeeded` for both. A repeated approval returned the existing publication without enqueueing a duplicate. YouTube Analytics returned HTTP 200 with 26 daily rows. No new media was generated, and Meta was not tested. This validates publishing existing files, not a new scheduled generation or a hosted Render deployment. Final local settings: YouTube publishing enabled, review-before-upload ON, visibility public, daily scheduling disabled.

### Dashboard speed changes and image-model comparisons

Open a video and use **Audio speed — keep every image**. A factor of 0.94 makes narration approximately 6% slower, relative to the saved baseline waveform. The job reprocesses audio, transcribes that waveform, rebuilds captions and scene lengths, then renders a versioned output URL. It never calls the image generator; missing source images stop the action rather than buying replacements. Original audio and all images are persisted in Supabase. Concurrent modifications of the same video are rejected. Lower playback speed is not silently cancelled by fitting back to the original duration.

Gemini 2.5 Flash Preview TTS has a free-tier listing. With **Free Gemini TTS first, then Groq** enabled, only `GEMINI_FREE_KEYS` are attempted for narration before Groq fallback; `GEMINI_AUDIO_PAID_KEY` is not used by that route. Free availability is still subject to project limits. Text remains Gemini 3.1 Flash-Lite; `models.allow_paid_text_fallback=false` disables paid text fallback for this evaluation batch. Preview speech models do not provide a production reliability guarantee.

CuriousDuo is the two-host channel: Alex/Puck and Sam/Zephyr discuss topics selected through the permanent uniqueness ledger. External fact-checking is disabled. If free Gemini TTS is unavailable, Groq synthesizes labelled turns using Troy and Hannah (not a single voice impersonating both). It uses clear turn-taking rather than overlapping words that would impair caption readability. Low-memory rendering supports centered zoom motion and non-directional blends.

Image comparisons are separate video records with a common source. They reuse the exact approved script, narration, timeline, and edit, but generate each set of images independently. Titles and versioned filenames contain the image model. The repeatable operator script is `backend/scripts/run_model_comparison.py`; it processes only explicitly selected batch jobs and never publishes. Default scope is the five existing channels. Add `--channels curiousduo` to evaluate the new channel separately. FLUX Schnell costs 0.002 credits/image and DreamShaper 8 LCM 0.0001 in the configured catalog. Twenty images per model is 0.042 credits per comparison pair before retries; catalog estimates should be reconciled with the provider balance.

### Optional Groq narration evaluation

Settings → Voice → Audio generation model switches between Gemini and Orpheus English/Arabic. Groq narration uses `GROQ_API_KEYS` (or dashboard Groq keys) and the separate Groq voice selectors. Text remains Gemini 3.1 Flash-Lite. Orpheus is single-speaker in this adapter; Gemini director prompts are not sent. Accept the selected model's terms in the Groq console as organization admin before testing. English and Arabic are the only supported Orpheus languages.

As checked September 20, 2026, Groq classifies Orpheus as preview and explicitly advises against production use. Whisper is a production model. Use Orpheus for evaluation, not as the sole dependable production narration service. The adapter chunks narration into at most 200 characters, caches each successful chunk, spaces requests, and honors bounded Retry-After waits without trying to multiply quotas by rotating keys. This pacing assumes the configured single worker; other apps using the same organization consume the same allowance.

The saved one-video test has 956 characters and five TTS requests. Ten similar videos would require approximately 50 requests/day and, assuming roughly four English characters per token, 2,390 input tokens/day; actual provider token accounting must be checked in the console. Against 100 RPD / 3,600 TPD, this is plausible but not guaranteed, and full regeneration of all ten would exceed the estimated token budget. Transcription is approximately 500 audio seconds/day for ten 50-second videos. Keys within one organization share quotas; additional keys provide no extra capacity. Upgrade the organization plan or request a limit increase if needed. Model preview status remains even on a paid plan.

After the organization admin accepted model terms, the same LoreHush video completed with Groq Orpheus English (Troy): 956 input characters across five requests, 20 unique images, 720×1280, 30 fps, 1,539 frames. The uploaded Supabase MP4 returned HTTP 200; audio and video both start at 0 and last exactly 51.300 seconds. All 20 image records use Supabase URLs, with 36 source/checkpoint objects in the manifest. Whisper matched 96.8% of script words; 153 recognized words produced 152 caption groups with zero dropped groups (two words sharing a start are displayed together, without inventing a boundary). This is not proof of perceptually perfect word timing; listening/viewing acceptance is still required. Fourteen backend tests passed.

Live-test fixes: disable psycopg automatic prepared statements for Supabase transaction pooling; allow the duration fitter to use the full 45–55s range; cache transcription responses by waveform/model/language; combine identical-start ASR words into measured phrase captions. Narration was reused from cached chunks on retry. Listed Groq costs in the dashboard are list-price estimates, not invoices; free-plan charges may be zero.

References: [Orpheus limits and voices](https://console.groq.com/docs/text-to-speech/orpheus), [organization rate limits](https://console.groq.com/docs/rate-limits), [production versus preview models](https://console.groq.com/docs/models).

Run backend regression tests and setup checks, frontend build, and synthetic render. After deploying, verify `/health`, dashboard loading/CORS, a single video, Supabase media persistence, shot previews after restart, and a scheduled external wakeup. Test publish/analytics only after credentials are available. The local test does not establish hosted Render reliability or social API permissions.

### Verification on 2026-09-20

Follow-up slower-video verification: LoreHush's 0.94× revision is 54.533333 seconds for both video and audio, with 20 retained images and zero image purchases for the speed job. The FLUX/DreamShaper LoreHush pair has byte-identical narration and identical script/timeline hashes. Free Gemini 2.5 Flash Preview TTS successfully generated TaleMorrow narration (Sulafat) at about 50.45 seconds. Seventeen regression tests pass on Windows and in Linux. An additional 25-scene 60-second Linux synthetic render completed under a 512 MiB/no-swap/one-CPU limit with exit 0 and OOMKilled=false. Hosted acceptance and human content review still apply.

For local evaluation only, `WORKER_CREATED_AFTER` can limit job pickup/recovery to a UTC ISO timestamp so older unrelated test jobs are not resumed. Do not set this on Render: the production worker must be able to recover all its queued jobs. The current local review server uses this cutoff and keeps automatic scheduling off; dashboard edits created after server startup can run normally.

- Frontend production build passed; 10 backend regression tests passed in Linux.
- Docker build passed. A 25-scene, approximately 50-second synthetic render completed with a 512 MiB memory limit, no swap, and one CPU. Container exit code was 0 and OOMKilled was false. This tests FFmpeg with generated fixtures, not live provider narration or concurrent HTTP traffic.
- The separate Windows synthetic render measured 0 ms added audio/video mux offset at 8 kHz analysis resolution. This does not prove speech recognition word accuracy; captions also remain bounded by frame and subtitle timestamp resolution.
- The single requested live LoreHush video (`d6816eddba9a4e949fb0a5950a0bf308`) completed using Groq after the Gemini key returned 402. Gemini billing must still be restored before switching narration back to Gemini.
- Render/Vercel deployment, external Cron wakeup, live final-video caption review, publishing, and analytics still need their deployment/account acceptance checks. Do not treat this release as fully production-verified until those pass.

Provider references: [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing#gemini-3.1-flash-lite), [Groq audio timestamps](https://console.groq.com/docs/speech-to-text), [Supabase scheduling](https://supabase.com/docs/guides/functions/schedule-functions), [YouTube resumable uploads](https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol), [YouTube analytics](https://developers.google.com/youtube/analytics/reference/reports/query).
# Text provider fallback

Text requests try free Gemini `gemini-3.1-flash-lite`, then Groq
`openai/gpt-oss-120b`, then paid Gemini only when paid text fallback is enabled.
Groq text uses the existing Settings Groq keys / `GROQ_API_KEYS` seed; no new
secret is required. Caption STT continues using the same credentials unchanged.
Text cooldowns are separate from STT. Groq text keys are configured from independent
accounts: a failure cools only that key and immediately tries the next available key.
After a failed round, wait 65 seconds and restart in configured order, at most three
rounds total (two retries). Longer Retry-After deadlines are respected; invalid
credentials are skipped. Keys within one organization still share its quota.
Gemini overload/503 errors cool down failed keys and advance to the fallback.
Groq JSON output still passes the existing Pydantic validation/repair workflow.
Deploy the backend changes to Render and the Settings label update to Vercel.
