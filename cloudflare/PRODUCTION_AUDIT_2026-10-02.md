# Production audit — 2 October 2026

## Scope and decision

Audit covers the React/Vite dashboard, Cloudflare API/Workflows, shared Supabase PostgreSQL/pgvector, Cloudinary media, provider rotation, scheduling, editing/reuse, and the canonical CircleCI renderer. Render remains available during cutover. No test was publicly published, no existing media was deleted, and no provider plan was upgraded.

Do not equate successful generation with verified public publishing or a guarantee that every provider is available. Instagram publishing is left to the owner as requested. The deployed dashboard is a Vercel **branch preview**, not a verified `main` Production deployment.

**Final acceptance: three fresh, overlapping deployed-dashboard → Cloudflare → providers → Cloudinary → CircleCI → Supabase → dashboard-review jobs completed without manual intervention. Average full generation time: 7m 25.408s. Generation-to-review is verified; autonomous editorial acceptance and the final scheduler/production-branch cutover are not.** All three remain `AWAITING_APPROVAL`. No video was approved or published.

## Fixes implemented

- Canonical channel instructions now require specific curiosity/puzzle hooks for factual channels, intriguing openings for fiction, fluent connected narration, and an informative payoff. No copied creator scripts or promise of virality.
- Clear, lively narration replaces legacy breathy/husky story casting. CuriousDuo has explicit two-speaker turns: scene changes do not force speaker changes. Owner customization is preserved.
- A sentence-level English fluency guard rejects three consecutive short/telegraphic sentences, filler-heavy actual turns and repeated dependent-clause fragments before TTS. It inspects joined narration and aggregated same-speaker turns, not image-sized fragments, and allows natural short replies followed by substance. Writer/repair/QA instructions explicitly prevent choppy endings and challenge unsupported consensus or conflated scientific mechanisms. This guard is deliberately narrow: passing it does not prove natural delivery or factual accuracy.
- Closed script schemas now enforce narration, scene IDs, field bounds and exactly 25 planned scenes. Three bounded repair attempts return actionable JSON-path feedback instead of accepting malformed/empty scripts.
- Writer-flagged unsupported claims force factual QA failure, even if the model's reviewer incorrectly marks the script passed. Two editorial revisions occur before TTS or image purchases. This is not external fact verification and cannot guarantee that an unflagged claim is true.
- Duplicate concepts trigger up to three distinct five-concept sets without weakening permanent entity/angle and pgvector uniqueness reservations. All exhausted sets fail before TTS/images.
- Non-concept model prompts no longer resend 30 historical concepts. This reduces repeated text tokens and latency without removing channel instructions.
- Image generation uses bounded parallel batches (three with the current single image key; two with multiple keys) while retaining per-image budget reservations/checkpoints and model-specific rate gates. Failed siblings are drained, not silently abandoned.
- A real workerd regression exposed unsupported `redirect: 'error'` options that Node fetch mocks accepted. Image generation/download now use `manual`; provider redirects are rejected without forwarding credentials. Tests execute the actual Cloudflare runtime without buying images.
- Three-channel contention exposed starvation in the shared rate gate. PostgreSQL now atomically assigns future model-specific slots; each requester waits once rather than racing six polls. The 300/60-RPM safety margins remain unchanged.
- New image calls now persist their exact request, deterministic seed, model, endpoint, timestamp and hashed key identity in the same credit reservation. Up to three identical Pollinations requests handle transient errors/lost replies; ambiguous requests never switch keys. A definitive initial 401/403 can advance to the next key after a compare-and-set. Retries rely on the provider's documented in-flight/cache contract, not an absolute billing guarantee. Legacy uncertain calls, changed requests/keys, custom endpoints and expired 15-minute replays remain blocked. No reservation is cleared/refunded merely because a reply was lost. Three-image children have a tested **45-external-request** bound and no whole-batch Workflow retries.
- Independent provider keys fail over individually. Only an exhausted sweep causes bounded cooldown/retry. Gemini 3.1 Flash Lite remains primary text generation; Groq GPT-OSS 120B is fallback. Gemini embeddings remain required for uniqueness.
- Scheduling failures no longer starve sibling jobs; submitted IDs and CircleCI acceptance/claim remain durable and idempotent. Stage waits include retry deadlines and recover saved checkpoints after lost notifications.
- The canonical auto renderer reuses prepared intermediates, uses detected CPU/cgroup memory, bounded parallel asset downloads and measured resource/pass metrics. The proven explicit `low_memory` mode is preserved. No FFmpeg runs inside Cloudflare.
- Frontend controls, voice/language selection, safe editing bounds, publication locks, analytics, readability, polling and generation timing were checked/improved. Existing assets can still be reused without new image generation.
- Admin-only `/api/platform-usage` reports whitelisted Cloudinary quota figures without exposing credentials.

