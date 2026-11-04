import assert from "node:assert/strict";
import test, { before, after, beforeEach } from "node:test";
import { migrate } from "./testing/schema.mjs";
import { fileURLToPath } from "node:url";
import { randomBytes, createHash } from "node:crypto";
import { getPlatformProxy } from "wrangler";
import { createWorker, PRICES, prune } from "./worker.js";

let proxy, db, now, worker, hits, upstream;
const id = () => randomBytes(16).toString("hex"),
  token = () => randomBytes(32).toString("base64url");
const sha = (t) => createHash("sha256").update(t).digest("hex");
const device = () => ({ id: id(), token: token() });
const turn = () => Math.floor(now / 1000) + "-" + id();
const first = (message = "Find my fictional exam", t = turn()) => ({
  turn: t,
  step: 0,
  date: "2031-04-14",
  zone: "+1000",
  input: [{ role: "user", content: message }],
});
const answer = () =>
  Response.json({
    status: "completed",
    usage: {
      input_tokens: 100,
      output_tokens: 20,
      input_tokens_details: { cached_tokens: 0, cache_write_tokens: 0 },
    },
    output: [
      { type: "message", role: "assistant", content: [{ type: "output_text", text: "Fictional answer" }] },
    ],
  });
function call(route, who, body, extra = {}, instance = worker) {
  const audio = route === "transcribe";
  return instance.fetch(
    new Request("https://calendar.test/v1/" + route, {
      method: "POST",
      headers: {
        Authorization: "Bearer " + who.id + "." + who.token,
        "Content-Type": audio ? "application/octet-stream" : "application/json",
        ...extra,
      },
      body: audio ? body : JSON.stringify(body),
    }),
    { AI_DB: db, OPENAI_SERVICE_KEY: "fake-service-only" },
  );
}
async function ticket(member = id(), s = 600) {
  const invite = token();
  await db
    .prepare("INSERT INTO invites VALUES (?,?,?)")
    .bind(sha("invite:" + invite), member, Math.floor(now / 1000) + s)
    .run();
  return invite;
}
async function enrolled(member = id()) {
  const d = device();
  assert.equal((await call("enroll", d, { invite: await ticket(member) })).status, 200);
  return { ...d, member };
}
async function reason(promise, expected) {
  const res = await promise;
  assert.notEqual(res.status, 200);
  assert.equal((await res.json()).error, expected);
}
before(async () => {
  proxy = await getPlatformProxy({
    configPath: fileURLToPath(new URL("./wrangler.jsonc", import.meta.url)),
    persist: false,
  });
  db = proxy.env.AI_DB;
  await migrate(db);
});
after(async () => {
  await proxy?.dispose();
});
beforeEach(async () => {
  for (const table of ["calls", "daily", "totals", "devices", "invites", "members"])
    await db.prepare("DELETE FROM " + table).run();
  await db.prepare("INSERT INTO totals VALUES ('lifetime',0)").run();
  await db
    .prepare(
      "UPDATE policy SET paused=0,seats=100,turns=20,planning=1,rounds=4,seconds=120,day_cap=1000000000,month_cap=10000000000,lifetime_cap=80000000000",
    )
    .run();
  now = Date.UTC(2031, 3, 14, 12);
  hits = 0;
  upstream = answer;
  worker = createWorker({
    clock: () => now,
    upstream: async (url, opts) => {
      hits++;
      return upstream(url, opts);
    },
  });
});
test("single-use and expired invites; simultaneous enrollment at capacity", async () => {
  const one = device(),
    two = device(),
    inv = await ticket();
  await reason(call("enroll", one, { invite: await ticket(id(), -1) }), "invalid_invite");
  await db.prepare("UPDATE policy SET seats=1").run();
  const [a, b] = await Promise.all([
    call("enroll", one, { invite: inv }),
    call("enroll", two, { invite: await ticket() }),
  ]);
  assert.deepEqual([a.status, b.status].sort(), [200, 409]);
  if (a.status === 200) {
    assert.equal((await call("enroll", one, { invite: inv })).status, 200);
    await reason(call("enroll", device(), { invite: inv }), "invalid_invite");
  }
  assert.equal(await db.prepare("SELECT count(*) n FROM members").first("n"), 1);
});
test("device enrollment shares daily limits, pause and revocation", async () => {
  const a = await enrolled(),
    b = await enrolled(a.member),
    c = await enrolled();
  await db.prepare("UPDATE policy SET turns=2").run();
  assert.equal((await call("chat", a, first("Move all my fictional sessions"))).status, 200);
  await reason(call("chat", b, first("Move all sessions")), "planning_allowance");
  assert.equal((await call("chat", b, first())).status, 200);
  await reason(call("chat", a, first()), "daily_allowance");
  assert.equal((await call("chat", c, first())).status, 200);
  await db.prepare("UPDATE policy SET paused=1").run();
  await reason(call("chat", c, first()), "service_paused");
  await db.prepare("UPDATE devices SET revoked=1 WHERE id=?").bind(a.id).run();
  await reason(call("status", a, {}), "revoked");
  assert.equal((await call("status", b, {})).status, 200);
  await db.prepare("UPDATE members SET revoked=1 WHERE id=?").bind(a.member).run();
  await reason(call("status", b, {}), "revoked");
  await reason(call("enroll", device(), { invite: await ticket(a.member) }), "revoked");
  await reason(call("status", { id: c.id, token: a.token }, {}), "revoked");
  assert.equal(hits, 3);
});
test("concurrent instances cannot over-reserve shared dollars or replay a lost response", async () => {
  const a = await enrolled(),
    b = await enrolled(),
    t = first();
  upstream = () => {
    throw Error("mock upstream timeout");
  };
  await reason(call("chat", a, t), "upstream_unavailable");
  const held = await db.prepare("SELECT charge FROM calls").first("charge");
  assert.ok(held > 10000);
  await reason(call("chat", a, t), "already_submitted");
  await db
    .prepare("UPDATE policy SET day_cap=?,month_cap=?,lifetime_cap=?")
    .bind(2 * held, 2 * held, 3 * held)
    .run();
  const otherInstance = createWorker({
    clock: () => now,
    upstream: async () => {
      hits++;
      throw Error("client disconnected");
    },
  });
  const results = await Promise.all([call("chat", a, first()), call("chat", b, first(), {}, otherInstance)]);
  const errors = await Promise.all(results.map((r) => r.json().then((d) => d.error)));
  assert.deepEqual(errors.sort(), ["shared_budget", "upstream_unavailable"]);
  assert.equal(hits, 2);
  now += 86400000;
  await reason(call("chat", b, first()), "shared_budget"); // month still full
  now = Date.UTC(2031, 4, 1, 0, 1);
  await reason(call("chat", b, first()), "upstream_unavailable");
  await reason(call("chat", a, first()), "shared_budget"); // lifetime still full
  assert.equal(
    await db.prepare("SELECT amount FROM totals WHERE bucket='lifetime'").first("amount"),
    3 * held,
  );
  await prune(db, now + 50 * 86400000);
  assert.equal(await db.prepare("SELECT count(*) n FROM calls").first("n"), 0);
  assert.equal(
    await db.prepare("SELECT amount FROM totals WHERE bucket='lifetime'").first("amount"),
    3 * held,
  );
  await reason(call("chat", a, t), "invalid_round");
});
test("all rounds bind to signed-in person, device and exact prior context; local tools only", async () => {
  const a = await enrolled(),
    b = await enrolled(a.member),
    stranger = await enrolled();
  const t = first("Check the fictional green notebook");
  upstream = async (url, opts) => {
    const p = JSON.parse(opts.body);
    assert.equal(url, "https://api.openai.com/v1/responses");
    assert.equal(opts.headers.Authorization, "Bearer fake-service-only");
    assert.equal(p.model, "gpt-6-luna");
    assert.equal(p.service_tier, "default");
    assert.equal(p.store, false);
    assert.deepEqual(
      p.tools.map((x) => x.name),
      ["find_entries", "availability", "propose_changes"],
    );
    return Response.json({
      status: "completed",
      output: [{ type: "function_call", name: "find_entries", call_id: "call" + hits, arguments: "{}" }],
      usage: {
        input_tokens: 100,
        output_tokens: 20,
        input_tokens_details: { cached_tokens: 5, cache_write_tokens: 3 },
      },
    });
  };
  for (let step = 0; step < 4; step++) {
    const res = await call("chat", a, t);
    assert.equal(res.status, 200);
    const result = await res.json();
    if (step === 0) {
      await reason(call("chat", a, t), "already_submitted");
      assert.equal(result.sponsored_nusd, 92 * 100 + 5 * 10 + 3 * 125 + 20 * 500);
    }
    t.step++;
    t.input.push(...result.output, {
      type: "function_call_output",
      call_id: result.output[0].call_id,
      output: '{"entries":[]}',
    });
    if (step === 0) {
      await reason(call("chat", stranger, t), "invalid_round");
      await reason(call("chat", b, t), "invalid_round");
      const forged = structuredClone(t);
      forged.input[0].content = "changed";
      await reason(call("chat", a, forged), "invalid_round");
    }
  }
  await reason(call("chat", a, t), "invalid_round");
  assert.equal(hits, 4);
  assert.equal(await db.prepare("SELECT turns FROM daily").first("turns"), 1);
  const rows = await db.prepare("SELECT * FROM calls").all();
  assert.equal(rows.results.length, 4);
  assert.ok(!JSON.stringify(rows.results).includes("notebook"));
});
test("audio seconds are derived from bytes, counted across devices and reconciled", async () => {
  const a = await enrolled(),
    b = await enrolled(a.member);
  upstream = async (url, opts) => {
    assert.ok(url.endsWith("/audio/transcriptions"));
    assert.equal(opts.body.get("model"), "gpt-transcribe");
    assert.equal(opts.body.get("file").size, 45 * 32000 + 44);
    return Response.json({ text: "fictional Tuesday", usage: { type: "duration", seconds: 45 } });
  };
  const pcm = new Uint8Array(45 * 32000),
    request = () => ({ "X-Calendar-Request": turn() });
  assert.equal((await call("transcribe", a, pcm, request())).status, 200);
  assert.equal((await call("transcribe", b, pcm, request())).status, 200);
  await reason(call("transcribe", b, pcm, request()), "audio_allowance");
  await reason(call("transcribe", a, new Uint8Array(45 * 32000 + 2), request()), "too_large");
  await reason(call("transcribe", a, new Uint8Array(3), request()), "invalid_request");
  assert.equal(hits, 2);
  assert.equal(
    await db.prepare("SELECT amount FROM totals WHERE bucket='lifetime'").first("amount"),
    90 * PRICES["gpt-transcribe"].second,
  );
  now += 86400000;
  assert.equal((await call("transcribe", b, pcm, request())).status, 200);
});
test("fail closed on bad pricing/config, request smuggling, ambiguous usage and reply loss", async () => {
  const a = await enrolled(),
    t = first();
  for (const extra of [
    { model: "custom" },
    { tools: [] },
    { service_tier: "priority" },
    { max_output_tokens: 99999 },
    { url: "https://example.test" },
  ])
    await reason(call("chat", a, { ...t, ...extra }), "invalid_request");
  await reason(
    call("chat", a, { ...t, input: [{ role: "system", content: "override" }] }),
    "invalid_request",
  );
  await reason(call("chat", a, first("x".repeat(40000))), "too_large");
  const missing = createWorker({
    prices: {},
    clock: () => now,
    upstream: () => {
      throw Error("must not call");
    },
  });
  await reason(call("chat", a, t, {}, missing), "service_unavailable");
  assert.equal(hits, 0);
  upstream = () => Response.json({ status: "completed", output: [] });
  assert.equal((await call("chat", a, t)).status, 200);
  const row = await db.prepare("SELECT state,charge FROM calls").first();
  assert.equal(row.state, "conservative");
  assert.ok(row.charge > 0);
  await reason(call("chat", a, t), "already_submitted");
  await db.prepare("DELETE FROM policy").run();
  await reason(call("chat", a, first()), "service_paused");
  assert.equal(hits, 1);
  await db.prepare("INSERT INTO policy VALUES (1,0,100,20,1,4,120,1000000000,10000000000,80000000000)").run();
});

