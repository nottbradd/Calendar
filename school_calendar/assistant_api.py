"""Stateless Responses tool loop. The API can read a bounded selection or propose a plan."""

from datetime import datetime
import http.client
import json
from pathlib import Path
import re
import socket
import threading
from uuid import uuid4

from .assistant_calendar import FIELDS

MAX_ROUNDS = 4
MAX_OUTPUT = 1800


# One calendar protocol for direct and hosted requests. Only the Worker selects
# its upstream instructions, schemas, model, tier and output cap.
_PROTOCOL = json.loads((Path(__file__).parent / "assets" / "assistant-protocol.json").read_text("utf-8"))
INSTRUCTIONS = _PROTOCOL["instructions"]
TOOLS = _PROTOCOL["tools"]
FILTERS = TOOLS[0]["parameters"]["properties"]
CHANGES = TOOLS[2]["parameters"]["properties"]["actions"]["items"]["properties"]["changes"]["properties"]
RATES = {
    "gpt-6-luna": (0.10, 0.01, 0.125, 0.50),
    "gpt-6.1-sol": (2, 0.10, 2.50, 10),
    "gpt-5.4": (2.50, 0.25, None, 15),
}


class ApiError(ValueError):
    pass


_BILLING_MESSAGES = {
    "credit_balance_exhausted": (
        "OpenAI reports that your organization's prepaid credits are exhausted. "
        "An organization owner can add credits in billing settings."
    ),
    "project_spend_limit_exceeded": (
        "This project reached its monthly spend limit. Wait for the monthly reset "
        "or ask an owner to raise the limit in project settings."
    ),
    "organization_spend_limit_exceeded": (
        "Your organization reached its monthly spend limit. Wait for the monthly reset "
        "or ask an owner to raise the limit in organization settings."
    ),
    "organization_usage_limit_exceeded": (
        "Your organization reached its OpenAI-assigned usage limit. "
        "Request a higher approved limit from OpenAI or contact support."
    ),
    "insufficient_quota": "OpenAI reported insufficient quota. Check organization billing and project limits.",
    "billing_hard_limit_reached": "The API billing hard limit was reached. Review your billing limits.",
}


def _error_message(status, error):
    details = error if isinstance(error, dict) else {}
    code = details.get("code")
    if isinstance(code, str) and code in _BILLING_MESSAGES:
        return _BILLING_MESSAGES[code]
    if status == 429:
        # The same broad error type can accompany different, more specific codes.
        kind = details.get("type")
        if code in ("rate_limit_exceeded", "slow_down") or kind in (
            "rate_limit_error",
            "tokens",
            "requests",
        ):
            return "OpenAI rate limit reached. Wait before sending another request."
        if kind == "insufficient_quota":
            return _BILLING_MESSAGES["insufficient_quota"]
        return "OpenAI limited this request (HTTP 429). Check project limits and organization billing."
    messages = {
        401: "The project API key was rejected. Replace it in Settings.",
        403: "The project does not have access to this request or model.",
        404: "The requested model is unavailable for this project. Choose a model in Settings.",
    }
    return messages.get(status, f"OpenAI declined the request (HTTP {status}).")


class Cancellation(threading.Event):
    def __init__(self):
        super().__init__()
        self.connection = None

    def set(self):
        super().set()
        conn = self.connection
        if conn and conn.sock:
            try:
                conn.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def check(self):
        if self.is_set():
            raise InterruptedError()


