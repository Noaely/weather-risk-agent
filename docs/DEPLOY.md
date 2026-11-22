# Deploying to Render

One Render web service runs everything: the FastAPI backend also serves the built React UI, so there is
one URL and no CORS. It is built from the repo's `Dockerfile` and configured by `render.yaml`.

## What runs where

| Part | Where | Cost |
|---|---|---|
| API + UI (one Docker service) | Render web service | Free while testing (sleeps after 15 min idle, ~30–60 s to wake); Starter while reviewers have the link (check the price on render.com/pricing) |
| Claude API | Anthropic | ~$0.01 per answer; capped at $1/day by the app |
| Data | `data/weather.db` (committed snapshot, 2016–2025) inside the image | — |

## Secrets

Set only in Render's dashboard, never in the repo:

| Variable | Value |
|---|---|
| `ANTHROPIC_API_KEY` | your Anthropic API key |
| `ACCESS_CODE` | the shared code for reviewers, at least 8 characters (12+ recommended); create it in a password manager |

`render.yaml` declares both with `sync: false`, so Render asks for them when the Blueprint is created.
All other settings (`APP_ENV=production`, `ANALYSIS_LAST_YEAR=2025`, model, rate limits, spend cap) are in
`render.yaml`. With `APP_ENV=production` the app refuses to start without `ACCESS_CODE`.

## Steps

1. Render dashboard → **New → Blueprint** → connect GitHub and choose this repository.
2. Render reads `render.yaml` and asks for `ANTHROPIC_API_KEY` and `ACCESS_CODE`. Enter them, then
   **Apply**. The first build takes a few minutes (it builds the UI with Node, then the Python image).
3. When the service shows **Live**, test it (free checks first, then one paid question):
   ```bash
   python scripts/smoke_test.py --url https://<service>.onrender.com --code <ACCESS_CODE> --free
   python scripts/smoke_test.py --url https://<service>.onrender.com --code <ACCESS_CODE>
   ```
   Note what it reports for `rate limiter keys on`: `true-client-ip` means each visitor has their own
   limit; `socket` means all visitors share one (safe, but stricter).
4. Before sending the link: service → **Settings → Instance type → Starter** (no sleeping), re-run the
   smoke test, then send the URL and the code separately.
5. Before a live demo: 2–3 minutes ahead, wake the service and warm the prompt cache (~$0.06):
   ```bash
   python scripts/ask.py --url https://<service>.onrender.com --code <ACCESS_CODE> --examples --quiet
   ```

## Limits (environment variables, defaults in `render.yaml`)

| Variable | Default | Meaning |
|---|---|---|
| `RATE_PER_IP_HOUR` | 40 | questions per visitor IP per rolling hour |
| `RATE_GLOBAL_HOUR` | 60 | questions across all visitors per rolling hour |
| `FAILED_CODES_PER_IP_HOUR` | 10 | wrong codes before an IP is blocked for an hour |
| `DAILY_SPEND_CAP_USD` | 1.00 | Claude API spend per UTC day before `/chat` stops answering |

Limits and sessions live in memory (one instance): a restart or redeploy resets them and ends open
conversations.
