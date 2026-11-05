# Calendar 1.5.0 owner-only hosted verification, 4 November 2026

**Both live checks passed on the corrected Worker.** The restricted service credential completed a two-request exam lookup and transcribed one synthetic clip. Cleanup left the Worker paused with participant access closed. This is the historical probe record; the subsequent [five-person credit-funded beta](credit-beta.md) has its own activation procedure, grant deadline and verification record.

## Corrected deployment at 11:52 UTC

Cloudflare routed 100% of traffic to `4cc4e411-9fc8-4878-8eeb-bbb77d717165`, the deployment with manual redirect handling recorded in `a5b8bd8`. Public health reported paused. At 11:52:18 UTC the owner preflight found zero active identities, outstanding invitations and probe locks. D1 held US$0.0062095 from the earlier trial. Adding the maximum new reservations of US$0.015323 brought the upper bound to US$0.0215325, within each temporary US$0.05 ceiling.

The owner command ran once. One fictional device redeemed its invitation while paused. At 11:52:27 UTC it held the only active seat, with one routine turn, at most two Responses requests, zero planning turns and five seconds of audio allowance.

| Request | Worker HTTP | OpenAI HTTP | OpenAI-reported usage | Worker estimated cost |
| --- | ---: | ---: | --- | ---: |
| `gpt-6-luna` requested the calendar lookup | 200 | 200 | 691 input + 49 output tokens | US$0.0000936 |
| `gpt-6-luna` returned the stored exam date, `2040-03-12` | 200 | 200 | 852 input + 10 output tokens | US$0.0000902 |
| `gpt-transcribe` returned “Tuesday” in the transcript of the 3.165-second synthetic clip | 200 | 200 | 4 audio seconds | US$0.0003000 |

The lookup used **1,602 tokens** across the two requests, comprising 1,543 input and 59 output. The independent audio check passed on its first submission. The Worker and provider error-code fields were empty for all three requests.

**Ledger.** All three new reservations settled to `measured`, for a combined usage-based estimate of **US$0.0004838** and **US$0 newly held**. The two prior rows remained identical, with **US$0.0062095 still held**. D1 therefore counts **US$0.0066933** against each of the daily, monthly and lifetime ceilings. Posted OpenAI charges and application of subscription credit remain unverified.

**Cleanup.** The Worker paused and revoked the disposable identity. Its same bearer received HTTP 403 `revoked` before the original policy was restored while paused. Cleanup completed by 11:52:43 UTC. An independent D1 read at 11:53:08 UTC confirmed the full original policy, five ledger entries, zero active identities, zero outstanding invitations and zero probe locks. The public health check also reported paused.

## Earlier probe at 11:26 UTC

The starting desktop release was 1.5.0 (`3501f27`). At 11:26:11 UTC the dedicated [AI Worker](https://calendar-ai-beta.nottbradd.workers.dev/health) was paused with zero active identities, outstanding invitations, ledger attempts, usage and reservations. The existing restricted `OPENAI_SERVICE_KEY` binding was confirmed by name only. No personal calendar, personal API key or microphone was used.

The owner command snapshotted D1, then staged one seat, one turn, two routine Responses calls, zero planning, five seconds of audio and US$0.05 estimated limits for the day, month and lifetime. It checked maximum reservation amounts of US$0.005308 + US$0.005515 + US$0.004500 = **US$0.015323**, including a full minute of price headroom for the single clip. All three buckets had enough room. A single fictional device redeemed the one invitation while paused. At 11:26:21 UTC the temporary policy was active with one identity and no outstanding invitations.

| Endpoint | Live Worker result | OpenAI result and reported usage | Reservation retained |
| --- | --- | --- | ---: |
| `/v1/responses` · `gpt-6-luna` | HTTP 503 on the first fictional exam lookup; no continuation | No provider status, error code or token counts | US$0.0017095 |
| `/v1/audio/transcriptions` · `gpt-transcribe` | HTTP 503 on the independent 3.165-second synthetic clip | No provider status, error code or audio usage | US$0.0045000 |

The deployed Worker version for this trial was `abfa1528-11dc-4191-83e6-38f73911203c`. The live owner record captured the two Worker HTTP statuses; the safe Worker code for both failures, `upstream_unavailable`, was reproduced against that same code in workerd. It is **not** an OpenAI error code. The Worker used `redirect: "error"`, which this runtime rejects when constructing the request. An offline outbound interceptor received zero calls with that option. [Cloudflare Request semantics](https://developers.cloudflare.com/workers/runtime-apis/request/#constructor)

### Earlier accounting and cleanup

Both D1 rows remained `held`. The first run contributed **US$0.0062095** (6,209,500 nano-USD) of reservations to each of the daily, monthly and lifetime ceilings. The Worker counted one chat turn and rounded the submitted clip up to four seconds of its own audio allowance. OpenAI returned no usage for either attempt.

By 11:26:27 UTC the Worker had paused, revoked the device's identity, verified **HTTP 403 `revoked`** using the same bearer, and restored the original policy while paused. The restored limits were 100 seats, 20 turns, one planning turn, four rounds, 120 audio seconds and US$1/day, US$10/month, US$80/lifetime. D1 showed zero active identities, zero outstanding invitations and no remaining probe lock. All ledger deltas matched the two holds.

## Transport fix and project safeguards

The corrected Worker uses manual redirect handling and rejects 3xx responses without forwarding the service key. A regression test runs the unmodified production entry point in workerd with all outbound hosts intercepted. It verifies a successful routine response, a separately recorded transcription HTTP 403 with an allowlisted provider code, and a redirect that is never followed. The offline owner rehearsal also tested the fictional date lookup and synthetic transcript, preserved earlier accounting, and verified revocation and policy restoration.

All **72 desktop and owner tests**, including the backend-dependent UI test, and **19 Worker tests** passed. The local 100-person simulation completed 315 Worker requests and 215 mocked upstream requests; the hosted desktop integration check also passed.

The provider project has input/output sharing disabled. Its saved US$10 monthly cap read back `inactive` twice; the alert endpoint returned HTTP 400 `paid_plan_required`. The observed grant expires **26 November 2026**. The service credential expires **4 December 2026** and cannot extend the grant. No credits were purchased, paid billing or auto-recharge enabled, admin key recreated, sharing changed or credential permissions expanded. The normal participant activation requirements still apply; the later limited credit mode does not claim those provider safeguards passed. [Owner guide](../backend/ai/README.md) · [OpenAI subscription credit terms](https://developers.openai.com/api/docs/guides/chatgpt-api-credits)

This successful live probe covered **`gpt-6-luna` and `gpt-transcribe`**, not **`gpt-6.1-sol` complex planning**. Real microphone input and encrypted sync between two physical PCs remain outstanding.
