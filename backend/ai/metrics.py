"""Read today's aggregate Cloudflare usage across all Workers and D1 databases."""

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
from urllib.error import URLError
from urllib.request import build_opener, HTTPRedirectHandler, Request


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def wrangler(*args):
    result = subprocess.run(
        ["node", "node_modules/wrangler/bin/wrangler.js", *args, "--json"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "CI": "true", "WRANGLER_SEND_METRICS": "false"},
    )
    if result.returncode:
        raise SystemExit("Sign in with Wrangler or supply a read-only analytics token and account ID.")
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--beta-minutes",
        metavar="N",
        type=int,
        help="Only read the deployed beta Worker's last N minutes, from 1 to 1440.",
    )
    args = parser.parse_args()
    if args.beta_minutes is not None and not 1 <= args.beta_minutes <= 1440:
        parser.error("Choose 1 to 1440 minutes.")
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    if not account:
        accounts = wrangler("whoami")["accounts"]
        if len(accounts) != 1:
            raise SystemExit("Select the intended account using CLOUDFLARE_ACCOUNT_ID.")
        account = accounts[0]["id"]
    token = os.environ.get("CLOUDFLARE_ANALYTICS_TOKEN") or wrangler("auth", "token").get("token")
    if not token:
        raise SystemExit("A Cloudflare bearer with Account Analytics Read is required.")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    start = (
        now - timedelta(minutes=args.beta_minutes)
        if args.beta_minutes
        else now.replace(hour=0, minute=0, second=0)
    )
    variables = {"account": account, "start": start.isoformat(), "end": now.isoformat()}
    time_filter = "datetime_geq:$start,datetime_leq:$end"
    queries = {
        "account_workers": ("workersInvocationsAdaptive", time_filter, "sum{requests errors}"),
        "account_d1": ("d1AnalyticsAdaptiveGroups", time_filter, "sum{rowsRead rowsWritten}"),
        "beta_worker": (
            "workersInvocationsAdaptive",
            time_filter + ',scriptName:"calendar-ai-beta"',
            "sum{requests errors} quantiles{cpuTimeP50 cpuTimeP99}",
        ),
    }
    if args.beta_minutes:
        queries = {"beta_worker": queries["beta_worker"]}
    results = {"utc_start": variables["start"], "utc_end": variables["end"]}
    opener = build_opener(NoRedirects())
    for name, (dataset, filters, fields) in queries.items():
        query = (
            "query($account:string!,$start:string!,$end:string!){viewer{accounts(filter:{accountTag:$account}){"
            + dataset
            + "(limit:1,filter:{"
            + filters
            + "}){"
            + fields
            + "}}}}"
        )
        request = Request(
            "https://api.cloudflare.com/client/v4/graphql",
            data=json.dumps({"query": query, "variables": variables}).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
        )
        try:
            with opener.open(request, timeout=20) as response:
                data = json.load(response)
            if data.get("errors"):
                results[name] = {
                    "unavailable": [e.get("message", "Analytics denied") for e in data["errors"]]
                }
            else:
                results[name] = data["data"]["viewer"]["accounts"][0][dataset]
        except (URLError, ValueError, IndexError, KeyError):
            results[name] = {
                "unavailable": "No account result. Check connectivity and Account Analytics Read."
            }
    print(json.dumps(results, indent=2))
    return int(any("unavailable" in results[name] for name in queries))


if __name__ == "__main__":
    raise SystemExit(main())
