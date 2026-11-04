# Calendar hosted assistant beta

The hosted option lets an invited person use typed chat and push-to-talk with an owner-funded OpenAI project. Windows protects each device's assistant credential with DPAPI. Personal project keys, model preferences, the local calendar and encrypted sync credentials remain separate. Choose **Settings → Assistant → Hosted beta**, read the data notice, accept it, enter the private invitation, activate the PC and save. **Personal project key** remains the default for new and upgraded installations.

The Worker is [calendar-ai-beta](https://calendar-ai-beta.nottbradd.workers.dev/health). Its only D1 binding is `AI_DB`, connected to `calendar-ai-beta`. The deployment starts paused. Health reads the pause flag from D1. The public service has no owner endpoints and no access to the sync database.

## Enable sponsorship

1. In the OpenAI organization, create a dedicated project for the Calendar beta. Exclude this project from **Share inputs and outputs with OpenAI**. If sharing is enabled for all projects, explicitly change the selection so this project is excluded. Confirm the project's setting before activation. The beta assumes no promotional balance, expiry or complimentary coverage. [Project data controls](https://platform.openai.com/settings/organization/data-controls/sharing) · [API data retention](https://developers.openai.com/api/docs/guides/your-data)
2. Open the dedicated project's **Limits → Spend → Edit spend limit**. Set the monthly limit to US$10, enable **Enforce a hard limit** and save. Configure separate spend alerts below the cap, for example at US$5 and US$8, and verify the notification recipient. A project hard limit can take time to propagate. Check posted costs in the project's Usage dashboard. The Worker independently enforces its own daily, monthly and cumulative estimates. [Spend controls](https://developers.openai.com/api/docs/guides/spend-limits) · [Usage dashboard](https://platform.openai.com/usage)
3. Create a **project service account** in that project. Set its key permissions to **Restricted**. Grant only the write/create operations for **Responses** and **Audio transcriptions** and restrict model access to `gpt-6-luna`, `gpt-6.1-sol` and `gpt-transcribe` where available. Leave other endpoints disabled. Verify the project can use those three models. [API project settings](https://platform.openai.com/settings/organization/projects) · [API permissions](https://developers.openai.com/api/docs/guides/rbac)
4. From the repository root in PowerShell, use Node.js 22 or newer with npm and the existing Free account's browser-based Wrangler login. The migration and deploy commands also reproduce this deployment. When updating, pause first, check the target database ID and review the migration. The secret command prompts privately. Enter the dedicated credential there, outside shell history, issue trackers and chat.

~~~powershell
cd backend
npm ci
npx wrangler login
npx wrangler whoami
npx wrangler d1 migrations apply AI_DB --remote --config ai/wrangler.jsonc
npm run ai:deploy
node node_modules/wrangler/bin/wrangler.js secret put OPENAI_SERVICE_KEY --config ai/wrangler.jsonc
cd ..
.\.venv\Scripts\python.exe backend/ai/owner.py --target remote usage
.\.venv\Scripts\python.exe backend/ai/owner.py --target remote resume --private-project-verified
~~~

5. Before admitting participants, issue one private invitation for a fictional identity and run the bounded probe. It uses a temporary calendar, up to two routine Responses requests and one synthetic recording. Enter the invitation at the private prompt. Revoke the printed disposable **member ID** afterwards and pause again if trials are complete. The encrypted service key belongs only in the Worker secret, including during this check.

~~~powershell
.\.venv\Scripts\python.exe backend/ai/owner.py --target remote invite --new-person --minutes 10
.\.venv\Scripts\python.exe scripts/verify_hosted.py --live-private-project
.\.venv\Scripts\python.exe backend/ai/owner.py --target remote revoke-user MEMBER_ID
.\.venv\Scripts\python.exe backend/ai/owner.py --target remote pause
~~~

## Invitations and owner operations

Verify a recipient through a private channel before issuing a ticket. Keep a private roster mapping each approved person to their opaque member ID. D1 has no names or emails. Use `--new-person` once per person. For another PC, a reinstall or a lost credential, verify the same person and use `--member MEMBER_ID`. Every device under that ID shares the same limits across all calendars. Identity recovery depends on the owner's roster. Device revocation takes effect for new requests. A submitted upstream request can still finish.

Invitations contain 256 random bits, expire after ten minutes by default (configurable from one to sixty), and are consumed atomically. Only a hash is stored. The original device can retry an enrollment after a lost reply using its same protected credential. No other device can redeem that invitation. **Forget hosted access on this PC** removes the local credential. Use the owner tool to disable a lost device server-side. **Personal project key** switches billing back to the user's own key only when the user selects and saves it.

~~~powershell
.\.venv\Scripts\python.exe backend/ai/owner.py --target remote members
.\.venv\Scripts\python.exe backend/ai/owner.py --target remote invite --member MEMBER_ID
.\.venv\Scripts\python.exe backend/ai/owner.py --target remote devices MEMBER_ID
.\.venv\Scripts\python.exe backend/ai/owner.py --target remote revoke-device DEVICE_ID
.\.venv\Scripts\python.exe backend/ai/owner.py --target remote revoke-user MEMBER_ID
.\.venv\Scripts\python.exe backend/ai/owner.py --target remote limit day_cap 1
.\.venv\Scripts\python.exe backend/ai/owner.py --target remote usage
.\.venv\Scripts\python.exe backend/ai/metrics.py
~~~

`owner.py` uses the owner's Wrangler access and defaults to **local** D1 unless `--target remote` is supplied. `metrics.py` reads aggregate GraphQL analytics for the entire signed-in Cloudflare account and the AI Worker since midnight UTC. For a separate read-only credential, supply `CLOUDFLARE_ANALYTICS_TOKEN` with Account Analytics Read and `CLOUDFLARE_ACCOUNT_ID` through your private environment. To rotate the OpenAI credential, pause the beta, replace the Worker secret, revoke the old key in the project, verify the new configuration and resume.

## Ceilings and accounting

| Owner setting | Default |
| --- | --- |
| `seats` | 100 active identities |
| `turns` | 20 submitted chat turns per person per UTC day |
| `planning` | 1 planning turn per person per UTC day, included in the 20 |
| `rounds` | 4 upstream Responses requests per turn |
| `seconds` | 120 seconds of transcription per person per UTC day, maximum 45 seconds per clip |
| `day_cap`, `month_cap`, `lifetime_cap` | US$1 per UTC day, US$10 per UTC calendar month, US$80 across all time |

A shared ceiling can stop an individual below their allowance. Requests can be denied early when the remaining budget cannot cover a conservative reservation. Pausing blocks new model and audio requests while allowing status checks and owner-issued enrollment. No process purchases credit or raises these ceilings.

The Worker chooses the model from the current request, pins Standard processing, supplies the tool schemas and bounds the upstream UTF-8 request to 32 KiB plus at most 1,400 output tokens per response. Tool continuations must match the preceding response and device. Audio duration is calculated from bounded 16 kHz mono PCM; the server creates its own WAV header. The Worker accepts no client-supplied price, model name, provider URL, audio duration, system instructions or tools. Personal-mode model selection is independent.

Each upstream attempt reserves integer nano-US dollars (one billion per dollar) and its identity quota in a single SQLite statement, using D1 triggers in the dedicated database. Costs include every round, cache writes, reasoning within reported output, and transcription. Text reserves the highest configured input price for a token per serialized UTF-8 byte plus framing and prior-reasoning headroom, and the entire output cap. Audio reserves a full minute per clip. Reported usage reconciles the original day/month/lifetime buckets. Unknown cache detail uses the most expensive configured input rate. Missing usage or a timeout, cancellation, malformed upstream reply or lost connection can leave the full estimate against the cap. A metered overrun pauses new traffic. Rates are operator-maintained; review them before changing models or re-enabling a paused service. [Routine pricing](https://developers.openai.com/api/docs/models/gpt-6-luna) · [Planning pricing](https://developers.openai.com/api/docs/models/gpt-6.1-sol) · [Audio pricing](https://developers.openai.com/api/docs/models/gpt-transcribe)

Retrying the same request cannot submit it or count it twice. After an ambiguous reply, the app preserves the draft. A duplicate attempt reports that it was received. Starting a new turn deliberately can consume another turn. The service never replays stored replies. Request IDs must be fresh (15 minutes) and belong to the current UTC day. A long conversation may need a fresh turn at midnight. Accounting provides estimates; reconcile actual charges and any credit application against OpenAI's project records.

## Privacy and retention

Messages, limited recent conversation and results of selected local calendar queries pass through the Worker to OpenAI. Submitted audio follows the same route. The desktop owns calendar lookups, proposal review, stale-plan and duplicate-Apply checks, and transactional saves. An assistant invitation cannot join an encrypted calendar. The Worker holds no sync keys. The Responses API uses `store: false`. Provider-side abuse monitoring can still apply. Application request logging, Worker observability and preview URLs are disabled. No application logs or D1 columns contain messages, tool results, transcripts or audio. [OpenAI endpoint retention](https://developers.openai.com/api/docs/guides/your-data)

D1 keeps opaque member/device IDs, credential and invitation hashes, revocation state, request IDs, model, usage and bounded continuation hashes. Daily pruning runs at 03:17 UTC. Expired invitations are removed, consumed invitations are deleted at enrollment, and per-request records are removed once their UTC date is more than seven days old. Per-person/day counters and daily shared totals are retained for 35 days, and monthly totals for about 400 days. Lifetime spend survives pruning, including uncertain reservations. Enrolled identities and device revocations remain for the beta's lifetime to preserve entitlements and prevent credential reuse. Infrastructure backups and provider telemetry follow their respective retention policies.

## Verification and capacity

From `backend`, run `npm ci`, `npm test`, then `npm run ai:load`. On Windows the last command uses the repository's Python environment for the Qt integration check. The load harness runs a local workerd with disposable D1, a mock OpenAI service and outbound traffic blocked. To run the full desktop sync regressions, start the local sync Worker on port 18787 following the [sync trial instructions](../README.md), set `CALENDAR_LOCAL_UI_TRIAL=1` and run `python -m unittest discover -s tests -v` from the root.

On 4 November 2026 the local simulation enrolled **100 fictional people** and used bursts of **20**. It ran 100 two-round routine lookups, ten planning turns and five three-second clips. All **315 Worker requests** succeeded, including **215 mocked upstream requests**. D1 reported **18,891 rows read** and **3,269 rows written** for that workload, excluding fixture creation. Two complete runs took 2.34–3.76 s with p95 request latency of 177–276 ms. The Windows workerd processes used 1,329–1,828 ms of CPU, averaging 4.22–5.80 ms per Worker request, including the local D1 emulator. These averages include only local execution. The synthetic estimated spend was US$0.057125. No paid API was called. A separate Qt integration check enrolled another device to an existing identity, used a tool round, kept a pending review and applied it with the transport offline, edited a mock transcript and restored personal settings.

The owner confirmed Workers Free for this account. Its daily allowances are shared by AI, encrypted sync and other workloads: 100,000 Worker requests, five million D1 rows read and 100,000 D1 rows written. D1 storage is 5 GB across the account. The Free Worker CPU limit is 10 ms per invocation. This simulated burst corresponds to 0.315% of daily requests, 0.378% of reads and 3.269% of writes. The real account-wide GraphQL baseline from midnight to 06:55 UTC on 4 November was **14 Worker requests, zero reported errors, 80 D1 rows read and 33 written**, before AI deployment. Analytics are sampled and may arrive late. Re-run `metrics.py` during the beta to compare all applications. Neither the load run nor a health probe establishes capacity for 100 real users over a day. [Workers limits](https://developers.cloudflare.com/workers/platform/limits/) · [D1 pricing and limits](https://developers.cloudflare.com/d1/platform/pricing/) · [Worker analytics](https://developers.cloudflare.com/analytics/graphql-api/tutorials/querying-workers-metrics/) · [D1 analytics](https://developers.cloudflare.com/d1/observability/metrics-analytics/)

The deployed Worker passed D1 health, real desktop enrollment in a disposable profile, invitation reuse rejection, unauthenticated access rejection, paused chat and revocation. The fictional identity was revoked. D1 then reported zero upstream requests and zero estimated spend. The separate sync Worker also passed its health check. At 07:01 UTC the account-wide analytics reported 26 Worker requests, zero Worker errors, 103 rows read and 71 written across D1. Cloudflare reported 11 requests with zero Worker errors for the deployed beta through 07:06 UTC. CPU was 1.499 ms at p50 and 4.618 ms at p99. These 11 requests exercised paused health and access paths; paid inference and full audio handling were only measured locally. To measure a short window for the AI Worker alone, use `python backend/ai/metrics.py --beta-minutes 15`.

The paid hosted function-call and transcription probe needs the dedicated private project credential. The earlier personal-key fictional verification is documented separately. Live participant traffic, an actual microphone recording and encrypted sync between two physical PCs remain outstanding. [Personal-key verification](../../docs/assistant-api.md) · [Two-PC trial](../README.md#trial-on-two-windows-pcs)
