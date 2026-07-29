"""
Prove the patch works by reading what actually goes on the wire.

Reasoning effort reaches the API as `output_config.effort` on the request body.
That field is the only ground truth: a subagent can report an effort it was
never launched at, but it cannot send a request body it did not send. So both
verification modes here run the real binary through a local HTTP endpoint and
read the bodies.

    live     the endpoint forwards to the real API, so the run genuinely
             completes. Costs a small amount of usage.
    offline  the endpoint answers with canned responses and forwards nothing,
             so it is free, but it drives the tool call itself rather than
             letting the model choose it.

Request headers are never read, logged or stored -- only `model` and
`output_config` are taken off each body. In live mode headers are passed
through to the upstream verbatim, without being inspected.
"""

import http.client
import http.server
import json
import os
import socket
import subprocess
import tempfile
import threading
import urllib.parse
from pathlib import Path

DEFAULT_UPSTREAM = "https://api.anthropic.com"
MODEL = "claude-sonnet-4-6"
PARENT_EFFORT = "low"
CHILD_EFFORT = "medium"

PROMPT = (
    "Use the Agent tool exactly once to spawn a general-purpose subagent at "
    f"effort {CHILD_EFFORT}. The subagent's prompt must be exactly: "
    "return 'hi', nothing else. "
    "Then reply with only what the subagent returned, and nothing else."
)

# What the offline mode injects instead of asking a model to choose it.
CANNED_TOOL_INPUT = {
    "description": "effort check",
    "prompt": "return 'hi', nothing else",
    "subagent_type": "general-purpose",
    "effort": CHILD_EFFORT,
    "run_in_background": False,
}


class VerificationError(Exception):
    pass


# ---------------------------------------------------------------------------
# canned responses (offline mode)
# ---------------------------------------------------------------------------


def _sse(events):
    return "".join(
        f"event: {name}\ndata: {json.dumps(payload)}\n\n" for name, payload in events
    ).encode()


