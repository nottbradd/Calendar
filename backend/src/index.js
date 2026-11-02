function statusResponse(status, code, headers = {}) {
  return Response.json({ status }, {
    status: code,
    headers: { "Cache-Control": "no-store", ...headers },
  });
}

export default {
  async fetch(request, env) {
    if (new URL(request.url).pathname !== "/health") {
      return statusResponse("not_found", 404);
    }
    if (request.method !== "GET") {
      return statusResponse("method_not_allowed", 405, { Allow: "GET" });
    }

    try {
      const ready = await env.DB.prepare("SELECT 1 AS ready").first("ready");
      if (ready === 1) return statusResponse("ok", 200);
    } catch {
      // D1 errors can include internal details. Keep the public response generic.
    }
    return statusResponse("unavailable", 503);
  },
};
