# Calender sync prototype

The development Worker and its D1 database are both named `calender-sync-dev`. The Worker accesses D1 through the `DB` binding. It uses the account's `workers.dev` address and services included in Workers Free. [Workers pricing](https://developers.cloudflare.com/workers/platform/pricing/) · [D1 pricing](https://developers.cloudflare.com/d1/platform/pricing/)

`GET /health` queries D1 on every request. It returns HTTP 200 with `{"status":"ok"}` when the query succeeds, or HTTP 503 with `{"status":"unavailable"}` if D1 is unavailable. The endpoint accepts no writes. The desktop app continues to store its calendar in local SQLite. [Live development health check](https://calender-sync-dev.nottbradd.workers.dev/health)

## Run locally

From this directory, use Node.js 22 or newer with npm.

```sh
npm ci
npm test
npm run dev
```

Open [the local health endpoint](http://localhost:8787/health). Wrangler emulates D1 locally and keeps its state in the ignored `.wrangler/` directory.

## Deploy and verify

Sign in through Wrangler's browser flow and confirm the intended account uses Workers Free in **Workers & Pages**. Wrangler stores its credentials in your user profile.

```sh
npx wrangler login --scopes account:read user:read workers:write workers_scripts:write d1:write
npx wrangler whoami
npx wrangler d1 info calender-sync-dev
npm run deploy
```

Open `/health` on the URL Wrangler prints. To test D1 directly, run the same disposable query against local and remote D1. Its sample row exists only for the duration of the query.

```sh
npx wrangler d1 execute DB --local --command "WITH sample(marker) AS (VALUES ('calender-dev-probe')) SELECT marker FROM sample"
npx wrangler d1 execute DB --remote --command "WITH sample(marker) AS (VALUES ('calender-dev-probe')) SELECT marker FROM sample"
```

## Before syncing calendar entries

- Pair devices using a short-lived, single-use invitation. Authenticate each device and support revocation.
- Generate and protect keys on-device. Establish end-to-end encryption between paired devices, including a recovery and key-rotation policy.
- Sync encrypted changes using stable entry IDs, revisions, deletion records and a durable local queue. Test offline edits, retries, concurrent changes and recovery across two devices.