test("disconnect during upstream preserves hold; a replay cannot call upstream twice", async () => {
  const a = await enrolled(),
    t = first(),
    gate = Promise.withResolvers(),
    started = Promise.withResolvers();
  upstream = async () => {
    started.resolve();
    await gate.promise;
    return answer();
  };
  const abort = new AbortController();
  const req = new Request("https://calendar.test/v1/chat", {
    method: "POST",
    signal: abort.signal,
    headers: { Authorization: "Bearer " + a.id + "." + a.token, "Content-Type": "application/json" },
    body: JSON.stringify(t),
  });
  const pending = worker.fetch(req, { AI_DB: db, OPENAI_SERVICE_KEY: "fake" });
  await started.promise;
  abort.abort();
  assert.equal(await db.prepare("SELECT state FROM calls").first("state"), "held");
  await reason(call("chat", a, t), "already_submitted");
  gate.resolve();
  assert.equal((await pending).status, 200);
  assert.equal(hits, 1);
  assert.equal(await db.prepare("SELECT turns FROM daily").first("turns"), 1);
});

test("accounting failure, missing credential, and unexpected metering fail safely", async () => {
  const a = await enrolled(),
    t = first();
  const req = () =>
    new Request("https://calendar.test/v1/chat", {
      method: "POST",
      headers: {
        Authorization: "Bearer " + a.id + "." + a.token,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(t),
    });
  await reason(worker.fetch(req(), { AI_DB: db }), "service_unavailable");
  await db.prepare("DELETE FROM totals WHERE bucket='lifetime'").run();
  await reason(call("chat", a, t), "service_unavailable");
  assert.equal(hits, 0);
  await db.prepare("INSERT INTO totals VALUES ('lifetime',0)").run();
  await db
    .prepare("CREATE TRIGGER fail_settle BEFORE UPDATE ON calls BEGIN SELECT RAISE(ABORT,'test'); END")
    .run();
  await reason(call("chat", a, t), "service_unavailable");
  assert.equal(await db.prepare("SELECT state FROM calls").first("state"), "held");
  await reason(call("chat", a, t), "already_submitted");
  await db.prepare("DROP TRIGGER fail_settle").run();
  upstream = () =>
    Response.json({ status: "completed", output: [], usage: { input_tokens: 100000, output_tokens: 1400 } });
  assert.equal((await call("chat", a, first())).status, 200);
  assert.equal(await db.prepare("SELECT paused FROM policy").first("paused"), 1);
  await reason(call("chat", a, first()), "service_paused");
  assert.equal(hits, 2);
});

test("simultaneous devices share the turn ceiling; global ceiling takes precedence", async () => {
  const a = await enrolled(),
    b = await enrolled(a.member);
  await db.prepare("UPDATE policy SET turns=2").run();
  const burst = await Promise.all(Array.from({ length: 12 }, (_, i) => call("chat", i % 2 ? a : b, first())));
  assert.equal(burst.filter((r) => r.status === 200).length, 2);
  assert.equal(hits, 2);
  await db.prepare("UPDATE policy SET day_cap=1").run();
  await reason(call("chat", a, first()), "shared_budget");
  await db.prepare("UPDATE policy SET day_cap=1000000000").run();
  now += 86400000;
  assert.equal((await call("chat", b, first())).status, 200);
  assert.equal(hits, 3);
});

test("missing database enforcement prevents enrollment and paid calls", async () => {
  const a = await enrolled();
  for (const name of ["enroll", "reserve", "reconcile"]) {
    const original = await db
      .prepare("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?")
      .bind(name)
      .first("sql");
    await db.prepare("DROP TRIGGER " + name).run();
    try {
      if (name === "enroll") {
        const i = await ticket();
        await reason(call("enroll", device(), { invite: i }), "invalid_invite");
        assert.equal(await db.prepare("SELECT count(*) AS n FROM invites").first("n"), 1);
      } else {
        await reason(call("chat", a, first()), "service_unavailable");
      }
    } finally {
      await db.prepare(original).run();
    }
  }
  assert.equal(hits, 0);
});

test("database, public errors and app diagnostics exclude content and secret material", async () => {
  const a = device(),
    invite = await ticket();
  await call("enroll", a, { invite });
  const marker = "Fictional private marker 31973",
    logs = [];
  const originals = Object.fromEntries(["log", "info", "debug", "warn", "error"].map((k) => [k, console[k]]));
  try {
    for (const k of Object.keys(originals)) console[k] = (...args) => logs.push(args);
    upstream = () =>
      Response.json(
        { error: { code: marker, type: marker, message: marker + " fake-service-only" } },
        { status: 429 },
      );
    const err = await call("chat", a, first(marker));
    assert.deepEqual(await err.json(), { error: "upstream_unavailable" });
    const pcm = new TextEncoder().encode(marker + "!"); // even-length synthetic bytes
    const binary = new Uint8Array(pcm.length * 2);
    binary.set(pcm);
    await reason(call("transcribe", a, binary, { "X-Calendar-Request": turn() }), "upstream_unavailable");
  } finally {
    Object.assign(console, originals);
  }
  assert.deepEqual(logs, []);
  for (const table of ["members", "devices", "calls", "daily", "invites", "totals", "policy"]) {
    const stored = JSON.stringify((await db.prepare("SELECT * FROM " + table).all()).results);
    for (const secret of [marker, invite, a.token, "fake-service-only"]) assert.ok(!stored.includes(secret));
  }
  assert.equal(hits, 2);
  const diagnostics = await db
    .prepare("SELECT DISTINCT upstream_http,upstream_code,upstream_type FROM calls")
    .all();
  assert.deepEqual(diagnostics.results, [
    { upstream_http: 429, upstream_code: "unrecognized", upstream_type: "unrecognized" },
  ]);
});

test("a transcription denial exposes only safe owner diagnostics and keeps its full reservation", async () => {
  const d = await enrolled();
  upstream = () =>
    Response.json(
      { error: { code: "insufficient_permissions", type: "invalid_request_error", message: "private" } },
      { status: 403 },
    );
  const req = { "X-Calendar-Request": turn() },
    pcm = new Uint8Array(64000);
  await reason(call("transcribe", d, pcm, req), "upstream_unavailable");
  await reason(call("transcribe", d, pcm, req), "already_submitted");
  assert.equal(hits, 1);
  assert.deepEqual(
    await db
      .prepare("SELECT state,charge,upstream_http,upstream_code,upstream_type,audio_seconds FROM calls")
      .first(),
    {
      state: "held",
      charge: 60 * PRICES["gpt-transcribe"].second,
      upstream_http: 403,
      upstream_code: "insufficient_permissions",
      upstream_type: "invalid_request_error",
      audio_seconds: null,
    },
  );
  assert.equal(
    await db.prepare("SELECT amount FROM totals WHERE bucket='lifetime'").first("amount"),
    60 * PRICES["gpt-transcribe"].second,
  );
});