def post(key, path, body, mime, cancel, *, host="api.openai.com", error_hint=_error_message, extra_headers=None):
    cancel.check()
    if not key:
        raise ApiError("Add your project API key in Settings → Assistant.")
    if not re.fullmatch(r"[!-~]{1,1024}", key):
        raise ApiError("Invalid project API key. Replace it in Assistant settings.")
    conn = http.client.HTTPSConnection(host, timeout=35)
    cancel.connection = conn
    try:
        cancel.check()
        conn.connect()
        cancel.check()
        conn.request("POST", "/v1/" + path, body, {"Authorization": "Bearer " + key, "Content-Type": mime,
                                                   **(extra_headers or {})})
        response = conn.getresponse()
        raw = response.read(600_001)
        cancel.check()
        if len(raw) > 600_000:
            raise ApiError("The API response exceeded the size limit.")
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        if response.status != 200:
            raise ApiError(error_hint(response.status, payload.get("error")))
        return payload
    except (OSError, http.client.HTTPException) as exc:
        cancel.check()
        raise ApiError("Connection failed or timed out. Check your connection and retry.") from exc
    finally:
        cancel.connection = None
        conn.close()


def response(key, body, cancel):
    return post(key, "responses", json.dumps(body).encode(), "application/json", cancel)


def transcribe(key, audio, cancel):
    if not audio or len(audio) > 2_000_000:
        raise ApiError("No usable recording.")
    boundary = uuid4().hex
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\ngpt-transcribe\r\n'.encode(),
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="speech.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode(),
        audio,
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    result = post(
        key, "audio/transcriptions", b"".join(parts), "multipart/form-data; boundary=" + boundary, cancel
    )
    transcript = result.get("text")
    if not isinstance(transcript, str) or not transcript.strip():
        raise ApiError("No speech was transcribed. Record again or type your message.")
    if len(transcript) > 2400:
        raise ApiError("Transcript is too long. Record a shorter message.")
    return transcript.strip()


def select_model(options, text):
    mode = options["mode"]
    if mode == "GPT-5.4":
        return "gpt-5.4"
    if mode == "Planning" or (
        mode == "Auto"
        and re.search(
            r"\b(keep|fixed|constraints?|conflicts?|overlap\w*|without|around|both|prioriti[sz]\w*|optimis\w*|optimiz\w*|push|fit|several|multiple|rearrange|plan around|reschedule all|move all|schedule all)\b",
            text,
            re.I,
        )
    ):
        return options["planning"]
    return options["routine"]


def estimate(model, usage):
    rates = RATES.get(model)
    if rates is None or not {"input_tokens", "output_tokens"} <= usage.keys():
        return None
    inp, out = usage["input_tokens"], usage["output_tokens"]
    details = usage.get("input_tokens_details") or {}
    cached = details.get("cached_tokens", 0)
    write = details.get("cache_write_tokens", 0)
    return (
        max(0, inp - cached - write) * rates[0]
        + cached * rates[1]
        + write * (rates[2] or rates[0])
        + out * rates[3]
    ) / 1_000_000


def validate_wire(value, schema):
    types = schema["type"]
    types = [types] if isinstance(types, str) else types
    kind = {str: "string", bool: "boolean", dict: "object", list: "array", type(None): "null"}.get(
        type(value)
    )
    if kind not in types or ("enum" in schema and value not in schema["enum"]):
        raise ValueError("Unexpected tool argument type or value.")
    if kind == "object":
        properties = schema["properties"]
        if set(value) != set(properties):
            raise ValueError("Missing or unknown calendar tool fields.")
        for name, field in properties.items():
            validate_wire(value[name], field)
    if kind == "array":
        if len(value) > 20:
            raise ValueError("Too many calendar tool items.")
        for element in value:
            validate_wire(element, schema["items"])


