"""Probe the OpenAI project with fixed fictional inputs. Run with --help for modes."""

import argparse
from datetime import date, datetime, time, timedelta, timezone
from http.client import HTTPSConnection, HTTPException
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from urllib.parse import urlencode
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MODELS = ("gpt-6-luna", "gpt-6.1-sol", "gpt-5.4")
EXAM_DAY = "2040-03-12"


def fictional_recording():
    """Use the installed Windows speech synthesizer. Never open a microphone."""
    command = """
    $ErrorActionPreference = 'Stop'
    Add-Type -AssemblyName System.Speech
    $voice = New-Object System.Speech.Synthesis.SpeechSynthesizer
    try {
        $voice.SetOutputToWaveFile([IO.Path]::Combine((Get-Location).Path, 'sample.wav'))
        $voice.Speak('The fictional violet exam is on Tuesday.')
    } finally { $voice.Dispose() }
    """
    with TemporaryDirectory(prefix="calender-fictional-") as tmp:
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("OPENAI_")}
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            cwd=tmp,
            env=env,
            capture_output=True,
            check=True,
            timeout=20,
        )
        audio = (Path(tmp) / "sample.wav").read_bytes()
    with wave.open(io.BytesIO(audio)) as stream:
        seconds = stream.getnframes() / stream.getframerate()
    if not 0 < seconds <= 12 or len(audio) > 2_000_000:
        raise ValueError("Synthetic recording outside the probe limit.")
    return audio, seconds


def request(method, path, key, body=None, content_type="application/json"):
    # A fixed TLS host, no redirects, no retries and no response-body error logging.
    if not key or len(key) > 1024 or any(not 33 <= ord(char) <= 126 for char in key):
        raise ValueError("Invalid API credential format.")
    headers = {"Authorization": "Bearer " + key, "Content-Type": content_type}
    connection = HTTPSConnection("api.openai.com", timeout=40)
    try:
        connection.request(method, "/v1/" + path, body=body, headers=headers)
        response = connection.getresponse()
        request_id = response.getheader("x-request-id")
        if response.status != 200:
            raise RuntimeError(
                f"{path.split('?')[0]} returned HTTP {response.status}, request {request_id}."
            )
        raw = response.read(524289)
        if len(raw) > 524288:
            raise RuntimeError("Unexpectedly large response.")
        result = json.loads(raw)
        if method == "POST":
            print(
                json.dumps(
                    {
                        "endpoint": path,
                        "utc": datetime.now(timezone.utc).isoformat(),
                        "request_id": request_id,
                        "model_reported_by_api": result.get("model"),
                        "usage_reported_by_api": result.get("usage"),
                    }
                ),
                flush=True,
            )
        return result
    finally:
        connection.close()


def verify_function_call(key, model):
    tool = {
        "type": "function",
        "name": "lookup_demo",
        "description": "Read the fictional exam date.",
        "strict": True,
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"exam": {"type": "string", "enum": ["violet"]}},
            "required": ["exam"],
        },
    }
    items = [
        {"role": "user", "content": "Look up the fictional violet exam. Reply with only its ISO date."}
    ]
    payload = {
        "model": model,
        "input": items,
        "tools": [tool],
        "store": False,
        "include": ["reasoning.encrypted_content"],
        "max_output_tokens": 256,
        "parallel_tool_calls": False,
        "reasoning": {"effort": "low" if model == MODELS[1] else "none"},
        "tool_choice": {"type": "function", "name": tool["name"]},
    }
    first = request("POST", "responses", key, json.dumps(payload).encode())
    calls = [item for item in first["output"] if item["type"] == "function_call"]
    if (
        len(calls) != 1
        or calls[0]["name"] != tool["name"]
        or json.loads(calls[0]["arguments"]) != {"exam": "violet"}
    ):
        raise ValueError("The model did not request the bounded fictional lookup.")
    items.extend(first["output"])
    items.append(
        {
            "type": "function_call_output",
            "call_id": calls[0]["call_id"],
            "output": json.dumps({"date": EXAM_DAY}),
        }
    )
    payload["tool_choice"] = "none"
    second = request("POST", "responses", key, json.dumps(payload).encode())
    text = " ".join(
        part.get("text", "")
        for item in second["output"]
        if item["type"] == "message"
        for part in item["content"]
        if part["type"] == "output_text"
    )
    if EXAM_DAY not in text:
        raise ValueError("The model did not return the date from the function result.")
    print("Fictional function-call round trip passed.")