Principal changed areas: `backend/app/agents/roles.py`, `backend/app/{schemas,channels,key_pool,media,render_profile}.py`, canonical editorial skills, media-only CircleCI setup, `cloudflare/src/{generation,stages,providers,scriptRepair,factualReview,scheduler,index}.ts`, Cloudflare SQL/install fixtures/tests, and frontend channel/run/editor/settings/analytics controls. Exact changes are in branch `codex/cloudflare-fresh-generation`; audit commits include `08aadc0`, `8ad68b9`, `e5fc191`, `f405f87`, `f956f48`, `4d71ab4`, `2f29f9b`, `258eab7` and `a1eab47`. Final three runs used source `a1eab47`, Worker version `0bcf8650-3e8b-44ff-84de-8ba2ae33a733`.

## Automated verification

| Check | Result |
|---|---|
| Full backend unittest suite | 139 passed; 5 disposable-PostgreSQL tests skipped (144 total) |
| Cloudflare TypeScript | Passed |
| Cloudflare tests including real workerd, queued-rate, image recovery and fluency/mechanism checks | 152 passed |
| Frontend build | Passed |
| Frontend tests | 17 passed |
| Focused canonical media checks | 37 passed; additional clean Python 3.12 real-render/import checks passed |
| Supabase Cloudflare RPC install/fixtures | Applied; fixture transactions rolled back |
| Deployed Vercel → Worker → shared DB | Live reads and fresh submissions verified |
| Deployed API/CORS/authentication | Health/channels/schedule/settings/music/videos HTTP 200; exact Vercel CORS; preflight 204; invalid admin token 401 |
| Tracked-file credential-pattern scan | 233 tracked files; no tested Gemini/Groq/CircleCI/GitHub token patterns; only placeholder `.env.example` files tracked |
| Public Instagram/YouTube upload this audit | Not performed |

The skipped backend tests require a disposable `TEST_RENDER_DATABASE_URL`; destructive test fixtures must not point at the production database.

Combined main suites: **308 passed, 5 skipped**. Backend discovery took 57.640s. Frontend has no `npm test` script; its five Node test files were invoked directly, followed by `npm run build`. Builds/tests are regression evidence, not proof of every feature under every provider outage.

## Live evidence and failures

Earlier acceptance attempts exposed real issues; they are not hidden from the success rate:

| Channel / ID prefix | Result | Elapsed | Finding |
|---|---|---:|---|
| CurioNerve `b76259c5` | Failed | 9m 29.890s | Pollinations HTTP 503 during image generation; uncertain-purchase guard prevented a second charge |
| NeuraScify `b78d8d53` | Failed | 3m 10.242s | Missing narration on repaired script; strict schema strengthened |
| CuriousDuo `5b0c5690` | Failed | 2m 6.109s | 218 spoken words exceeded the 205-word gate |
| CuriousDuo `31d7ecb2` | Failed | About 2m 11s | Empty narration in late beats; field constraints and repair feedback strengthened |
| CuriousDuo `681f5cee` | Failed | About 33s | All five concepts collided; bounded topic replanning added |
| NeuraScify `ffa578b2` | Technical completion, editorial rejection | 7m 27.089s | Fresh 25-image, 68.5s/30fps video; manual audit found speculative claims presented as fact, so factual-risk gating was added |
| CurioNerve `64a1eddc` | Failed before media | 1m 46.785s | Writer retained unresolved flags through two revisions; prompt/schema semantics clarified, gate preserved |
| NeuraScify `88e7d90a` | Failed before media | 1m 6.195s | Same stale flag semantics; script also contained audio-direction tags, separated from spoken text |
| CuriousDuo `a53996f0` | Failed before media | 2m 56.910s | Same stale flag semantics; unsupported performance claims in the premise removed from future planning guidance |

