# Calendar 1.5.0 credit-funded beta

This is an invitation-only beta for **five active people, including the owner**. It uses the existing dedicated Calendar project's restricted service credential and the observed subscription credit window. The provider's saved monthly cap is **inactive** and alerts are **unavailable** (`paid_plan_required`). The separate `credit-beta` command does not claim those safeguards passed. The original normal activation requirements are unchanged. [Owner operations and policy](../backend/ai/README.md)

## Install and activate your PC

Use the **1.5.0 prototype**, which already contains Hosted beta settings. The public stable offline build does not contain this assistant. Close Calendar before running `dist/release/Calendar-1.5.0-Setup.exe`, or run `dist/release/Calendar-1.5.0-Portable.exe`. Both use the existing local Calendar profile; no data migration or desktop rebuild is needed for this server update. The checksums in `dist/release/Calendar-1.5.0-SHA256SUMS.txt` are:

~~~text
bd44ad7cdee0c87980cd75749220498c5917a8523a476ea1b40c7af922baf72c  Calendar-1.5.0-Setup.exe
eda6f6fac5686542e7e218ea397839a5b43fb6d0f6685d091373ff07db5fd9aa  Calendar-1.5.0-Portable.exe
~~~

When you are ready, open PowerShell in the repository root and issue **one invitation for yourself**:

~~~powershell
.\.venv\Scripts\python.exe backend/ai/owner.py --target remote invite --new-person --minutes 10
~~~

Keep the printed member ID in your private owner roster. Copy the private `CAI1.` invitation to the app; do not put it in chat, a commit or a public issue. The invitation expires in ten minutes.

1. Open **Settings → Assistant → Hosted beta**.
2. Read and accept the data notice. Paste the invitation and choose **Activate this PC**.
3. Choose **Save settings**, then open **Assistant** from the toolbar. Personal-key settings remain saved separately.
4. With fictional entries, ask for an exam date and check the answer. Ask for a small change, inspect its review, and choose **Apply** only if correct. Drafting a proposal alone must not change the calendar.
5. For voice, hold **Talk** and release, or click **Talk → Stop**. The transcript stays editable in the composer; correct it before **Send**. Actual microphone hardware still needs this owner trial. These live interactions consume the shared budget.

To enrol a second PC as **the same person**, issue another invitation with the saved ID, not `--new-person`:

~~~powershell
.\.venv\Scripts\python.exe backend/ai/owner.py --target remote invite --member MEMBER_ID --minutes 10
~~~

Repeat activation on the second PC. Its device credential is distinct, while both PCs share one seat and the same daily chat, planning and audio allowance. Calendar data is still local; assistant enrollment does not pair encrypted sync. Use the separate [two-PC sync guide](../backend/README.md#trial-on-two-windows-pcs) for that.

No invitations are created automatically. For another approved person, use `--new-person` only after privately verifying them. Keep the initial total at five, including the owner. Revoke an identity with `revoke-user MEMBER_ID`, or one device with `revoke-device DEVICE_ID`. The sixth active identity cannot enrol, even when invitations are redeemed concurrently.

## Enforced beta policy

| Setting | Credit-funded beta |
| --- | --- |
| Active people | At most 5, including the owner |
| Shared estimated spend | US$0.25 per UTC day; US$1 per UTC month; US$1 cumulatively |
| Prior accounting included | US$0.0066933, comprising US$0.0062095 held and US$0.0004838 measured estimate |
| Chat per person | 20 turns per UTC day, including at most 1 complex-planning turn |
| Responses per turn | At most 4 |
| Audio per person | 120 seconds per UTC day, at most 45 seconds per clip |
| Models | `gpt-6-luna`, `gpt-6.1-sol`, `gpt-transcribe`; selected and restricted by the Worker |
| Grant cutoff | **26 November 2026 at 00:00 UTC**; no new sponsored inference at or after that time |

Reservations compete atomically against the shared ceilings and retain uncertain costs. A request can be refused before a displayed allowance is exhausted when its conservative reservation will not fit. The entire ledger remains retained in credit mode, including after expiry. No accounting is reset by activation, restart, pause or renewal. A request submitted before the cutoff may still finish.

Use these commands to inspect or stop the service:

~~~powershell
.\.venv\Scripts\python.exe backend/ai/owner.py --target remote usage
.\.venv\Scripts\python.exe backend/ai/owner.py --target remote pause
~~~

To resume within the reviewed window, or after independently checking a replacement grant, use the explicit [credit activation procedure](../backend/ai/README.md#enable-the-limited-credit-funded-beta). An expired window stays closed until that owner update. No replacement grant is assumed. The service key's **4 December 2026** expiry does not extend the grant.

## Verification

On **5 November 2026 at 05:50 UTC**, migration `0003_credit_beta.sql` was applied to the existing `calendar-ai-beta` D1 database while paused. The updated [Worker](https://calendar-ai-beta.nottbradd.workers.dev/health) was deployed as **`2fb06fc7-1c6f-4484-85a2-89dbf212b0cd`**, receiving 100% of traffic. Health still reported paused before the owner ran `credit-beta --grant-expires 2026-11-26 --grant-reviewed`. The subsequent read at **05:51 UTC** confirmed `funding=credit`, the exact policy above and HTTP 200 health with `{"status":"ok","paused":false}`.

Before and after deployment, SHA-256 comparisons of the complete serialized rows in `calls`, `daily`, `totals`, `members`, `devices` and `invites` matched. The five ledger rows remain **two held / US$0.0062095** and **three measured / US$0.0004838**. Monthly and lifetime accounting both remain **US$0.0066933**; the original 4 November daily bucket is also unchanged. There was no 5 November inference. There are **zero active identities, zero outstanding invitations and zero owner-probe locks**. No new paid synthetic probe or enrollment was performed. Aggregate-only local receipts are retained under ignored `artifacts/credit-beta-before.json` and `artifacts/credit-beta-after.json`.

Offline verification passed **78 Python desktop/owner tests**, including the backend-dependent sync UI and restart regressions, and **23 Worker tests**, including production transport and redirect rejection. Five-person tests cover shared device quotas, seat races, spending ceilings, expiry, revocation, missing enforcement, retained holds and interrupted activation. The exact owner command was also exercised against disposable local D1 and read back in a separate process.

The local five-person simulation completed **25 Worker requests and 20 mocked upstream requests**; the desktop integration then activated a second device, performed a tool lookup, reviewed and applied a local change, edited a mock transcript and restored personal settings. The original 100-person simulation also passed: **315 Worker requests and 215 mocked upstream requests**. Outbound provider calls were intercepted in these simulations.

The earlier [live owner probe](hosted-owner-probe.md) passed **`gpt-6-luna` routine lookup and `gpt-transcribe` synthetic transcription**. It reported 1,602 text tokens and 4 audio seconds, with a combined Standard list-price estimate of **US$0.0004838**. **It did not live-test `gpt-6.1-sol` complex planning.** Posted charges and confirmed credit application remain unverified; the beta does not display an assumed balance or promise free usage.

Actual microphone input, live participant use and the physical two-PC encrypted sync trial remain outstanding. Billing, credentials, input/output sharing and the Cloudflare Free plan are unchanged. The provider cap and alerts remain unavailable; Worker accounting is a conservative estimate, not provider billing enforcement.