def run(tools, key, options, history, message, cancel, transport=None):
    if not key:
        raise ApiError("Add your project API key in Settings → Assistant.")
    if not 0 < len(message.strip()) <= 2400:
        raise ApiError("Use a message of 1 to 2,400 characters.")
    model = select_model(options, message)
    now = datetime.now().astimezone()
    instructions = (
        INSTRUCTIONS + f"\nLocal date: {tools.today:%A %Y-%m-%d}. Local system timezone: {now:%Z %z}."
    )
    items = [{"role": r, "content": s[:1200]} for r, s in history[-6:] if r in ("user", "assistant")]
    items.append({"role": "user", "content": message})
    counts = []
    costs = []
    for round_no in range(MAX_ROUNDS):
        cancel.check()
        body = {
            "model": model,
            "instructions": instructions,
            "input": items,
            "tools": TOOLS,
            "store": False,
            "parallel_tool_calls": False,
            "max_output_tokens": MAX_OUTPUT,
            "include": ["reasoning.encrypted_content"],
        }
        if len(json.dumps(body).encode()) > 100_000:
            raise ApiError("This exchange reached the context limit. Narrow your request.")
        res = (transport or response)(key, body, cancel)
        cancel.check()
        if (
            not isinstance(res, dict)
            or not isinstance(res.get("output"), list)
            or not all(isinstance(item, dict) for item in res["output"])
        ):
            raise ApiError("The API returned an invalid response.")
        usage = res.get("usage") or {}
        counts.append(usage.get("total_tokens"))
        reported_model = res.get("model") or model
        hosted_estimate = res.get("sponsored_nusd")
        costs.append(hosted_estimate / 1_000_000_000 if isinstance(hosted_estimate, int) else estimate(model, usage))
        tokens = str(sum(counts)) if all(isinstance(n, int) for n in counts) else "unavailable"
        cost = f"US${sum(costs):.6f}" if None not in costs else "unavailable"
        status = f"{reported_model} · API tokens: {tokens} · Estimated list cost: {cost}"
        if res.get("status") not in ("completed", None):
            raise ApiError("The response was incomplete. Narrow your request.")
        output = res.get("output", [])
        calls = [x for x in output if x.get("type") == "function_call"]
        if not calls:
            reply = "\n".join(
                c["text"]
                for x in output
                if x.get("type") == "message"
                for c in x.get("content", [])
                if c.get("type") == "output_text" and isinstance(c.get("text"), str)
            )
            if not reply.strip():
                raise ApiError("No usable response. Try rephrasing the request.")
            return reply[:5000], None, status
        if len(calls) != 1:
            raise ApiError("Received an invalid tool sequence. No changes were applied.")
        call = calls[0]
        if not all(isinstance(call.get(k), str) and call[k] for k in ("call_id", "name", "arguments")):
            raise ApiError("The API returned an incomplete tool call.")
        try:
            args = json.loads(call["arguments"])
            if not isinstance(args, dict):
                raise ValueError()
            name = call["name"]
            schema = next((tool["parameters"] for tool in TOOLS if tool["name"] == name), None)
            if schema is None:
                raise ValueError("Unknown calendar tool.")
            validate_wire(args, schema)
            if name == "propose_changes" and set(args) == {"actions", "fixed_ids"}:
                actions = args["actions"]
                if not isinstance(actions, list) or len(actions) > 12:
                    raise ValueError("Invalid action list.")
                for action in actions:
                    if (
                        not isinstance(action, dict)
                        or set(action) != {"op", "id", "changes"}
                        or not isinstance(action["changes"], dict)
                        or set(action["changes"]) - set(FIELDS)
                    ):
                        raise ValueError("Invalid action fields.")
                    action["changes"] = {k: v for k, v in action["changes"].items() if v is not None}
                plan = tools.propose(actions, args["fixed_ids"])
                return "Review the proposed changes below.", plan, status
            if name == "find_entries":
                data = tools.query(args)
            elif name == "availability":
                data = tools.availability(**args)
            else:
                raise ValueError("Unknown calendar tool.")
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            # An invalid tool call never writes calendar data or executes model text.
            reason = (
                str(exc)
                if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError)
                else ""
            )
            data = {"error": reason[:180] or "Invalid tool arguments. Clarify the request."}
        items.extend(output)
        items.append(
            {"type": "function_call_output", "call_id": call["call_id"], "output": json.dumps(data)}
        )
    raise ApiError("The assistant reached its tool limit. Narrow the request and try again.")