Do not approve the older `ffa578b2` technical sample as production content. Its measurements are valid for infrastructure, not editorial acceptance.

The following fresh three-channel runs used the corrected flag semantics. No manual restart, input edit or Worker deployment occurred inside these runs:

| Channel / ID prefix | Result | Elapsed | Finding |
|---|---|---:|---|
| CurioNerve `6c43ae50` | Technical completion, editorial rejection | 7m 17.032s | 25 fresh images; manual review found choppy final narration and conflated visual mechanisms. Fluency/mechanism guidance strengthened before further runs. |
| NeuraScify `9ee10799` | Failed during images | 4m 30.690s | Original Pollinations HTTP 503 on `s003`; uncertain-purchase guard stopped a blind repeat. |
| CuriousDuo `8c5c61c4` | Failed during images | 5m 31.984s | Original Pollinations HTTP 503 on `s008`; sibling images were checkpointed. Manual review also found a disputed mechanism presented too confidently. |

Neither technical sample (`ffa578b2`, `6c43ae50`) is accepted production content. A rendered MP4 or model QA pass is not proof of factual accuracy. Human review remains necessary with external fact checking disabled.

### Runtime compatibility and concurrent rate-gate acceptance attempts

| Channel / ID prefix | Original result | Original elapsed | Recovery result |
|---|---|---:|---|
| CurioNerve `5ba4e6ce` | Unsupported fetch redirect mode, before provider request | 6m 21.946s | Resumed after the runtime fix, then rate-slot contention failed at image `s005`; 5 images settled, 1 reserved. Original submission-to-last failure **19m 23.227s**. |
| NeuraScify `32996f27` | Same runtime compatibility error | 4m 45.790s | Resumed, then rate-slot contention failed at `s014`; 14 settled, 1 reserved. Original submission-to-last failure **18m 50.618s**. |
| CuriousDuo `729b58c2` | Same runtime compatibility error | 3m 29.490s | Resumed to technical completion with 25 new images, **25m 44.008s** original submission-to-completion; this includes manual intervention and is NOT an uninterrupted benchmark. |

The CuriousDuo technical output is in dashboard review, not approved or published. Manual script review found excessive short filler turns and an oversimplified explanation, so it is not an editorial acceptance pass. New sentence/actual-turn guards address filler that escaped the initial four-word guard.

Recovered CuriousDuo output: 720×1280, 30 FPS, **74.4s**, **14,594,812 bytes**, Cloudinary HTTP 200. Canonical final FFmpeg **61.976s**, 9 passes/8 encoding passes, peak RAM **1,580,507,136 bytes (~1.472 GiB)**, CPU **115.715 CPU-seconds / 93.35% of two CPUs**. Audio preparation **0.635s**. Task creation-to-finish **48.560s audio + 126.739s final** includes queue/control-plane time and is NOT CircleCI billed execution time.

### Final uninterrupted three-channel acceptance

All times below are UTC on 2 October 2026. End-to-end elapsed time uses `Job.created_at` → `Job.updated_at`; automatic repairs/retries and queue/transfer time are included. These are overlapping, shared-load runs, not isolated capacity benchmarks. No manual restart, edited inputs, reused images or deployment occurred during them.

