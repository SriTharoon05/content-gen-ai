# Instagram news posts

Open the Cloudflare dashboard's **Instagram news** tab. Select a connected Instagram channel, configure the brand/categories, and generate a post. Review the source, caption and slides before approving and separately publishing. No YouTube delivery is used. Scheduling defaults to off.

## Safeguards

- Feed fetches have a database-enforced daily budget; cached articles are reused.
- Exact URLs/titles, normalized outlet suffixes and conservative near-title matching exclude previously used news across channels, including historical news rows. Admission is serialized to prevent concurrent duplicate posts.
- New news also reserves its entity, angle, concept and embedding in the existing video content ledger before image generation. Entity naming and semantic similarity are probabilistic: this is not a guarantee against every paraphrase. Older news posts were not retrospectively embedded; their title/source guards still apply.
- Copy gets at most three attempts, with concrete layout repair feedback. Highlight formatting is repaired without changing factual prose. Provider exhaustion cools down before another key sweep.
- Each slide has its own scene prompt and purchased image (1–3 per post). Assets are checkpointed per slide; layout edits reuse those individual assets. Exact hashes and a conservative thumbnail comparison reject duplicates within the carousel. This does not guarantee global visual uniqueness or catch every crop/semantic resemblance. Ambiguous image purchases are not automatically repeated.
- New layouts/captions have no application-added image-generation badge. This does not disable any platform-required disclosure or make an illustration documentary evidence.
- A public Instagram upload remains a separate explicit action. Generation-to-preview tests do not validate public publishing.

## Deployment

1. Keep `news_api` or `NEWSDATA_API_KEY` only in the ignored backend `.env`; run the existing `cloudflare/scripts/sync_news_secret.py --apply` utility to set the Worker secret.
2. From `backend`, run `venv/Scripts/python.exe ../cloudflare/scripts/install_rpc.py --apply` (use the equivalent venv executable on Linux).
3. Push the Python renderer to the CircleCI checkout branch configured by `CIRCLECI_BRANCH`.
4. From `cloudflare`, run `npm run check`, `npm test`, then `npm run deploy`.
5. Rebuild/deploy the frontend normally. No news key belongs in Vite variables.

Free-feed delivery is delayed, so posts must not be presented as a live breaking-news service. Image, CircleCI and storage usage have independent quotas/costs. Increasing the post count does not guarantee sufficient fresh, unique source material.
