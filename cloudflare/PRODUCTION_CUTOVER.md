# Production cutover checklist

## Latest live acceptance test — 27 September 2026

- Real Cloudflare cron scheduled for 17:10 India time, admitted exactly one news request at 17:10:16.
- Request `fe04cb454231088706889758ab5710bb`: source selection, uniqueness reservation, copy, per-slide image planning/generation, CircleCI rendering, Cloudinary upload and awaiting-approval completed in **92.171 seconds** from request creation.
- Final JPEG returned HTTP 200. Original disabled schedule restored immediately after dispatch; no public upload performed.
- The model chose one slide. This test does not verify multi-slide visual distinctness, Instagram publishing, or the deployed Vercel UI.

## 1. Repository and database

- Current tested code is on `codex/cloudflare-fresh-generation`, not automatically on `main`.
- Either deploy that branch consistently, or merge it into `main` and change both CircleCI's config/checkout branch and Worker `CIRCLECI_BRANCH` together.
- Keep the existing Supabase PostgreSQL/pgvector project. Do not create D1 or migrate/delete its data.
- From `backend`, run `venv\Scripts\python.exe ..\cloudflare\scripts\install_rpc.py --apply` when installing/updating the Cloudflare SQL. It rolls back test fixtures.

## 2. Cloudinary

- Keep cloud `lvriw0hv` and prefix `story-shorts-cloudflare` unless deliberately migrating assets.
- Cloud name, API key and API secret go to Worker secrets only. Uploads are signed; an unsigned upload preset is not required.
- Public media URLs must remain accessible for Instagram ingestion. Do not delete queued-job inputs or approved media.
- Monitor storage, delivery and transformations: the credit allowance is shared usage, not a dedicated 25 GB storage allocation.

## 3. CircleCI

- Use the existing Cloudflare media pipeline; do not replace it with the Render pipeline.
- Config and checkout repository: `SriTharoon05/content-gen-ai`.
- Config path: `cloudflare/circleci/config.yml`.
- Config/checkout branch: same as Worker `CIRCLECI_BRANCH` (currently `codex/cloudflare-fresh-generation`).
- API-only triggers; leave Git push, PR and CircleCI schedule triggers off.
- YAML already selects Python 3.12, `medium`, and `RENDER_PROFILE=auto`.
- Context `story-shorts-cloudflare` contains only:
  - `CF_MEDIA_API_URL=https://story-shorts-cloudflare-pilot.storyshort.workers.dev` (no `/api` suffix).
  - `CF_MEDIA_WORKER_TOKEN`: exactly the Worker's `MEDIA_WORKER_TOKEN`.
- No Gemini/Groq, database or Cloudinary secret is needed in this context. The worker obtains task-scoped upload authorization from Cloudflare.

## 4. Cloudflare

Keep the existing Worker to avoid breaking callbacks and running Workflows. From `cloudflare`: `npm ci`, `npm run check`, `npm test`, `npm run deploy`.

Required secrets:

```text
ADMIN_TOKEN
MEDIA_WORKER_TOKEN
SUPABASE_URL
SUPABASE_KEY
CLOUDINARY_CLOUD_NAME
CLOUDINARY_API_KEY
CLOUDINARY_API_SECRET
CIRCLECI_TOKEN
CIRCLECI_PROJECT_SLUG
CIRCLECI_PIPELINE_DEFINITION_ID
NEWSDATA_API_KEY
GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET
META_APP_ID
META_APP_SECRET
```

Use the server-side Supabase service-role key, never the public anon key. Preserve any existing `OAUTH_ENCRYPTION_KEY`; changing encryption credentials without migration/reconnection can invalidate stored account connections. Use a strong new admin token before public rollout if the old one was shared or predictable. Never put secrets in Git or `VITE_*` variables.

Provider generation keys are read from existing Supabase runtime settings. Verify them in the Cloudflare dashboard Settings; merely adding a Worker Gemini/Groq environment variable does not replace that settings store.

Keep all six Workflow bindings and the every-minute cron in `wrangler.jsonc`. Dashboard schedule time is interpreted with its configured offset (`330` for India). No Render keepalive is required for news generation.

Set `CORS_ORIGINS` to the exact production Vercel origin (no path or trailing slash). Keep only localhost origins you still need. Deploy after changing it.

OAuth callbacks in the provider consoles must exactly match:

```text
https://story-shorts-cloudflare-pilot.storyshort.workers.dev/auth/google/callback
https://story-shorts-cloudflare-pilot.storyshort.workers.dev/auth/meta/callback
```

## 5. Vercel

Import the repository, select the chosen production branch, root directory `frontend`, framework Vite, install `npm ci`, build `npm run build`, output `dist`. Existing `frontend/vercel.json` handles SPA rewrites.

Vercel Hobby is restricted to personal, non-commercial use. If this dashboard supports a commercial/monetized operation, verify eligibility or use the appropriate plan: https://vercel.com/docs/plans/hobby . Cloudinary credits cover combined storage, bandwidth and transformations: https://cloudinary.com/documentation/billing_and_plans . Do not promise that every production workload fits free tiers.

Set the nonsecret build variable:

```text
VITE_CLOUDFLARE_API_BASE=https://story-shorts-cloudflare-pilot.storyshort.workers.dev/api
```

If retaining Render switching, also set `VITE_RENDER_API_BASE=https://story-shorts-api.onrender.com/api`. Deploy/redeploy after environment changes. On the deployed dashboard choose **Backend > Cloudflare**, enter the admin token there, and save. The selection is stored per browser; the current app defaults to Render until selected.

Copy the assigned Vercel origin into Worker CORS and redeploy the Worker. Verify dashboard reads and previews from that deployed origin before calling frontend cutover complete.

## 6. Enable gradually

- Start with review/manual publishing. User verifies Instagram publishing separately.
- In Instagram news configure channel, categories, daily count/budget, time and offset, then enable its schedule.
- News counts today's manual and scheduled requests toward the daily target, including failed requests. It admits at most one per minute within a four-hour window. Setting the target below today's already-requested count intentionally produces no more jobs that day.
- Keep video and news budgets separate in planning; both consume shared image/CI/storage allowances.
- Watch failed jobs, duplicate reservations and provider quota errors. Do not blindly resubmit an uncertain image purchase or Instagram upload.

## Remaining Render dependency

`saveSchedule` in `cloudflare/src/scheduler.ts` checks Render `/health` for the scheduler ownership guard before selecting Cloudflare as video scheduler owner. Do not delete Render or claim complete Render independence until this guard has been deliberately migrated. This does not affect the separate news cron path. Legacy Supabase Storage assets also remain readable; do not delete them during cutover.

References: [Vercel Vite](https://vercel.com/docs/frameworks/frontend/vite), [Cloudflare cron](https://developers.cloudflare.com/workers/configuration/cron-triggers/), [CircleCI API triggers](https://circleci.com/docs/guides/orchestrate/triggers-overview/).