| Channel / video ID | Submitted | Completed | Full generation | Final FFmpeg | Output duration | Final MP4 bytes |
|---|---|---|---:|---:|---:|---:|
| NeuraScify `fe5c0950d6964ce688c2c4f931acde82` | 09:23:05.285911 | 09:30:55.402520 | **470.116609s / 7m 50.117s** | 67.542s | 68.9s | 14,325,914 |
| CuriousDuo `1b645ffa5ed84b01822ee521881a8900` | 09:23:53.745159 | 09:30:54.170263 | **420.425104s / 7m 00.425s** | 51.318s | 68.733333s | 14,923,048 |
| CurioNerve `780d15d951a6428c9555e0b3fedafcac` | 09:26:08.430046 | 09:33:34.112953 | **445.682907s / 7m 25.683s** | 71.920s | 78.3s | 14,132,140 |

Every final output is **720×1280, 30 FPS**, uses **25 newly generated images, zero reused**, and has a 0.0025-credit image ledger. All final MP4s returned HTTP 200. The deployed dashboard shows completion, full generation time, playable output, the asset-only editor and locked publishing until approval.

| Channel | FFmpeg peak RAM | CPU utilization of two CPUs | FFmpeg passes / encoding passes | Audio CI running time | Final CI running time | All CI running time | Estimated standard-medium credits |
|---|---:|---:|---:|---:|---:|---:|---:|
| NeuraScify | 1,273,884,672 bytes (~1.186 GiB) | 93.44% | 10 / 9 | 38.746s (job 69) | 109.746s (job 72) | 148.492s | 24.749 |
| CuriousDuo | 1,340,416,000 bytes (~1.248 GiB) | 92.70% | 10 / 9 | 38.643s (job 70) | 91.385s (job 73) | 130.028s | 21.671 |
| CurioNerve | 1,445,687,296 bytes (~1.346 GiB) | 92.90% | 10 / 9 | 45.554s (job 71) | 116.313s (job 74) | 161.867s | 26.978 |

CircleCI figures are the API's running start-to-stop durations, including setup/checkout/dependency installation/download/upload. Queue/control-plane waits are not added to billed execution. These estimates are **not invoices**. Mean observed all-CI usage is approximately 24.466 credits/video; 30/day would be ~22,019 credits/30 days before extra edits/failures/news work.

The final CuriousDuo job had **156 recorded Workflow steps across 39 completed instances**, including 119 ordinary steps and 37 event waits, with no extra attempts and no missing children. Counts include both media tasks and their child workflows. This confirms the revised healthy-job baseline, not a maximum: editorial revisions, provider fallback, edits and publishing can increase it.

Dashboard previews:

- [NeuraScify — skyscraper damping](https://res.cloudinary.com/lvriw0hv/video/upload/v1790933450/story-shorts-cloudflare/tasks/7a31ec6cc3021b49780dbdcafb72a476/final.mp4)
- [CuriousDuo — honeycomb geometry](https://res.cloudinary.com/lvriw0hv/video/upload/v1790933446/story-shorts-cloudflare/tasks/5b08bb32f0230d7118800cc917177c2e/final.mp4)
- [CurioNerve — phantom phone vibrations](https://res.cloudinary.com/lvriw0hv/video/upload/v1790933608/story-shorts-cloudflare/tasks/f31de3f4c5c86ec64a8bb1e92c6c0505/final.mp4)

**Editorial result is not an autonomous-publication pass.** NeuraScify still has short/choppy phrasing and absolute claims such as “every new supertall” having a damper. CurioNerve presents a speculative evolutionary/predictive explanation too confidently and has a choppy ending. CuriousDuo's nine real speaker turns are more substantive, but wording such as bees' “entire lives” and a “purely” evolutionary explanation still needs qualification. Prompts, repair and model QA were improved; manual review shows they do not reliably catch all overclaims. Keep human review enabled, and revise these scripts before publishing. External source verification remains disabled by owner preference.

### Measured infrastructure sample (`ffa578b2`)

- Full job: **447.089 seconds**, including one automatic script repair.
- Final FFmpeg: **47.352 seconds**; 10 FFmpeg passes, 9 encoding passes.
- Output: 720×1280, 30 fps, 68.5 seconds, 13,806,413 bytes; final URL returned HTTP 200.
- FFmpeg peak RAM: **1,238,962,176 bytes (~1.154 GiB)**.
- CPU: 87.349 CPU-seconds; **92.23% of the allocated two CPUs** during rendering.
- Complete CircleCI running time: audio job 41.839s + final job 84.075s = **125.914s**, approximately **20.986 credits** at standard medium's 10 credits/minute. This includes setup/download/upload, not just FFmpeg. It is a duration-based estimate, not an invoice.
- Old orchestration: **228 measured Workflow steps**, 57 instances, including 175 ordinary steps and 53 event waits. New image batches reduce orchestration; final revised counts must be measured rather than assuming 228 or an ideal minimum.

### Revised batching infrastructure sample (`6c43ae50`)

- Original submission 08:00:15.057979 UTC to Job completion 08:07:32.089839 UTC: **437.031860s**.
- Final FFmpeg **56.177s**, 10 passes/9 encoding passes; peak RAM **1,412,259,840 bytes (~1.315 GiB)**, CPU **103.837 CPU-seconds / 92.42% of two CPUs**.
- Output 720×1280, 30 FPS, 2,026 frames, **67.533333s**, **18,671,226 bytes**, HTTP 200; 25 fresh images, zero reused, 0.0025 image credits.
- All CircleCI running time: audio job 61 **65.127s** + final job 64 **96.077s** = **161.204s** (API duration sum 160.658s); approximately **26.867 credits**, not an invoice.
- **156 recorded Workflow steps / 39 instances**, including 119 ordinary steps and 37 event waits. This includes generation coordinators/children AND media coordinators/children; dropping media children would undercount usage. One extra attempt does not add a new billed step under published retry accounting.
- The healthy-generation theoretical ceiling from this sample alone is `floor(3000 / 156) = 19` videos/day, **before publishing, news, edits, failed attempts or a safety reserve**. At 15/day generation alone consumes 2,340 steps; 20/day already requires 3,120; 30/day 4,680.
- Compared with the older sample, batching reduced recorded steps ~32% (228 → 156), but these different-content jobs do not prove a latency speedup. Whole-job time remained around 7 minutes.

Provider retry basis: [Pollinations retry recipes](https://github.com/pollinations/pollinations/blob/main/gen.pollinations.ai/src/docs/apidocs-recipes.md), [stable-seed image cache identity](https://github.com/pollinations/pollinations/blob/main/gen.pollinations.ai/src/routes/images.ts), [in-flight deduplication](https://github.com/pollinations/pollinations/blob/main/gen.pollinations.ai/src/middleware/generation-deduplication.ts), [cache/error billing handling](https://github.com/pollinations/pollinations/blob/main/gen.pollinations.ai/src/middleware/track.ts). Public source describes behavior; service changes/outages can still require reconciliation.

## Capacity assumptions

Planning assumes 30 days/month, 25 fresh images/video, roughly 60–90s narration, standard Docker medium CircleCI, review enabled, and no repeated edits. Scheduled and manual jobs, failed attempts, news posts, remixes and publication all share the same quotas.

Configured independent accounts: **5 Gemini free projects, 4 Groq accounts, 1 Pollinations key**. Independence is the owner's assertion; multiple keys in the same organization/project do not multiply quotas.

### Service limits and caveats

| Service | Relevant free allowance | Impact |
|---|---|---|
| Cloudflare Workflows | 3,000 steps/day, 1 GB Workflow storage, published Free CPU limit 10ms | Steps are a main daily bottleneck; extra stages for Groq TTS chunks, edits, news and publishing add work. Retry attempts alone do not add billed steps |
| Cloudflare Workers | 100,000 requests/day shared with Workflows; 128 MB memory; free 50 external subrequests/instance | Request volume is modest; CPU/subrequest safety still matters and network waiting is not CPU time |
| CircleCI standard Docker medium | 2 CPUs/4 GB, 10 credits/minute, 30,000 free credits/month | 3,000 billed minutes/month (~100/day averaged); not currently the first bottleneck |
| Groq GPT-OSS 120B, per organization | 30 RPM, 1,000 RPD, 8,000 TPM, 200,000 TPD | Four independent accounts give 800k TPD, but the 8k per-account TPM still applies; actual text-token usage is not currently measured per job |
| Groq Orpheus, per organization | 10 RPM, 100 RPD, 1,200 TPM, 3,600 TPD | Four accounts: 400 requests/14,400 tokens per day. **200 characters/request** means many requests/video, not one |
| Groq Whisper, per organization | 20 RPM, 2,000 RPD, 7,200 audio seconds/hour, 28,800/day | Four accounts: 115,200 audio seconds/day; 10–30 videos are far below the daily audio allowance |
| Gemini | Actual limits vary by model/project and are shown in AI Studio | No verified fixed daily capacity for the five projects; embeddings have no Groq fallback, and TTS model availability matters |
| Cloudinary Free | 25 credits shared across storage, bandwidth and transformations | Not simply 25 GB available for all purposes; storage accumulates, bandwidth/transforms use a rolling window |
| Supabase Free | 500 MB database, shared 5 GB uncached + 5 GB cached egress | Measured database ~29.1 MB; Cloudinary avoids new media storage here, but persistent checkpoints/ledger still grow |
| YouTube API | Default separate 100 uploads/day, plus other project quotas | 30 uploads/day is below the default count; eligibility and actual project quota must still be checked |
| Instagram | Account-specific publishing permissions and quota | Owner verifies publishing; actual connected-account limit not measured in this audit |
| Vercel Hobby | Personal/non-commercial usage restriction | Verify plan eligibility before monetized/commercial operation; preview deployment is not the production-branch cutover |

Cloudflare billing subscriptions could not be inspected with the present token (403); this report models **Free**, not a verified account billing plan. A successful test does not prove every execution stays inside a hard 10ms CPU limit or establish 30-video load capacity.

CircleCI's self-hosted-runner network allowance does not apply to this hosted Docker executor's transfers; cloud-executor egress is not passed on as that charge. CI has a separate 2 GB included artifact/cache/workspace storage allowance. The media is uploaded directly to Cloudinary, not retained as CI artifacts; dependency caches and any future artifact retention still need monitoring. Docker layer caching/IP-range add-ons are not enabled. See [CircleCI plan accounting](https://circleci.com/pricing/).

News posts share these budgets with videos. The NewsData key's actual allowance was not independently confirmed; the owner has referred to both 200/month and 200/day. Cached category feeds reduce calls, but neither an unverified NewsData allowance nor a separate 10–20-post/day goal can be added on top of the video maximum without reserving Cloudflare/Cloudinary/CircleCI usage.

Sources: [Workflow pricing](https://developers.cloudflare.com/workflows/reference/pricing/), [Workflow limits](https://developers.cloudflare.com/workflows/reference/limits/), [CircleCI pricing](https://circleci.com/pricing/), [CircleCI resource prices](https://circleci.com/pricing/price-list/), [CircleCI credit accounting](https://circleci.com/docs/guides/plans-pricing/credits/), [Groq limits](https://console.groq.com/docs/rate-limits), [Orpheus input limits](https://console.groq.com/docs/text-to-speech/orpheus), [Gemini project limits](https://ai.google.dev/gemini-api/docs/rate-limits), [Cloudinary credit accounting](https://cloudinary.com/documentation/billing_and_plans), [Supabase database size](https://supabase.com/docs/guides/platform/database-size), [Supabase egress](https://supabase.com/docs/guides/platform/manage-your-usage/egress), [YouTube upload endpoint](https://developers.google.com/youtube/v3/docs/videos/insert), [Vercel Hobby](https://vercel.com/docs/plans/hobby).

### Image cost: cheap, but not free

The public [Pollinations model catalog](https://gen.pollinations.ai/image/models), fetched on 2 October, confirms DreamShaper 8 LCM 0.0001 credit/image, 300 RPM; FLUX.1 Schnell 0.002, 60 RPM; Z-Image Turbo 0.004, 60 RPM. DreamShaper's catalog health snapshot reported approximately 97.22% request success; provider availability, not RPM exhaustion, caused the observed image failures. This aggregate snapshot is not a guarantee or an independently measured per-video success rate. The account balance API was not accessible, so saved balance is not provider-confirmed.

The shared database gate spaces DreamShaper requests by 220ms (~272 RPM ceiling) and the 60-RPM models by 1.1s (~54 RPM), while the memory-safe actual concurrency is lower. At 25 images/video and $1.185/credit:

| Videos/day | Videos/30 days | DreamShaper credits/month | Approximate image cost |
|---:|---:|---:|---:|
| 10 | 300 | 0.75 | $0.89 |
| 15 | 450 | 1.125 | $1.33 |
| 20 | 600 | 1.5 | $1.78 |
| 30 | 900 | 2.25 | $2.67 |

30 images increase these costs 20%. Failed uncertain purchases may consume budget. After the final three tests, the app's saved credit balance minus reserved/settled/uncertain calls was **0.0175 credit (approximately seven more 25-image videos total, before other purchases)**. That is not a fresh daily allowance or a confirmed real provider balance. A top-up/saved-balance reconciliation will be needed for sustained production; no purchase or refund was made by this audit.

### Cloudinary: monthly sustainability is different from a one-day burst

Measured technical sample media: images 722,571 bytes + source audio 3,168,570 + prepared audio 6,577,004 + MP4 13,806,413 = **24,274,558 bytes/video**, excluding small checkpoints.

Modeled downloads: source/prepared audio for processing and ASR plus images ~17.05 MB; one full dashboard preview + YouTube ingest + Instagram ingest add 3×13.81 MB. Total modeled bandwidth **58.46 MB/video**. This is a planning estimate, not measured billing; repeat previews/edits increase it.

| Videos/day | 30-day stored media + modeled delivery | Shared credits before transformations/current assets |
|---:|---:|---:|
| 10 | ~24.82 GB decimal | ~23.12 GiB-equivalent |
| 15 | ~37.23 GB decimal | ~34.68 |
| 20 | ~49.64 GB decimal | ~46.24 |
| 30 | ~74.46 GB decimal | ~69.37 |

**Upload processing must also be included.** Cloudinary counts each image/video upload as one transformation even without an incoming transformation; raw JSON assets are exempt. A healthy Gemini-TTS video here has at least 25 image uploads + source audio + prepared audio + final MP4 = **28 upload transformations (0.028 credit/video)**. Groq chunks, remixes, replacement images and default Cloudinary optimizations can add more. See [transformation counting basics](https://cloudinary.com/documentation/transformation_counts).

| Videos/day | Older 13.81 MB MP4 sample: combined estimated credits/30 days including minimum upload processing and existing 1.13 credits | Revised 18.67 MB MP4 sample under the same other-asset assumptions |
|---:|---:|---:|
| 6 | ~20.0 | ~23.3 |
| 7 | ~23.2 | ~27.0 |
| 10 | ~32.6 | ~38.1 |
| 15 | ~48.4 | ~56.6 |
| 20 | ~64.2 | ~75.0 |
| 30 | ~95.7 | ~112.0 |

The earlier size-range estimates above use the historical 1.13-credit starting snapshot. The latest live usage endpoint returned Free plan, **1.37/25 credits (5.48%)**, 579,936,572 bytes stored, 354,310,371 bytes delivered and 497 transformations. Its `last_updated` still says **1 October**, so this is not a real-time invoice or assurance that all of today's work is settled.

Final CuriousDuo assets were individually checked with HTTP HEAD, without downloading them again: 25 images **742,270 bytes**, source audio **3,162,810**, prepared narration **6,600,044**, final MP4 **14,923,048**; total **25,428,172 bytes** excluding small JSON checkpoints. A modeled processing download is source audio once + prepared audio for ASR and assembly + images = **17,105,168 bytes**. One full preview and both publishing ingests add three MP4 deliveries, producing **61,874,312 modeled bandwidth bytes/video**.

| Videos/day | Latest 14.92 MB MP4 sample: estimated combined credits after 30 days |
|---:|---:|
| 6 | ~21.0 |
| 7 | ~24.3 |
| 10 | ~34.2 |
| 15 | ~50.6 |
| 20 | ~67.0 |
| 30 | ~99.7 |

Calculation: `1.37 + 30 × videos_per_day × ((25,428,172 + 61,874,312) / 1,073,741,824 + 28 / 1000)`. This conservatively carries the current usage snapshot into the forecast; some rolling usage expires, whereas existing retained storage does not. It is a planning estimate, not measured billing. Extra captions/checkpoints, cached-range/player behavior, provider fallback, news, remixes, multiple previews and default Cloudinary optimizations can change it.

Under **30-day asset retention, one full preview and both-platform ingestion**, roughly **6–7/day** fits the measured-size range before news, edits, failed uploads or a safety margin; **6/day is the conservative starting point**, not a guarantee. Even this is not perpetual: retained assets keep accumulating unless an owner-approved retention policy is implemented. With the final sample, ten/day plus ~7-day retention and eliminating one full preview is ~24.5 modeled credits—very tight, with little room for news/edits and larger files. No deletion policy has been enabled or inferred. Single-platform delivery or shorter retention changes the forecast without lowering video quality.

### Practical recommendation

- **Use 6/day as the conservative free-hosting start under 30-day retention/full-preview/both-platform assumptions; 7/day is borderline with larger MP4s.** Images still use prepaid Pollinations credits. Unlimited permanent asset retention is not sustainably free at any nonzero daily rate.
- **The planned 10/day is conditional**, not an all-free promise: agree retention, avoid unnecessary full preview downloads, reserve room for news/edits, and monitor real Cloudinary usage. No output-quality reduction is proposed.
- **15/day may be a controlled burst** with new batch orchestration, available TTS, no many-step fallback/revisions, and separate news/edit budgets. It is not sustainably free under the modeled Cloudinary delivery/retention pattern.
- **20 or 30/day is not a defensible strict-free recommendation** for the current whole stack. Workflow steps and Cloudinary are limiting before CircleCI in the observed workload.
- CircleCI alone has headroom: final samples used ~21.67–26.98 credits/video (mean ~24.47); 30/day would be ~19,504–24,280 credits/month, mean ~22,019, before extra operations. Its allowance is shared; this cannot override other services' limits.
- For reference, CircleCI's free monthly budget permits maximum average complete billed time/video of 10m at 10/day, 6m40s at 15/day, 5m at 20/day, and 3m20s at 30/day. Compare all CI jobs, not only final FFmpeg time.

## Remaining rollout actions

1. Follow [PRODUCTION_CUTOVER.md](PRODUCTION_CUTOVER.md) for exact Cloudflare/CircleCI/Cloudinary/Vercel settings. Keep both backend choices until explicit cutover.
2. Select the production branch and exact Vercel origin, redeploy, and retain only required CORS origins. Never expose admin/provider secrets in `VITE_*`.
3. Replace the previously shared/predictable admin token through the normal secrets/dashboard flow.
4. Deliberately transfer **video scheduler ownership** to Cloudflare. The first transfer verifies Render's shared ownership guard; once verified, future Cloudflare schedule edits do not depend on Render being alive. The guard has not been verified as transferred in this audit.
5. Owner verifies Instagram public publishing and connected-account quota; reconnect OAuth if callback/client settings differ. No live publishing proof is claimed here.
6. Confirm actual Cloudflare billing plan/CPU limits and Gemini project quotas in their consoles; monitor Workflow steps and Cloudinary usage daily.
7. Agree retention rules before enabling any deletion of source assets/final media. Do not delete the legacy Supabase Storage assets still referenced by existing jobs.
8. Keep review enabled until content quality, captions/voice and public publishing are approved. Model-generated factual content without external verification still needs human review.
9. Cloudflare supports one selected primary language per run; additional-language variants remain unsupported. Do not treat backend switching as complete feature parity for those variants.
