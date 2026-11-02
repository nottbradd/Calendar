"""Inspect only disposable trial events in memory. Run alongside verify_sync.py --base <development URL>."""
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
from time import monotonic
from urllib.parse import urlsplit


def main():
    backend = Path(__file__).resolve().parents[1] / "backend"
    command = ["node", "node_modules/wrangler/bin/wrangler.js", "tail", "calender-sync-dev",
               "--format", "json", "--header", "X-Calender-Check:disposable-v1", "--method", "POST"]
    process = subprocess.Popen(command, cwd=backend, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               encoding="utf-8", errors="replace", env={**os.environ, "WRANGLER_SEND_METRICS": "false"})
    lines = queue.Queue()

    def read():
        for line in process.stdout:
            lines.put(line)
        lines.put(None)
    threading.Thread(target=read, daemon=True).start()
    buffer, seen = "", set()
    deadline = monotonic() + 180
    try:
        while not {"push", "pull"} <= seen:
            if monotonic() >= deadline:
                raise RuntimeError("No matching upload and download traces arrived before timeout.")
            try:
                line = lines.get(timeout=2)
            except queue.Empty:
                continue
            if line is None:
                raise RuntimeError("Wrangler tail ended before the check. Verify the Workers tail permission.")
            if not buffer and not line.lstrip().startswith("{"):
                continue
            buffer += line
            try:
                event = json.loads(buffer)
            except ValueError:
                continue
            buffer = ""
            request = event.get("event", {}).get("request", {})
            headers = {k.lower(): v for k, v in request.get("headers", {}).items()}
            if headers.get("x-calender-check") != "disposable-v1":
                continue
            path = urlsplit(request["url"])
            assert not path.query and "body" not in request
            assert not event.get("logs") and not event.get("exceptions") and not event.get("diagnosticsChannelEvents")
            assert headers.get("authorization", "REDACTED").upper() in {"REDACTED", "[REDACTED]"}
            seen.add(path.path.rsplit("/", 1)[-1])
        print("Live push and pull traces contained no request bodies, query strings, app logs, exceptions or unredacted device credentials.")
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    main()