def verify_transcription(key, audio):
    boundary = "CalenderFictionalWav913"
    prefix = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\ngpt-transcribe\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="sample.wav"\r\n'
        "Content-Type: audio/wav\r\n\r\n"
    ).encode()
    data = prefix + audio + f"\r\n--{boundary}--\r\n".encode()
    result = request("POST", "audio/transcriptions", key, data, "multipart/form-data; boundary=" + boundary)
    if "tuesday" not in result.get("text", "").casefold():
        raise ValueError("Transcript did not match the fictional sample.")
    print("Transcription of the synthesized fictional recording passed.")


def project_costs(day, project):
    admin = os.environ.get("OPENAI_ADMIN_KEY")
    if not admin or not project or not project.startswith("proj_"):
        raise ValueError(
            "Costs lookup requires a project ID and a separate OPENAI_ADMIN_KEY in this shell."
        )
    start = datetime.combine(day, time.min, timezone.utc)
    end = min(start + timedelta(days=1), datetime.now(timezone.utc))
    if end <= start:
        raise ValueError("Select a past or current UTC day.")
    params = {
        "start_time": int(start.timestamp()),
        "end_time": int(end.timestamp()),
        "limit": 1,
        "project_ids": [project],
        "group_by": ["project_id", "line_item"],
    }
    print("Posted project costs by UTC day and line item. Other project traffic is included.")
    while True:
        data = request("GET", "organization/costs?" + urlencode(params, doseq=True), admin)
        for bucket in data["data"]:
            for row in bucket["results"]:
                if row.get("project_id") != project:
                    raise ValueError("Unexpected project in costs result.")
                print(
                    json.dumps(
                        {
                            "utc_day": day.isoformat(),
                            "line_item": row.get("line_item"),
                            "amount": row["amount"],
                        }
                    )
                )
        if not data.get("has_more"):
            break
        params["page"] = data["next_page"]
    print("An empty result or zero posted cost does not establish that a request was free.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--run", action="store_true", help="Send two Responses calls and one short WAV to OpenAI."
    )
    mode.add_argument(
        "--check-audio", action="store_true", help="Generate and validate a fictional WAV offline."
    )
    mode.add_argument(
        "--costs-on", type=date.fromisoformat, metavar="YYYY-MM-DD", help="Read posted project costs."
    )
    parser.add_argument("--project-id", help="Project ID for --costs-on.")
    parser.add_argument("--model", choices=MODELS, default=MODELS[0])
    args = parser.parse_args()
    if args.costs_on:
        project_costs(args.costs_on, args.project_id)
        return
    if args.run:
        from school_calendar.assistant_config import project_key

        key = project_key()
        if not key:
            raise ValueError(
                "Live verification skipped: configure your OpenAI project key in Calender first."
            )
    audio, seconds = fictional_recording()
    print(f"Fictional synthesized WAV validated offline ({seconds:.2f} seconds).")
    if args.run:
        print(f"Testing {args.model} function calling and gpt-transcribe with fictional data.", flush=True)
        verify_function_call(key, args.model)
        verify_transcription(key, audio)
        print(
            "Actual billing requires reconciliation against this project's posted costs and credit grants."
        )


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError, HTTPException, KeyError, subprocess.SubprocessError) as exc:
        raise SystemExit(str(exc)) from None
