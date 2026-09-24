# Backend selector and native generation UI

The frontend now has a saved Render/Cloudflare selector. Selection is browser-local,
credentials are separated by backend, and a connection check runs before switching.
Jobs remain on their original backend. Scheduling ownership is unchanged.

Cloudflare mode currently uses its own generation workspace: supported-channel
selection, fresh review-only generation, recent runs, live generation time and
Cloudinary playback. It does not proxy unsupported actions back to Render.

This is NOT full feature parity. Publishing/OAuth, scheduling, settings/channel
editing, BGM/remix/speed editing, multilingual/conversation workflows, analytics,
and complete accounting still need migration. Production-readiness remains false.

## Deployment

Keep the existing Render deployment and its environment unchanged.
In Vercel, build `frontend/` with these public (non-secret) variables:

```
VITE_RENDER_API_BASE=https://story-shorts-api.onrender.com/api
VITE_CLOUDFLARE_API_BASE=https://story-shorts-cloudflare-pilot.storyshort.workers.dev/api
```

Add the exact Vercel origin to Cloudflare `CORS_ORIGINS` and Render's CORS allowlist.
Do not use wildcard credentialed CORS. No production Vercel URL or linked Vercel
project was found in this workspace; production frontend deployment is unverified.
Admin tokens belong in the protected dashboard input, never VITE environment vars.

## Verification in progress

- Cloudflare deployment `e053c645-c8a9-4525-8e53-1ff7bb92a476` serves native APIs.
- Browser verified saved Cloudflare selection, channel list, prior videos/timings.
- Fresh LoreHush run submitted by clicking the frontend generation button:
  `1b025b6fb84945c7b348e9357ebfcaed`, 2026-09-24 15:06:01 UTC.
- This run is review-only, uses fresh media and the three-image parallel path.
- Its final result and new timing are not yet established.
- Render health request timed out after 60 seconds; no claim of verified dual-backend
  availability or completed Render-to-Cloudflare-to-Render round-trip UI testing.
