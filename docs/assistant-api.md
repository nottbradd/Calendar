# Assistant privacy, usage and billing

**Settings → Assistant** offers two providers. **Hosted beta** uses the owner's dedicated service account and requires an invitation and explicit consent. Selected text, calendar details and submitted audio pass through the Calendar AI Worker to OpenAI. The beta requires input/output sharing disabled and enforces shared spending ceilings. The desktop applies reviewed calendar changes locally. [Hosted privacy, activation and metering](../backend/ai/README.md)

In **Personal project key**, enter an OpenAI API key from the project you intend to bill. Choose the project associated with your credit grant or complimentary usage offer. An organization may have several projects. Use the project selector in the OpenAI dashboard to check which one owns the key. [OpenAI project keys](https://platform.openai.com/api-keys) · [Credit grants](https://platform.openai.com/settings/organization/billing/credit-grants)

**Auto** uses `gpt-6-luna` for routine requests and `gpt-6.1-sol` for complex scheduling. You can select a model explicitly, including `gpt-5.4`. Voice input sends a recording to `gpt-transcribe`. Review the transcript in the composer before sending it to the text model. Model access depends on your project.

## Information sent to OpenAI

A typed request sends your message, bounded conversation context and the results of any calendar queries the assistant needs. Stopping a voice recording submits the audio for transcription. Sharing an encrypted calendar between PCs and sending selected details to the assistant are separate operations. When you use the assistant, OpenAI processes those selected details as readable text or audio.

Calendar keeps its conversation in the current local session and sets `store: false` on Responses calls. This disables storage of the retrievable response object. OpenAI’s ordinary abuse-monitoring policy can retain Responses content for up to 30 days, with exceptions for safety and legal obligations. Other controls can apply to an approved organization or project. OpenAI lists no default abuse-log or application-state retention for the file transcription endpoint. [OpenAI API data controls and endpoint retention](https://developers.openai.com/api/docs/guides/your-data)

API data is excluded from model training by default. An organization owner can opt in to sharing inputs and outputs for selected projects. Shared material may include messages, calendar tool results and generated replies, and OpenAI can use it to improve its models. Before using a real calendar, check that sharing these details, including information about other people, is permitted. Calendar uses the configuration already selected for the API project. [Project data-sharing controls](https://platform.openai.com/settings/organization/data-controls/sharing)

## Complimentary usage and promotional credit

For an organization offered complimentary tokens in exchange for sharing, the selected project must participate in **input and output sharing** for eligible traffic to count. The offer shown in the account determines the included models, limits and other conditions. Check the daily allowance and over-limit terms there. A promotional credit grant has its own availability, expiry and application to charges. A tool-using conversation can make several billable model requests, and voice transcription uses a separate endpoint. Check coverage for each before relying on an allowance. [Sharing and eligibility](https://platform.openai.com/settings/organization/data-controls/sharing) · [Billing overview](https://platform.openai.com/settings/organization/billing/overview)

**Usage** means counts the API returned for a request, such as input and output tokens. **Estimated cost** means an approximation using public USD list prices. Cached input, cache writes, processing tier and actual account terms can change the result. An approximate token count before sending is also an estimate. Calendar cannot infer a credit balance or an actual charge from those numbers.

Standard text prices checked on 4 November 2026, in USD per million tokens, and the per-minute voice price are available in the [Luna](https://developers.openai.com/api/docs/models/gpt-6-luna), [Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol), [GPT-5.4](https://developers.openai.com/api/docs/models/gpt-5.4) and [transcription](https://developers.openai.com/api/docs/models/gpt-transcribe) model references.

## Verify a project with fictional samples

Both live checks passed in personal-key mode with fictional data on 4 November 2026.

The [owner-only hosted verification](hosted-owner-probe.md) used a separate restricted service credential. Its results and D1 reservations are recorded separately from the personal-key figures below. Participant access remains closed.

- **Function calling.** `gpt-6-luna` requested the fictional exam lookup and returned the date supplied by the tool. The API reported **344 tokens across two requests** (315 input and 29 output).
- **Transcription.** `gpt-transcribe` correctly transcribed “Tuesday” from the 3.16-second synthetic recording. The API reported **4 seconds** of audio usage.

The **estimated combined cost at Standard list prices was US$0.00035**, rounded. Posted charges, credit application and complimentary-token coverage remain unverified.

In separate mocked HTTP tests, each billing failure left the typed draft editable and the calendar unchanged after one request. A transcription credit error also preserved the draft. Actual microphone testing and the [physical two-PC sync trial](../backend/README.md#trial-on-two-windows-pcs) are still outstanding.

From the repository root on Windows, use the project’s virtual environment. The first command generates a short WAV locally using the Windows speech synthesizer, validates it and deletes the temporary file. The second explicitly sends up to two Responses requests and one transcription request using the configured project key. The only sample lookup concerns a fictional violet exam. Neither command reads your calendar or uses your microphone.

~~~powershell
.\.venv\Scripts\python.exe scripts/verify_assistant_api.py --check-audio
.\.venv\Scripts\python.exe scripts/verify_assistant_api.py --run
~~~

The live probe prints UTC times, available request IDs, and usage as returned by the API. It verifies the model’s function arguments, supplies the fictional result, checks the resulting answer and checks that the transcription contains the spoken day. Each invocation can incur charges. To test a specific model, add `--model gpt-5.4` or `--model gpt-6.1-sol`.

To reconcile actual billing, use an otherwise idle test project where practical. Record its posted costs before running the probe, select the same project and UTC date afterwards, and check the posted line items once usage has settled. Review credit grants separately. A zero or missing amount during reporting delay cannot establish that any particular request was free. [Usage and costs dashboard](https://platform.openai.com/usage)

An organization owner who already uses the Costs API can also set `OPENAI_ADMIN_KEY` in a separate local shell and query that project before and after the trial. The desktop application only needs a project key. The example reads one UTC day of project-level totals. Other traffic on that project can contribute to the same totals. OpenAI’s Costs API reports aggregated line items rather than individual request receipts. [Costs API](https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/costs)

~~~powershell
.\.venv\Scripts\python.exe scripts/verify_assistant_api.py --costs-on 2026-11-04 --project-id proj_YOUR_PROJECT
~~~
