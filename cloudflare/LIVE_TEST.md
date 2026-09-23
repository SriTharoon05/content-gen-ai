# Fresh live integration test — 2026-09-23

Video: `d8bf3c698c61426297a88935f6cd1b05` (LoreHush, English, review only).

Verified live: free Gemini narration, Groq timestamped transcription, 21 new
Pollinations images, Cloudinary asset storage, CircleCI canonical rendering,
Supabase completion and `AWAITING_APPROVAL`. No reused assets or publications.
22 asset rows (images plus narration) point to Cloudinary.

Final render task: `8728cc894c236e5112e8f257ed858cfb`.
CircleCI pipeline: `7468c82d-b887-40ea-b3ca-54a96f00abfc`, success.

| Metric | Observed |
| --- | --- |
| Output duration / FPS | 88 seconds / 30 |
| File size | 19,361,462 bytes |
| Canonical rendering time | 64.495 seconds |
| Final CircleCI workflow including setup and transfer | 127 seconds |
| Peak render RAM | 1,581,600,768 bytes |
| CPU utilization | 92.96% of two CPUs |
| FFmpeg passes / encoding passes | 9 / 8 |

These times are for final assembly, not total generation. Provider generation,
two audio preparation jobs and recovery added substantial time to this debugging
run; this does not establish steady-state throughput or free-tier daily capacity.

Final media HEAD returned HTTP 200, video/mp4, AVC codec.

[Preview](https://res.cloudinary.com/lvriw0hv/video/upload/v1790181388/story-shorts-cloudflare/tasks/8728cc894c236e5112e8f257ed858cfb/final.mp4)

Image ledger: 0.042 settled credits plus one uncertain 0.002-credit request,
conservatively recorded as 0.044. The uncertain request is not confirmed billed.
Subsequent checkpoint recovery did not buy replacement images.

## Limitations

This is an unpublished technical test, not approved editorial content. The script
drifted from its reserved premise and presented invented historical claims as
facts. Future QA prompts were strengthened, but that improvement needs another
separate content-quality validation; this output must not be treated as factual.

The complete Render replacement is NOT ready. Existing dashboard routes,
OAuth/publishing, scheduling, edit orchestration and multilingual/conversation
parity remain migration gates. Keep the production frontend and Render unchanged.

Verification: 22 TypeScript tests, 7 Python media tests, TypeScript checking,
and transactional live SQL RPC tests passed (including review-only completion,
asset persistence, duplicate handling and access restrictions).
