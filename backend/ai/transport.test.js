// Bundle the deployment entry point unchanged. workerd's real fetch constructs the
// outbound request; Miniflare intercepts every destination in-process, including redirects.
import assert from "node:assert/strict";
import test from "node:test";
import { createHash, randomBytes } from "node:crypto";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { Miniflare, convertV4MiniflareOptions } from "miniflare";
import { migrate } from "./testing/schema.mjs";

test("production transport reaches the offline provider, records denials and never follows a redirect", async () => {
  const bundle = await build({
    entryPoints: [fileURLToPath(new URL("./worker.js", import.meta.url))],
    bundle: true,
    format: "esm",
    write: false,
    platform: "browser",
  });
  const urls = [];
  const mf = new Miniflare(
    convertV4MiniflareOptions({
      modules: true,
      script: bundle.outputFiles[0].text,
      compatibilityDate: "2026-11-01",
      d1Databases: ["AI_DB"],
      bindings: { OPENAI_SERVICE_KEY: "fictional-test-only" },
      async outboundService(request) {
        urls.push(request.url);
        assert.equal(new URL(request.url).hostname, "api.openai.com");
        assert.equal(request.headers.get("Authorization"), "Bearer fictional-test-only");
        if (request.url.endsWith("/audio/transcriptions")) {
          assert.equal((await request.formData()).get("model"), "gpt-transcribe");
          return Response.json(
            { error: { code: "insufficient_permissions", type: "invalid_request_error" } },
            { status: 403 },
          );
        }
        assert.equal((await request.json()).model, "gpt-6-luna");
        if (urls.length === 3)
          return new Response(null, { status: 307, headers: { Location: "https://never-follow.invalid/" } });
        return Response.json({
          status: "completed",
          output: [],
          usage: { input_tokens: 19, output_tokens: 1 },
        });
      },
    }),
  );
  try {
    const db = await mf.getD1Database("AI_DB");
    await migrate(db);
    const id = () => randomBytes(16).toString("hex"),
      token = () => randomBytes(32).toString("base64url");
    const invite = token(),
      bearer = id() + "." + token(),
      member = id(),
      now = Math.floor(Date.now() / 1000);
    await db
      .prepare("INSERT INTO invites VALUES(?,?,?)")
      .bind(
        createHash("sha256")
          .update("invite:" + invite)
          .digest("hex"),
        member,
        now + 60,
      )
      .run();
    const post = async (route, body, extra = {}) => {
      const audio = route === "transcribe";
      const res = await mf.dispatchFetch("https://calendar.test/v1/" + route, {
        method: "POST",
        headers: {
          Authorization: "Bearer " + bearer,
          "Content-Type": audio ? "application/octet-stream" : "application/json",
          ...extra,
        },
        body: audio ? body : JSON.stringify(body),
      });
      return [res.status, (await res.json()).error ?? null];
    };
    assert.deepEqual(await post("enroll", { invite }), [200, null]);
    await db.prepare("UPDATE policy SET paused=0").run();
    const text = () =>
      post("chat", {
        turn: now + "-" + id(),
        step: 0,
        date: "2040-03-12",
        zone: "+0000",
        input: [{ role: "user", content: "Find the fictional exam." }],
      });
    const results = [
      await text(),
      await post("transcribe", new Uint8Array(32000), { "X-Calendar-Request": now + "-" + id() }),
      await text(),
    ];
    assert.deepEqual(results, [
      [200, null],
      [503, "upstream_unavailable"],
      [503, "upstream_unavailable"],
    ]);
    assert.deepEqual(urls, [
      "https://api.openai.com/v1/responses",
      "https://api.openai.com/v1/audio/transcriptions",
      "https://api.openai.com/v1/responses",
    ]);
    const rows = await db.prepare("SELECT state,upstream_http,upstream_code FROM calls ORDER BY rowid").all();
    assert.deepEqual(rows.results, [
      { state: "measured", upstream_http: 200, upstream_code: null },
      { state: "held", upstream_http: 403, upstream_code: "insufficient_permissions" },
      { state: "held", upstream_http: 307, upstream_code: "not_provided" },
    ]);
  } finally {
    await mf.dispose();
  }
});
