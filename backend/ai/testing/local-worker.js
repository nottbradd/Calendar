// Miniflare-only entry point. Never used by the deployment config. No external fetches.
import { createWorker } from "../worker.js";
import protocol from "../../../school_calendar/assets/assistant-protocol.json" with { type: "json" };

const empty = (schema) => Object.fromEntries(Object.keys(schema.properties).map((k) => [k, null]));
async function fictionalUpstream(url, opts) {
  // Exercise the actual workerd Request constructor while keeping all traffic offline.
  const request = new Request(url, opts);
  if (url.endsWith("/audio/transcriptions")) {
    const form = await request.formData();
    const pcm = new Uint8Array(await form.get("file").arrayBuffer()).subarray(44);
    const duration = Math.ceil(pcm.length / 32000);
    const text = pcm[0] === 1 && pcm[2] === 1 ? "Find my fictional exam." : "Tuesday.";
    return Response.json({ text, usage: { type: "duration", seconds: duration } });
  }
  const p = await request.json(),
    last = p.input.at(-1);
  const propose = last.role === "user" && last.content.startsWith("Add ");
  let output;
  if (propose) {
    const fields = empty(protocol.tools[2].parameters.properties.actions.items.properties.changes);
    Object.assign(fields, { title: "Fictional hosted assessment", day: "2031-04-14" });
    output = [
      {
        type: "function_call",
        name: "propose_changes",
        call_id: "sample-call",
        arguments: JSON.stringify({
          actions: [{ op: "create", id: null, changes: fields }],
          fixed_ids: [],
        }),
      },
    ];
  } else if (last.role === "user") {
    const query = { ...empty(protocol.tools[0].parameters), title: "Fictional" };
    output = [
      {
        type: "function_call",
        name: "find_entries",
        call_id: "sample-call",
        arguments: JSON.stringify(query),
      },
    ];
  } else {
    output = [
      {
        type: "message",
        role: "assistant",
        content: [
          {
            type: "output_text",
            text: last.output?.includes("2040-03-12") ? "2040-03-12" : "The fictional lookup completed.",
          },
        ],
      },
    ];
  }
  return Response.json({
    status: "completed",
    output,
    usage: {
      input_tokens: 900,
      output_tokens: 100,
      total_tokens: 1000,
      input_tokens_details: { cached_tokens: 0, cache_write_tokens: 0 },
    },
  });
}

export default {
  async fetch(request, env) {
    const stats = { reads: 0, writes: 0, upstream: 0 };
    function stmt(s) {
      async function result(method) {
        const r = await s[method]();
        stats.reads += r.meta.rows_read;
        stats.writes += r.meta.rows_written;
        return r;
      }
      return {
        bind: (...args) => stmt(s.bind(...args)),
        run: () => result("run"),
        async first(col) {
          const row = (await result("all")).results[0] ?? null;
          return col ? (row?.[col] ?? null) : row;
        },
      };
    }
    const service = createWorker({
      upstream: (url, opts) => {
        stats.upstream++;
        return fictionalUpstream(url, opts);
      },
    });
    const reply = await service.fetch(request, {
      AI_DB: { prepare: (sql) => stmt(env.AI_DB.prepare(sql)) },
      OPENAI_SERVICE_KEY: "fictional-test-only",
    });
    const out = new Response(reply.body, reply);
    out.headers.set("X-Calendar-Probe-Mock", "offline");
    out.headers.set("X-Local-Measurements", JSON.stringify(stats));
    return out;
  },
};
