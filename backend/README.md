# Calender encrypted sync prototype

The Windows desktop app is usable offline. Sync is opt-in under **Settings → Sync**. The development Worker `calender-sync-dev` uses the D1 database of the same name through the `DB` binding on Workers Free. [Live health check](https://calender-sync-dev.nottbradd.workers.dev/health)

## Trial on two Windows PCs

1. Use the same 1.3.0 prototype build on both PCs. You can build it from the repository with the [Windows build instructions](../README.md#verify-and-build). For a disposable trial, run the following in PowerShell on **each** PC. Use a new folder name for each trial and substitute the path to your prototype executable.

   ~~~powershell
   $env:LOCALAPPDATA = Join-Path $env:TEMP 'Calender-Trial-01'
   & 'C:\path\to\Calender.exe'
   ~~~

2. On PC A open **Settings → Sync → Create calendar**. Save the private recovery key offline and confirm that you saved it. Choose **Invite device**, select the invitation and copy it. Share it privately with PC B. It expires in ten minutes. Issuing another invitation replaces the previous one.
3. On PC B open **Settings → Sync → Join calendar** and paste the invitation. Read the 12-character verification code shown on B to the person at A over a trusted channel. On A select **Sync now → Approve request** and enter B’s code. Select **Sync now** on B. Both PCs should list two devices.
4. Add a sample assessment on A. Select **Sync now** on A and then B. Disconnect B from the network, edit the same assessment independently on both PCs, reconnect and sync both. **Review conflicts** lets you compare the versions and explicitly choose one. Sync again to send the resolution. Repeat with a deletion or subject-colour change.
5. On A select B in **Paired devices** and choose **Revoke device**. A rotates the key. B’s next check should report access denied. On a replacement PC choose **Recover** and enter the offline key. If every prior PC was lost, select the option to revoke all of them and rotate the key during recovery.

Linking imports the entries and visible subjects already on each PC. Entries keep their original IDs. Different IDs survive as separate entries, identical versions of one ID coalesce, and divergent versions are offered for resolution. Subjects are matched by their case-folded name. Month/Week preferences stay local. Removing a subject hides its label while retaining its entries. Explicitly adding the same subject again restores its label.

**Disconnect** forgets the local credentials and queue and retains the visible calendar. Resolve conflicts before disconnecting. Use **Revoke device** on a different paired PC to remove that credential from the server. Back up a closed database before trying the prototype on an existing calendar. Schema 3 is readable by this prototype. For a second PC, start with its own database and join. Copying a linked profile can duplicate its device identity.

![Sync settings with two disposable PCs and a conflict](../docs/sync-settings.png)

![Choosing between independent edits to a sample assessment](../docs/sync-conflict.png)

## Authentication, encryption and recovery

- Each installation creates a UUIDv4 device ID, 256-bit bearer and X25519 keypair. Every feed, upload and management request authenticates against the requested calendar over HTTPS. D1 stores bearer hashes. Calendar creation, invitation claims and recovery have indexed, one-minute attempt budgets per source and per calendar. The creation limit is 8 per minute per source, and the claim and recovery limits are 24. A pending device can request approval but cannot read or write changes. Active devices have equal authority to approve or revoke another device.
- Invitations contain a 256-bit random ticket and a commitment to the calendar’s first key. D1 stores the ticket hash, expiry and at most one claimant. Approval verifies a fingerprint of the claimant’s device ID and public key. Membership changes use conditional updates on the directory generation. Concurrent approvals, recovery and revocation must refresh the directory before retrying.
- The desktop serialises the entry or subject, including identifiers, fields, causal parents and deletion state, and encrypts it with PyNaCl’s XChaCha20-Poly1305 AEAD. A fresh 192-bit nonce is generated for each encryption. Associated data binds the ciphertext to the calendar, operation ID and key epoch. X25519 sealed boxes carry the keyring to approved devices. Clients check the original key commitment, continuity with previously held keys and keyed authentication tags on recipients before rotating or sharing keys. [PyNaCl AEAD](https://pynacl.readthedocs.io/en/latest/secret/) · [libsodium sealed boxes](https://doc.libsodium.org/public-key_cryptography/sealed_boxes)
- Device bearers, private keys and cached keyrings are sealed with current-user Windows DPAPI in the local SQLite profile. The desktop’s ordinary calendar and causal history are readable to that Windows user. Worker management credentials are used solely by Wrangler. The service sees random IDs, membership, public keys, epochs, ciphertext sizes and access timing. Application logs exclude request bodies and credentials, and persistent Worker observability is disabled. [Windows DPAPI](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata)
- The offline recovery key contains the calendar ID and a random 256-bit seed. Separate HMAC-SHA-256 domain labels derive a recovery authenticator, an X25519 recovery private key and the first content key. D1 stores a hash of the authenticator, the recovery public key and an encrypted keyring. A replacement PC validates the key commitment, decrypts the keyring locally and registers its new credential. A pending recovery is DPAPI-protected and cleared on activation. An active profile does not keep the recovery seed. Anyone with the offline key can recover access. If all devices and the key are lost, the hosted ciphertext is unrecoverable.
- Revoking a member atomically removes its access, cancels pending invitations and increments the epoch. A fresh random key and historical keys are sealed for surviving devices and the recovery public key. A surviving offline PC fetches its new envelope before uploading. Old-epoch uploads are rejected. Rotation protects future changes. It cannot erase old downloads, retrieve previously disclosed keys, or invalidate an independently retained recovery copy.

## Reliability and limits

A local edit, its stable operation ID and its parent versions are recorded in one SQLite transaction. Applying a downloaded page and advancing its cursor also share a transaction. The server indexes the feed by calendar and sequence, deduplicates by calendar and operation ID and checks membership generation inside the data transaction. A lost response can be retried. Downloaded operations do not enter the upload queue.

The current versions of an object are the operations that no known child supersedes. Concurrent versions with different values remain available until a resolution references all current versions. A concurrent deletion wins the ordinary calendar display while keeping the competing edit in the conflict screen. Tombstones and causal history are retained, including when operations arrive out of order.

An idle PC makes one incremental request roughly every 90–100 seconds. Local edits are debounced, linking polls about every 12–15 seconds, and transient errors back off to about 15 minutes. **Sync now** makes an immediate check. The UI counts unacknowledged changes and records the last successful server contact. Uploads are acknowledged by stable operation ID. Transfers use pages of 24 changes and bounded request bodies. An individual change above roughly 512 KiB of serialised plaintext stays local until shortened. Other changes can still upload. The prototype supports 12 normal paired devices plus a recovery slot. History compaction, recovery-key replacement, and an independent security review remain future work.

## Run, deploy and verify

Use Node.js 22+ with npm in this directory. The desktop tests and trial harness use the repository's Windows Python environment.

~~~sh
npm ci
npm test
npx wrangler d1 migrations apply DB --local
npm run dev -- --port 18787
~~~

Run the local trial from the repository root in another PowerShell session. It creates disposable Windows profiles and exercises pairing, offline edits, retries, conflict resolution, revocation, rotation and recovery. The audit inspects stored values and removes only calendars generated by that trial.

~~~powershell
$env:CALENDER_LOCAL_UI_TRIAL = '1'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts/verify_sync.py --audit
~~~

Wrangler uses browser OAuth and keeps its session in the developer’s user profile. Apply the migration to the development DB before deploying the matching Worker.

~~~sh
npx wrangler login --scopes account:read user:read workers:write workers_scripts:write d1:write
npx wrangler whoami
npx wrangler d1 migrations apply DB --remote
npm run deploy
~~~

For the deployed trial, run the log check in one terminal and the following trial in another. The log check filters on the disposable trial header and inspects events in memory. The optional packaged check starts two executable instances with isolated local profiles.

~~~powershell
.\.venv\Scripts\python.exe scripts/check_sync_trace.py
# In a second terminal at the repository root
.\.venv\Scripts\python.exe scripts/verify_sync.py --base https://calender-sync-dev.nottbradd.workers.dev --audit --packaged .\dist\installer-runtime\Calender\Calender.exe
~~~

## Verification on 2 November 2026

The 28 desktop tests and 6 Worker tests passed, including real Qt pairing controls, schema migration, transaction rollback, fingerprint and roster checks, invitation throttling and byte-bounded pagination. The full workflow ran against local Wrangler and the deployed Worker using disposable profiles on one Windows PC. A trial on two physical machines is still outstanding.

The deployed harness made 93 requests, sent 14,749 bytes of JSON and received 59,103 bytes of successful JSON responses. The audited feed contained 18 distinct encrypted operations. An idle check took one request. These figures exclude HTTP headers, Wrangler management calls and the separate packaged processes. D1 and matching live push/pull traces were checked for the sample plaintext and credentials. The remote trial calendars and their changes were removed after verification.

Two packaged instances exchanged an entry using isolated profiles. Both the installer runtime and portable executable were exercised. The Windows installer also passed install, launch, reinstall, uninstall, shortcut and database-preservation checks.
