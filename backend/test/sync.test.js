import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { after, before, test } from "node:test";
import { randomBytes, createHash } from "node:crypto";
import { getPlatformProxy } from "wrangler";
import worker from "../src/index.js";

const id = () => randomBytes(16).toString("hex");
const key = () => randomBytes(32).toString("base64url");
const envelope = () => randomBytes(96).toString("base64url");
const hash = s => createHash("sha256").update(s).digest("hex");
let proxy;
before(async () => {
  proxy = await getPlatformProxy({ persist: false });
  const schema = await readFile(new URL("../migrations/0001_encrypted_sync.sql", import.meta.url), "utf8");
  for (const statement of schema.split(";").filter(s => s.trim())) await proxy.env.DB.prepare(statement).run();
});
after(async () => { await proxy?.dispose(); });

function call(calendar, action, who, data, address = "test") {
  return worker.fetch(new Request("https://worker.test/v1/" + calendar + "/" + action, {
    method: "POST", headers: { Authorization: "Bearer " + who, "Content-Type": "application/json", "CF-Connecting-IP": address },
    body: JSON.stringify(data),
  }), proxy.env);
}
function setup() {
  return { pub: key(), box: envelope(), recoveryPub: key(), recoveryBox: envelope(), recoveryHash: hash(key()),
    tag: hash(key()), recoveryTag: hash(key()) };
}
test("scoped bearer, authenticated feed, idempotent pushes and compare-and-swap membership", async () => {
  const cal = id(), who = id() + "." + key(), other = id() + "." + key();
  assert.equal((await call(cal, "create", who, setup())).status, 200);
  assert.equal((await call(cal, "pull", other, { after: 0 })).status, 403);
  assert.equal((await call(id(), "pull", who, { after: 0 })).status, 403);
  const change = { op: id(), box: envelope() };
  for (let i = 0; i < 2; i++) assert.deepEqual(await (await call(cal, "push", who, { epoch: 1, changes: [change] })).json(), { accepted: [change.op] });
  const feed = await (await call(cal, "pull", who, { after: 0 })).json();
  assert.equal(feed.changes.length, 1);
  assert.equal((await (await call(cal, "pull", who, { after: feed.changes[0].seq })).json()).changes.length, 0);
  const ticket = key();
  const invite = { generation: 1, seconds: 600, hash: hash(ticket) };
  assert.equal((await call(cal, "invite", who, invite)).status, 200);
  assert.equal((await call(cal, "invite", who, invite)).status, 409);
  assert.equal((await call(cal, "claim", other, { pub: key(), ticket })).status, 200);
  assert.equal((await call(cal, "pull", other, { after: 0 })).status, 403);
  assert.equal((await call(cal, "claim", id() + "." + key(), { pub: key(), ticket })).status, 403);
});
test("repeated invitation guesses are limited even for nonexistent calendars", async () => {
  const cal = id(), who = id() + "." + key();
  // Allow for crossing one wall-clock minute boundary during the test.
  for (let i = 0; i < 50; i++) {
    const response = await call(cal, "claim", who, { pub: key(), ticket: key() }, "guess-test");
    if (response.status === 429) {
      assert.equal(response.headers.get("Retry-After"), "60");
      return;
    }
    assert.equal(response.status, 403);
  }
  assert.fail("The attempt budget did not reject repeated guesses");
});
test("large encrypted changes page by bytes as well as row count", async () => {
  const cal = id(), who = id() + "." + key();
  assert.equal((await call(cal, "create", who, setup(), "page-test")).status, 200);
  for (let n = 0; n < 2; n++) {
    const changes = [{ op: id(), box: "A".repeat(500000) }];
    assert.equal((await call(cal, "push", who, { epoch: 1, changes })).status, 200);
  }
  const first = await (await call(cal, "pull", who, { after: 0 })).json();
  assert.equal(first.changes.length, 1);
  assert.equal(first.more, true);
  const last = await (await call(cal, "pull", who, { after: first.changes[0].seq })).json();
  assert.equal(last.changes.length, 1);
  assert.equal(last.more, false);
});