def _message(blocks, stop_reason):
    events = [("message_start", {"type": "message_start", "message": {
        "id": "msg_local", "type": "message", "role": "assistant", "model": MODEL,
        "content": [], "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1}}})]
    for i, block in enumerate(blocks):
        events.append(("content_block_start", {
            "type": "content_block_start", "index": i, "content_block": block["start"]}))
        events.append(("content_block_delta", {
            "type": "content_block_delta", "index": i, "delta": block["delta"]}))
        events.append(("content_block_stop", {"type": "content_block_stop", "index": i}))
    events += [
        ("message_delta", {"type": "message_delta", "usage": {"output_tokens": 1},
                           "delta": {"stop_reason": stop_reason, "stop_sequence": None}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return _sse(events)


def _canned_text(text):
    return _message([{"start": {"type": "text", "text": ""},
                      "delta": {"type": "text_delta", "text": text}}], "end_turn")


def _canned_tool_use():
    return _message([{"start": {"type": "tool_use", "id": "toolu_local",
                                "name": "Agent", "input": {}},
                      "delta": {"type": "input_json_delta",
                                "partial_json": json.dumps(CANNED_TOOL_INPUT)}}], "tool_use")


# ---------------------------------------------------------------------------
# the endpoint
# ---------------------------------------------------------------------------


class _Recorder:
    def __init__(self, live, upstream):
        self.live = live
        self.upstream = urllib.parse.urlparse(upstream)
        self.calls = []
        self.lock = threading.Lock()

    def note(self, body):
        """
        Record a conversation turn; return its 1-based index, or None.

        Claude Code makes side calls that are not conversation turns -- session
        titling, for one, which runs at its own fixed effort and would
        otherwise be mistaken for the parent's first turn. A real turn is the
        one carrying the tool set.
        """
        if not body.get("tools"):
            return None
        with self.lock:
            self.calls.append({
                "model": body.get("model"),
                "effort": (body.get("output_config") or {}).get("effort"),
                "n": len(self.calls) + 1,
            })
            return len(self.calls)


def _handler_for(rec):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_GET(self):
            if rec.live:
                return self._proxy(b"")
            self._reply(200, b'{"data":[]}', "application/json")

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length)
            try:
                body = json.loads(raw)
            except ValueError:
                body = {}
            index = rec.note(body)

            if rec.live:
                return self._proxy(raw)
            if index == 1:
                return self._reply_sse(_canned_tool_use())
            return self._reply_sse(_canned_text("hi"))

        # -- plumbing ------------------------------------------------------

        def _reply(self, code, payload, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _reply_sse(self, payload):
            self._reply(200, payload, "text/event-stream")

        def _proxy(self, raw):
            """Forward verbatim. Headers are relayed without being inspected."""
            scheme, host = rec.upstream.scheme, rec.upstream.netloc
            conn_cls = (http.client.HTTPSConnection if scheme == "https"
                        else http.client.HTTPConnection)
            conn = conn_cls(host, timeout=180)
            headers = {k: v for k, v in self.headers.items()
                       if k.lower() not in ("host", "content-length")}
            headers["Host"] = host
            if raw:
                headers["Content-Length"] = str(len(raw))
            try:
                conn.request(self.command, rec.upstream.path + self.path,
                             body=raw or None, headers=headers)
                upstream = conn.getresponse()
                self.send_response(upstream.status)
                for k, v in upstream.getheaders():
                    if k.lower() in ("transfer-encoding", "connection", "content-length"):
                        continue
                    self.send_header(k, v)
                self.send_header("Connection", "close")
                self.end_headers()
                while True:
                    chunk = upstream.read(8192)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except Exception as exc:  # upstream trouble -> surface as a 502
                try:
                    self._reply(502, json.dumps(
                        {"type": "error", "error": {"type": "api_error",
                                                    "message": str(exc)}}
                    ).encode(), "application/json")
                except Exception:
                    pass
            finally:
                conn.close()

    return Handler


def _serve(rec):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), _handler_for(rec))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port


# ---------------------------------------------------------------------------
# the check
# ---------------------------------------------------------------------------


def unsupported_reason():
    """Why a live check cannot run here, or None."""
    for var in ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX"):
        if os.environ.get(var):
            return f"{var} is set; this check only understands the Anthropic API"
    return None


def run(binary, live=True, log=print, timeout=300):
    """
    Spawn a subagent through `binary` and assert the wire says what it should.

    Returns the list of observed (model, effort) pairs.
    """
    if live and unsupported_reason():
        raise VerificationError(unsupported_reason())

    upstream = os.environ.get("ANTHROPIC_BASE_URL") or DEFAULT_UPSTREAM
    rec = _Recorder(live, upstream)
    server, port = _serve(rec)

    env = dict(os.environ)
    env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{port}"
    env["DISABLE_AUTOUPDATER"] = "1"
    env["DISABLE_TELEMETRY"] = "1"
    # Isolate the run: its own project directory, so nothing lands in whatever
    # the user happens to be sitting in.
    workdir = tempfile.mkdtemp(prefix="ccpatch-verify-")

    cmd = [str(binary), "-p", PROMPT, "--model", MODEL,
           "--effort", PARENT_EFFORT, "--allowedTools", "Agent"]
    log(f"  {MODEL} at effort {PARENT_EFFORT}, spawning a subagent at {CHILD_EFFORT}")

    try:
        proc = subprocess.run(cmd, cwd=workdir, env=env, capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise VerificationError(f"the run did not finish within {timeout}s")
    finally:
        server.shutdown()

    answer = (proc.stdout or "").strip()
    calls = rec.calls

    if not calls:
        raise VerificationError(
            "the binary made no API calls.\n"
            + (proc.stderr or proc.stdout or "").strip()[:600]
        )

    for call in calls:
        log(f"    request {call['n']}: model={call['model']} effort={call['effort']}")

    parent = calls[0]
    child = next((c for c in calls[1:] if c["effort"] == CHILD_EFFORT), None)

    if parent["effort"] != PARENT_EFFORT:
        raise VerificationError(
            f"the parent conversation ran at effort {parent['effort']!r}, "
            f"expected {PARENT_EFFORT!r}"
        )
    if child is None:
        seen = sorted({str(c["effort"]) for c in calls[1:]})
        raise VerificationError(
            f"no subagent request was sent at effort {CHILD_EFFORT!r} "
            f"(saw: {', '.join(seen) or 'none'}).\n"
            "The `effort` parameter was accepted but did not reach the wire -- "
            "this is exactly the bug the patch is meant to fix, so the patch "
            "did not take effect.\n"
            + (f"Model's answer: {answer[:200]}" if answer else "")
        )
    if live and "hi" not in answer.lower():
        log(f"  note: the run finished but its answer was {answer[:120]!r}")

    log(f"  parent at {parent['effort']}, subagent at {child['effort']} -- confirmed "
        "on the wire")
    return calls
