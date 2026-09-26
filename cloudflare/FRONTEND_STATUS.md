# Backend selector and native Cloudflare UI

The frontend now has a saved Render/Cloudflare selector. Selection is browser-local,
credentials are separated by backend, and a connection check runs before switching.
Jobs remain on their original backend. Scheduling ownership is unchanged.

Cloudflare mode uses its own native workspace and API calls. Implemented controls include:

- Channel selection, review/direct/settings-based generation, publishing destinations,
  topic input, recent runs, progress, generation timing and Cloudinary playback.
- Version-specific review approval, YouTube/Instagram publishing confirmation,
  publication status and protection against duplicate uploads after uncertain results.
- Google/Meta account connections, YouTube analytics and Instagram insights.
- Schedule editing and backend ownership selection, guarded by Render deployment health.
- Saved-asset re-render, speed adjustment, BGM cropping/mixing/ducking, music uploads
  and catalog management. Unsaved edits or active rendering block approval/publishing.
- Channel create/edit/delete and validated settings updates.
- Alex/Sam duo conversations and configured primary-language narration, with English
  translated phrase captions for non-English speech.

Additional-language variant fan-out is unsupported. These features are implemented,
but their presence does not establish complete production parity, accounting coverage
or live end-to-end verification. The native UI does not proxy unsupported actions to
Render. Health responses still report `production_ready: false`.

Implementation references: `frontend/src/pages/CloudflareWorkspace.jsx`,
`frontend/src/components/Cloudflare*.jsx`, and `cloudflare/src/` modules
`index.ts`, `dashboardAdmin.ts`, `generation.ts`, `editing.ts`, `scheduler.ts`,
`oauth.ts`, `publishing.ts` and `publishingWorkflow.ts`.

## Deployment

Keep Render available. Scheduler ownership remains **Render**; deploy and verify
its ownership guard before attempting a transfer to Cloudflare. Cloudflare's cron
is configured, but UI backend selection does not transfer scheduling ownership.
In Vercel, build `frontend/` with these public (non-secret) variables:

```
VITE_RENDER_API_BASE=https://story-shorts-api.onrender.com/api
VITE_CLOUDFLARE_API_BASE=https://story-shorts-cloudflare-pilot.storyshort.workers.dev/api
```

Add the exact Vercel origin to Cloudflare `CORS_ORIGINS` and Render's CORS allowlist.
Do not use wildcard credentialed CORS. No production Vercel URL or linked Vercel
project was found in this workspace; production frontend deployment is unverified.
Admin tokens belong in the protected dashboard input, never VITE environment vars.

Google/Meta callbacks require allowlisting in their respective provider consoles.
The checked-in redirect URLs are:

```text
https://story-shorts-cloudflare-pilot.storyshort.workers.dev/auth/google/callback
https://story-shorts-cloudflare-pilot.storyshort.workers.dev/auth/meta/callback
```

## Verification snapshot (2026-09-27)

- Post-fix checks: **70 TypeScript, 8 frontend and 8 Python tests passed**;
  TypeScript checking and the Vite production build passed.
- Fresh review-only run `fe7d16a3614a45ebb179210cd8171b5f` completed in
  **806.734197 seconds** (13m 26.7s). Final FFmpeg: **111.326 seconds**,
  88.066667s duration, 30 fps, 20,559,212 bytes; Cloudinary HEAD returned 200.
  It was submitted through the local dashboard. API fixes were deployed during
  execution, so this is not an uninterrupted benchmark. Human content review remains.
- Live YouTube analytics and Instagram insights both returned **200** using the
  existing encrypted connections. Workers redirect handling was fixed and tested.
- Shared music folders and browser live mixing were verified. Soundtrack-only
  CircleCI edit `cb8d96b93bb55a4420cf268c18d58895` succeeded: 15% drama music,
  76.412s FFmpeg, 88.033333s/30fps output, HTTP 200, zero new provider calls.
  Dashboard shows the new preview and enables approval; nothing was published.
- Render guard deployment is **unverified**; its health request timed out. Scheduler owner
  remains Render. Dual-backend availability and a completed backend-switch round trip
  are not verified.
- Production Vercel origin is unknown; production deployment and CORS remain unverified.
- Provider-console callback allowlisting remains pending.
- **No live Cloudflare public publishing test has been completed.** A review-only
  generation run does not validate publishing, OAuth or scheduling in production.
- Free-tier fit is not guaranteed; measure real usage and account limits.

See the [production checklist](README.md#production-checklist-pending-gates) for
release gates. The local dashboard test is not a Vercel production deployment test.
