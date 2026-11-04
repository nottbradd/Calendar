// Run from backend/. Uses an isolated local workerd, disposable D1 and mock OpenAI.
import { Miniflare, convertV4MiniflareOptions } from "miniflare";
import { build } from "esbuild";
import { randomBytes, createHash } from "node:crypto";
import { execFileSync, spawn } from "node:child_process";
import { migrate } from "./schema.mjs";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";
import { performance } from "node:perf_hooks";
import { once } from "node:events";

const path = (name) => fileURLToPath(new URL(name, import.meta.url));
const compiled = await build({
  entryPoints: [path("./local-worker.js")],
  bundle: true,
  format: "esm",
  platform: "browser",
  write: false,
});
const mf = new Miniflare(
  convertV4MiniflareOptions({
    modules: true,
    script: compiled.outputFiles[0].text,
    d1Databases: ["AI_DB"],
    host: "127.0.0.1",
    port: 0,
    compatibilityDate: "2026-11-01",
    outboundService: () => new Response("disabled", { status: 403 }),
  }),
);
const id = () => randomBytes(16).toString("hex");
const token = () => randomBytes(32).toString("base64url");
const tid = () => Math.floor(Date.now() / 1000) + "-" + id();
const measures = { requests: 0, upstream: 0, reads: 0, writes: 0 },
  statuses = {};
const percentile = (samples, p) =>
  samples.toSorted((a, b) => a - b)[Math.min(samples.length - 1, Math.floor(samples.length * p))];
const latencies = [];
function cpuMillis() {
  if (process.platform !== "win32") return null;
  // Cumulative CPU of this test's child workerd processes (includes the local D1 emulator).
  const command =
    "$all=Get-CimInstance Win32_Process; $owners=@(" +
    process.pid +
    "); do { $children=@($all | Where-Object { $_.ParentProcessId -in $owners -and $_.ProcessId -notin $owners } | ForEach-Object ProcessId); $owners+= $children } while ($children.Count); $ms=0; foreach($worker in $all){ if($worker.ProcessId -in $owners -and $worker.Name -eq 'workerd.exe'){ $ms+=(Get-Process -Id $worker.ProcessId).TotalProcessorTime.TotalMilliseconds } }; [Console]::Write([long]$ms)";
  return Number(
    execFileSync("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", command], {
      windowsHide: true,
      encoding: "utf8",
    }),
  );
}
async function call(d, route, body, headers = {}) {
  const start = performance.now(),
    audio = route === "transcribe";
  const res = await mf.dispatchFetch("http://local.test/v1/" + route, {
    method: "POST",
    headers: {
      Authorization: "Bearer " + d.id + "." + d.token,
      "Content-Type": audio ? "application/octet-stream" : "application/json",
      ...headers,
    },
    body: audio ? body : JSON.stringify(body),
  });
  const out = await res.json(),
    stats = JSON.parse(res.headers.get("x-local-measurements"));
  measures.requests++;
  for (const k of ["reads", "writes", "upstream"]) measures[k] += stats[k];
  statuses[res.status] = (statuses[res.status] ?? 0) + 1;
  latencies.push(performance.now() - start);
  assert.equal(res.status, 200, out.error);
  return out;
}
async function waves(items, width, fn) {
  for (let i = 0; i < items.length; i += width) await Promise.all(items.slice(i, i + width).map(fn));
}
async function run() {
  const addr = await mf.ready;
  assert.equal(addr.hostname, "127.0.0.1");
  const db = await mf.getD1Database("AI_DB");
  await migrate(db);
  await db.prepare("UPDATE policy SET paused=0").run();
  const people = Array.from({ length: 100 }, () => ({
    id: id(),
    token: token(),
    invite: token(),
    member: id(),
  }));
  for (const d of people)
    await db
      .prepare("INSERT INTO invites VALUES (?,?,?)")
      .bind(
        createHash("sha256")
          .update("invite:" + d.invite)
          .digest("hex"),
        d.member,
        Math.floor(Date.now() / 1000) + 600,
      )
      .run();
  const startCPU = cpuMillis(),
    start = performance.now();
  await waves(people, 20, (d) => call(d, "enroll", { invite: d.invite }));
  await waves(people, 20, async (d) => {
    const input = [{ role: "user", content: "Find my fictional exam" }],
      turn = tid();
    const f = { turn, step: 0, date: "2031-04-14", zone: "+1000", input };
    const a = await call(d, "chat", f);
    assert.equal(a.output[0].name, "find_entries");
    input.push(...a.output, {
      type: "function_call_output",
      call_id: "sample-call",
      output: '{"entries":[]}',
    });
    const b = await call(d, "chat", { ...f, step: 1 });
    assert.equal(b.output[0].type, "message");
  });
  await waves(people.slice(0, 10), 5, (d) =>
    call(d, "chat", {
      turn: tid(),
      step: 0,
      date: "2031-04-14",
      zone: "+1000",
      input: [{ role: "user", content: "Add a fictional session but keep my appointment fixed." }],
    }),
  );
  await waves(people.slice(0, 5), 5, (d) =>
    call(d, "transcribe", new Uint8Array(32000 * 3), { "X-Calendar-Request": tid() }),
  );
  const elapsed = performance.now() - start,
    endCPU = cpuMillis();
  assert.equal(await db.prepare("SELECT count(*) n FROM members").first("n"), 100);
  assert.equal(measures.upstream, 215);
  const cpu = startCPU === null ? null : endCPU - startCPU;
  const total = await db.prepare("SELECT amount FROM totals WHERE bucket='lifetime'").first("amount");
  console.log(
    JSON.stringify({
      people: 100,
      max_concurrency: 20,
      ...measures,
      statuses,
      workerd_process_cpu_ms: cpu,
      workerd_mean_cpu_ms: cpu === null ? null : cpu / measures.requests,
      elapsed_ms: Math.round(elapsed),
      latency_p95_ms: Math.round(percentile(latencies, 0.95)),
      estimated_mock_nano_usd: total,
    }),
  );
  if (process.argv.includes("--desktop")) {
    const invite = token();
    await db
      .prepare("INSERT INTO invites VALUES (?,?,?)")
      .bind(
        createHash("sha256")
          .update("invite:" + invite)
          .digest("hex"),
        people[0].member,
        Math.floor(Date.now() / 1000) + 600,
      )
      .run();
    const py = path("../../../.venv/Scripts/python.exe");
    const p = spawn(py, [path("../../../scripts/verify_hosted.py"), "--mock-local", addr.origin], {
      stdio: ["pipe", "inherit", "inherit"],
      windowsHide: true,
      env: { ...process.env, PYTHONUTF8: "1", QT_QPA_PLATFORM: "offscreen" },
    });
    p.stdin.end("CAI1." + invite + "\n");
    assert.equal((await once(p, "exit"))[0], 0);
  }
}
try {
  await run();
} finally {
  await mf.dispose();
}
