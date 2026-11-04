# Calendar 1.5.0: owner-only hosted verification, 4 November 2026

**The live lookup and transcription both stopped in the Worker before reaching OpenAI.** The transport bug was reproduced and fixed. The corrected Worker was deployed paused; its text and audio paths were tested offline, not retried live. The beta is closed to participants.

## What ran

The starting desktop release was 1.5.0 (`3501f27`). At 11:26:11 UTC the dedicated [AI Worker](https://calendar-ai-beta.nottbradd.workers.dev/health) was paused with zero active identities, outstanding invitations, ledger attempts, usage and reservations. The existing restricted `OPENAI_SERVICE_KEY` binding was confirmed by name only. No personal calendar, personal API key or microphone was used.

The owner command snapshotted D1, then staged one seat, one turn, two routine Responses calls, zero planning, five seconds of audio and US$0.05 estimated limits for the day, month and lifetime. It checked maximum reservation amounts of US$0.005308 + US$0.005515 + US$0.004500 = **US$0.015323**, including a full minute of price headroom for the single clip. All three buckets had enough room. A single fictional device redeemed the one invitation while paused. At 11:26:21 UTC the temporary policy was active with one identity and no outstanding invitations.

| Endpoint | Live Worker result | OpenAI result and reported usage | Reservation retained |
| --- | --- | --- | ---: |
| `/v1/responses` · `gpt-6-luna` | HTTP 503 on the first fictional exam lookup; no continuation | No provider status, error code or token counts | US$0.0017095 |
| `/v1/audio/transcriptions` · `gpt-transcribe` | HTTP 503 on the independent 3.165-second synthetic clip | No provider status, error code or audio usage | US$0.0045000 |

The deployed Worker version for this trial was `abfa1528-11dc-4191-83e6-38f73911203c`. The live owner record captured the two Worker HTTP statuses; the safe Worker code for both failures, `upstream_unavailable`, was reproduced against that same code in workerd. It is **not** an OpenAI error code. The Worker used `redirect: "error"`, which this runtime rejects when constructing the request. An offline outbound interceptor received zero calls with that option. [Cloudflare Request semantics](https://developers.cloudflare.com/workers/runtime-apis/request/#constructor)

## Accounting and cleanup

Both D1 rows remained `held`. There are no metered charges in the Worker estimate. **US$0.0062095** (6,209,500 nano-USD) counts against each of the daily, monthly and lifetime ceilings; these are the same two reservations, not three separate costs. The Worker counted one chat turn and rounded the submitted clip up to four seconds of its own audio allowance. OpenAI did not report four seconds or any tokens. Posted charges, application of subscription credit and complimentary-token coverage were not verified.

By 11:26:27 UTC the Worker had paused, revoked the device's identity, verified **HTTP 403 `revoked`** using the same bearer, and restored the original policy while paused. The restored limits were 100 seats, 20 turns, one planning turn, four rounds, 120 audio seconds and US$1/day, US$10/month, US$80/lifetime. D1 showed zero active identities, zero outstanding invitations and no remaining probe lock. All ledger deltas matched the two holds. Nothing was refunded, deleted or reset. No further live invitation, chat or audio request was issued.

## Fix and remaining verification

The corrected Worker (`4cc4e411-9fc8-4878-8eeb-bbb77d717165`) uses manual redirect handling and rejects 3xx responses without forwarding the service key. A regression test runs the unmodified production entry point in workerd with all outbound hosts intercepted. It verifies a successful routine response, a separately recorded transcription HTTP 403 with an allowlisted provider code, and a redirect that is never followed. The owner rehearsal also tested the fictional date lookup and synthetic transcript, preserved earlier accounting, and verified revocation and policy restoration. These are **offline** results; the restricted service account's actual model and transcription permissions remain unverified.

All **72 desktop and owner tests**, including the backend-dependent UI test, and **19 Worker tests** passed. The local 100-person simulation completed 315 Worker requests and 215 mocked upstream requests; the hosted desktop integration check also passed.

The provider project has input/output sharing disabled. Its saved US$10 monthly cap read back `inactive` twice; the alert endpoint returned HTTP 400 `paid_plan_required`. The observed grant expires **26 November 2026**. The service credential expires **4 December 2026** and cannot extend the grant. No credits were purchased, paid billing or auto-recharge enabled, admin key recreated, sharing changed or credential permissions expanded. The normal participant activation requirements still apply. [Owner guide](../backend/ai/README.md) · [OpenAI subscription credit terms](https://developers.openai.com/api/docs/guides/chatgpt-api-credits)

A successful live function-call round trip and live transcription still require a separately authorized owner trial. Real microphone input and encrypted sync between two physical PCs remain outstanding.
