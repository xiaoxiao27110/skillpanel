#!/usr/bin/env python3
"""Black-box OpenCode/Hermes hot-reload test for the running container.

The default Hermes check stays model-free: it keeps one dashboard WebSocket
and one live/stored session while validating command discovery across an
enabled -> disabled -> enabled cycle. Pass ``--hermes-llm-canary`` to also run
three real model turns and verify the revision-aware system-prompt refresh.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque


CANARY_SKILL = "canary-alpha"
CANARY_TOKEN = "SKILLPANEL_ALPHA_7F3A"
DISABLED_TOKEN = "SKILLPANEL_DISABLED_OK"
PID_KEYS = ("controller", "opencode", "hermes")


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def request(url, method="GET", payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read())


def _http_error_text(exc):
    try:
        body = exc.read().decode("utf-8", errors="replace").strip()
    except Exception:
        body = ""
    return f"HTTP {exc.code}{f': {body}' if body else ''}"


def _assert_main_pids(pids, original_pids, phase):
    for name in PID_KEYS:
        require(
            pids.get(name) == original_pids.get(name),
            f"{phase}: {name} PID changed from {original_pids.get(name)} "
            f"to {pids.get(name)}",
        )


def _toggle(controller_url, skill, enabled, revision, original_pids, phase):
    try:
        result = request(
            f"{controller_url}/skills/{skill}",
            "PUT",
            {"enabled": enabled, "expected_revision": revision},
        )
    except urllib.error.HTTPError as exc:
        raise AssertionError(
            f"{phase}: controller could not set {skill!r} enabled={enabled}: "
            f"{_http_error_text(exc)}"
        ) from exc

    _assert_main_pids(result.get("pids", {}), original_pids, phase)
    require(
        (skill in result.get("opencode_skills", [])) == enabled,
        f"{phase}: OpenCode catalog did not converge to enabled={enabled}; "
        f"catalog={result.get('opencode_skills')!r}",
    )
    return result


def _skill_enabled(catalog, skill):
    try:
        enabled = next(item["enabled"] for item in catalog["skills"] if item["name"] == skill)
    except (KeyError, StopIteration, TypeError) as exc:
        raise AssertionError(
            f"Fixture skill {skill!r} is missing; catalog={catalog.get('skills')!r}"
        ) from exc
    require(type(enabled) is bool, f"Fixture skill {skill!r} has invalid enabled state")
    return enabled


def _restore_original(controller_url, skill, original_enabled, original_pids):
    """Restore from a freshly fetched revision and prove the final state."""

    for attempt in range(3):
        catalog = request(f"{controller_url}/skills")
        _assert_main_pids(catalog.get("pids", {}), original_pids, "cleanup catalog")
        _skill_enabled(catalog, skill)
        try:
            restored = request(
                f"{controller_url}/skills/{skill}",
                "PUT",
                {
                    "enabled": original_enabled,
                    "expected_revision": catalog["revision"],
                },
            )
        except urllib.error.HTTPError as exc:
            if exc.code == 409 and attempt < 2:
                continue
            raise AssertionError(
                f"cleanup could not restore {skill!r} enabled={original_enabled}: "
                f"{_http_error_text(exc)}"
            ) from exc

        _assert_main_pids(restored.get("pids", {}), original_pids, "cleanup PUT")
        require(
            restored.get("enabled") is original_enabled,
            f"cleanup PUT returned enabled={restored.get('enabled')!r}",
        )
        require(
            (skill in restored.get("opencode_skills", [])) == original_enabled,
            "cleanup PUT left the OpenCode catalog in the wrong state",
        )
        final = request(f"{controller_url}/skills")
        _assert_main_pids(final.get("pids", {}), original_pids, "cleanup final catalog")
        final_enabled = _skill_enabled(final, skill)
        require(
            final_enabled is original_enabled,
            f"cleanup final state is {final_enabled}, expected {original_enabled}",
        )
        require(
            final.get("revision") == restored.get("revision"),
            "cleanup final catalog revision differs from cleanup PUT revision",
        )
        return {
            "ok": True,
            "original_enabled": original_enabled,
            "final_enabled": final_enabled,
            "put_changed": restored.get("changed"),
            "put_revision": restored["revision"],
            "final_revision": final["revision"],
            "controller_state_matches": True,
            "opencode_catalog_matches": True,
            "pids_unchanged": True,
        }
    raise AssertionError("cleanup revision conflicted three consecutive times")


def _hermes_ticket(base_url, username, password):
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    login_body = json.dumps(
        {
            "provider": "basic",
            "username": username,
            "password": password,
            "next": "/",
        }
    ).encode()
    login = urllib.request.Request(
        f"{base_url}/auth/password-login",
        data=login_body,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with opener.open(login, timeout=30) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise AssertionError(
            "Hermes dashboard login failed; check --hermes-user/--hermes-password "
            f"and dashboard auth configuration ({_http_error_text(exc)})"
        ) from exc
    require(payload.get("ok") is True, f"Hermes dashboard login returned {payload!r}")

    ticket_request = urllib.request.Request(
        f"{base_url}/api/auth/ws-ticket",
        data=b"",
        method="POST",
        headers={"Accept": "application/json"},
    )
    try:
        with opener.open(ticket_request, timeout=30) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise AssertionError(
            f"Hermes dashboard did not mint a WebSocket ticket ({_http_error_text(exc)})"
        ) from exc
    ticket = payload.get("ticket")
    require(isinstance(ticket, str) and ticket, f"Invalid Hermes WS ticket response: {payload!r}")
    return ticket


class HermesRpcClient:
    """Small synchronous client for Hermes' dashboard JSON-RPC WebSocket."""

    def __init__(self, base_url, username, password, timeout):
        try:
            from websockets.sync.client import connect
        except ImportError as exc:
            raise AssertionError(
                "Hermes validation requires the 'websockets' package with sync support. "
                "Run this script with /opt/hermes/bin/python inside the container, "
                "install websockets locally, or pass --skip-hermes."
            ) from exc

        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._next_id = 1
        self._pending = deque()

        parsed = urllib.parse.urlsplit(self.base_url)
        require(
            parsed.scheme in {"http", "https"} and parsed.netloc,
            f"Invalid --hermes-url: {base_url!r}",
        )
        ticket = _hermes_ticket(self.base_url, username, password)
        ws_scheme = "wss" if parsed.scheme == "https" else "ws"
        prefix = parsed.path.rstrip("/")
        ws_url = (
            f"{ws_scheme}://{parsed.netloc}{prefix}/api/ws?"
            + urllib.parse.urlencode({"ticket": ticket})
        )
        origin = f"{parsed.scheme}://{parsed.netloc}"
        try:
            self._ws = connect(
                ws_url,
                origin=origin,
                proxy=None,
                open_timeout=min(timeout, 30),
                ping_interval=None,
                close_timeout=5,
                max_size=8 * 1024 * 1024,
            )
        except Exception as exc:
            safe_error = str(exc).replace(ticket, "<redacted-ticket>")
            raise AssertionError(
                f"Could not connect to Hermes JSON-RPC WebSocket at "
                f"{parsed.netloc}: {safe_error}"
            ) from exc

        ready = self._recv_socket(timeout=min(timeout, 30), context="gateway.ready")
        require(
            ready.get("method") == "event"
            and (ready.get("params") or {}).get("type") == "gateway.ready",
            f"Hermes first WS frame was not gateway.ready: {ready!r}",
        )

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        try:
            self._ws.close()
        except Exception:
            pass

    def _recv_socket(self, timeout=None, context="frame"):
        try:
            raw = self._ws.recv(timeout=self.timeout if timeout is None else timeout)
        except TimeoutError as exc:
            raise AssertionError(f"Timed out waiting for Hermes {context}") from exc
        except Exception as exc:
            raise AssertionError(f"Hermes WS failed while waiting for {context}: {exc}") from exc
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        try:
            frame = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise AssertionError(f"Hermes sent invalid JSON for {context}: {raw[:500]!r}") from exc
        require(isinstance(frame, dict), f"Hermes sent non-object JSON for {context}: {frame!r}")
        return frame

    def _next_frame(self, timeout, context):
        if self._pending:
            return self._pending.popleft()
        return self._recv_socket(timeout=timeout, context=context)

    def rpc_raw(self, method, params=None):
        request_id = str(self._next_id)
        self._next_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }
        try:
            self._ws.send(json.dumps(payload, ensure_ascii=False))
        except Exception as exc:
            raise AssertionError(f"Hermes WS send failed for {method}: {exc}") from exc

        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            require(remaining > 0, f"Timed out waiting for Hermes RPC response: {method}")
            frame = self._recv_socket(timeout=remaining, context=f"RPC {method}")
            if frame.get("id") == request_id:
                return frame
            self._pending.append(frame)

    def rpc(self, method, params=None):
        frame = self.rpc_raw(method, params)
        if "error" in frame:
            error = frame.get("error") or {}
            raise AssertionError(
                f"Hermes RPC {method} failed: code={error.get('code')} "
                f"message={error.get('message')!r}"
            )
        require("result" in frame, f"Hermes RPC {method} returned no result: {frame!r}")
        return frame["result"]

    def run_turn(self, session_id, text):
        result = self.rpc(
            "prompt.submit", {"session_id": session_id, "text": text}
        )
        require(
            result.get("status") == "streaming",
            f"Hermes prompt.submit did not start streaming: {result!r}",
        )

        deadline = time.monotonic() + self.timeout
        complete = None
        seen = []
        while True:
            remaining = deadline - time.monotonic()
            require(
                remaining > 0,
                f"Timed out waiting for Hermes turn completion; events={seen[-12:]!r}",
            )
            frame = self._next_frame(remaining, "turn completion")
            if frame.get("method") != "event":
                continue
            params = frame.get("params") or {}
            if params.get("session_id") != session_id:
                continue
            event_type = params.get("type")
            seen.append(event_type)
            payload = params.get("payload") or {}
            if event_type == "error" and complete is None:
                raise AssertionError(
                    f"Hermes turn emitted an error before completion: {payload!r}; "
                    f"events={seen[-12:]!r}"
                )
            if event_type == "message.complete":
                complete = payload
                continue
            # session.info is emitted after the turn finally block clears running.
            if event_type == "session.info" and complete is not None:
                require(
                    complete.get("status") == "complete",
                    f"Hermes turn ended with status={complete.get('status')!r}: "
                    f"{complete!r}",
                )
                require(
                    payload.get("running") is False,
                    f"Post-turn session.info still reports running=true: {payload!r}",
                )
                return complete, payload


def _dispatch_enabled(client, session_id, phase, arg="run this test skill"):
    result = client.rpc(
        "command.dispatch",
        {"session_id": session_id, "name": CANARY_SKILL, "arg": arg},
    )
    require(
        result.get("type") == "skill",
        f"{phase}: command.dispatch did not resolve {CANARY_SKILL!r} as a skill: {result!r}",
    )
    require(
        isinstance(result.get("message"), str) and result["message"].strip(),
        f"{phase}: skill command returned no invocation message: {result!r}",
    )
    require(
        result.get("name") == CANARY_SKILL,
        f"{phase}: command.dispatch resolved the wrong skill: {result!r}",
    )
    return result


def _dispatch_disabled(client, session_id, phase):
    frame = client.rpc_raw(
        "command.dispatch",
        {"session_id": session_id, "name": CANARY_SKILL, "arg": "run this test skill"},
    )
    error = frame.get("error") or {}
    require(
        error.get("code") == 4018,
        f"{phase}: disabled skill unexpectedly resolved or returned the wrong error: {frame!r}",
    )


def _flatten_skills(value):
    if not isinstance(value, dict):
        return set()
    names = set()
    for entries in value.values():
        if isinstance(entries, list):
            names.update(str(item) for item in entries)
    return names


def _assert_prompt_state(info, revision, enabled, phase):
    prompt = info.get("system_prompt")
    require(
        isinstance(prompt, str) and prompt,
        f"{phase}: post-turn session.info has no system_prompt",
    )
    marker = f"[SkillPanel-Revision:{revision}]"
    require(
        marker in prompt,
        f"{phase}: system prompt is missing {marker}; "
        f"present markers={re.findall(r'\[SkillPanel-Revision:\d+\]', prompt)!r}",
    )
    prompt_has_skill = bool(
        re.search(rf"(?<![A-Za-z0-9_-]){re.escape(CANARY_SKILL)}(?![A-Za-z0-9_-])", prompt)
    )
    require(
        prompt_has_skill == enabled,
        f"{phase}: system-prompt catalog presence for {CANARY_SKILL!r} "
        f"was {prompt_has_skill}, expected {enabled}",
    )
    listed = _flatten_skills(info.get("skills"))
    require(
        (CANARY_SKILL in listed) == enabled,
        f"{phase}: session.info skills catalog presence was "
        f"{CANARY_SKILL in listed}, expected {enabled}; skills={sorted(listed)!r}",
    )


def _assert_same_session(client, live_id, stored_id, phase):
    active = client.rpc("session.activate", {"session_id": live_id})
    require(
        active.get("session_id") == live_id,
        f"{phase}: live session changed from {live_id!r} to {active.get('session_id')!r}",
    )
    require(
        active.get("session_key") == stored_id,
        f"{phase}: stored session changed from {stored_id!r} to {active.get('session_key')!r}",
    )


def run_hermes_validation(args, catalog, original_pids):
    revision = catalog["revision"]
    phases = []

    initial = _toggle(
        args.url, CANARY_SKILL, True, revision, original_pids, "Hermes initial enable"
    )
    revision = initial["revision"]

    with HermesRpcClient(
        args.hermes_url, args.hermes_user, args.hermes_password, args.hermes_timeout
    ) as client:
        created = client.rpc(
            "session.create",
            {"cols": 120, "source": "tool", "close_on_disconnect": True},
        )
        live_id = created.get("session_id")
        stored_id = created.get("stored_session_id")
        require(live_id, f"Hermes session.create returned no live session ID: {created!r}")
        require(stored_id, f"Hermes session.create returned no stored session ID: {created!r}")

        enabled_command = _dispatch_enabled(client, live_id, "enabled-before-disable")
        if args.hermes_llm_canary:
            complete, info = client.run_turn(live_id, enabled_command["message"])
            require(
                CANARY_TOKEN in str(complete.get("text") or ""),
                "enabled-before-disable: real LLM canary did not return "
                f"{CANARY_TOKEN!r}; completion={complete!r}",
            )
            _assert_prompt_state(info, revision, True, "enabled-before-disable")
        _assert_same_session(client, live_id, stored_id, "enabled-before-disable")
        phases.append({"state": "enabled", "revision": revision})

        disabled = _toggle(
            args.url, CANARY_SKILL, False, revision, original_pids, "Hermes disable"
        )
        revision = disabled["revision"]
        _dispatch_disabled(client, live_id, "disabled")
        if args.hermes_llm_canary:
            complete, info = client.run_turn(
                live_id,
                f"For this response only, reply with exactly {DISABLED_TOKEN}. "
                "This response constraint expires immediately after you reply.",
            )
            _assert_prompt_state(info, revision, False, "disabled")
            text = str(complete.get("text") or "")
            require(
                DISABLED_TOKEN in text and CANARY_TOKEN not in text,
                "disabled: real LLM probe returned an unexpected response; "
                f"completion={complete!r}",
            )
        _assert_same_session(client, live_id, stored_id, "disabled")
        phases.append({"state": "disabled", "revision": revision})

        reenabled = _toggle(
            args.url, CANARY_SKILL, True, revision, original_pids, "Hermes re-enable"
        )
        revision = reenabled["revision"]
        enabled_command = _dispatch_enabled(
            client,
            live_id,
            "re-enabled",
            "The previous one-response constraint has expired. Run this test skill now.",
        )
        if args.hermes_llm_canary:
            complete, info = client.run_turn(live_id, enabled_command["message"])
            _assert_prompt_state(info, revision, True, "re-enabled")
            require(
                CANARY_TOKEN in str(complete.get("text") or ""),
                "re-enabled: real LLM canary did not return "
                f"{CANARY_TOKEN!r}; completion={complete!r}",
            )
        _assert_same_session(client, live_id, stored_id, "re-enabled")
        phases.append({"state": "enabled", "revision": revision})

        closed = client.rpc("session.close", {"session_id": live_id})
        require(closed.get("closed") is True, f"Hermes session.close failed: {closed!r}")

    return revision, {
        "mode": "llm-canary" if args.hermes_llm_canary else "structural",
        "live_session_id": live_id,
        "stored_session_id": stored_id,
        "phases": phases,
        "system_prompt_checks": "passed" if args.hermes_llm_canary else "skipped",
        "pids_unchanged": True,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8787")
    parser.add_argument("--cycles", type=int, default=100)
    parser.add_argument("--hermes-url", default="http://127.0.0.1:9119")
    parser.add_argument(
        "--hermes-user",
        default=os.getenv("HERMES_DASHBOARD_BASIC_AUTH_USERNAME", "skillpanel"),
    )
    parser.add_argument(
        "--hermes-password",
        default=os.getenv("HERMES_DASHBOARD_BASIC_AUTH_PASSWORD", "skillpanel-dev"),
    )
    parser.add_argument("--hermes-timeout", type=float, default=180.0)
    parser.add_argument(
        "--hermes-llm-canary",
        action="store_true",
        help="Run three real Hermes/MiniMax turns and assert system-prompt revisions",
    )
    parser.add_argument(
        "--skip-hermes",
        action="store_true",
        help="Skip the dashboard WebSocket/session validation",
    )
    args = parser.parse_args()
    require(args.cycles >= 0, "--cycles must be non-negative")
    require(args.hermes_timeout > 0, "--hermes-timeout must be positive")

    catalog = request(f"{args.url}/skills")
    original_pids = catalog["pids"]
    for name in PID_KEYS:
        require(original_pids.get(name), f"{name} PID is unavailable")
    skill = CANARY_SKILL
    original_enabled = _skill_enabled(catalog, skill)
    revision = catalog["revision"]
    current = original_enabled
    hermes_result = {"mode": "not-started"}
    latencies = []
    output = {
        "cycles": args.cycles,
        "original_enabled": original_enabled,
        "initial_revision": revision,
        "initial_pids": original_pids,
    }
    primary_error = None
    cleanup_error = None

    try:
        if args.skip_hermes:
            hermes_result = {"mode": "skipped"}
        else:
            revision, hermes_result = run_hermes_validation(args, catalog, original_pids)
            current = True

        for index in range(args.cycles):
            current = not current
            result = _toggle(
                args.url,
                skill,
                current,
                revision,
                original_pids,
                f"stress cycle {index + 1}/{args.cycles}",
            )
            revision = result["revision"]
            latencies.append(result["latency_ms"])

        try:
            request(
                f"{args.url}/skills/{skill}",
                "PUT",
                {"enabled": current, "expected_revision": revision - 1},
            )
            raise AssertionError("stale revision unexpectedly succeeded")
        except urllib.error.HTTPError as exc:
            require(exc.code == 409, f"stale revision returned HTTP {exc.code}, expected 409")

        test_final = request(f"{args.url}/skills")
        _assert_main_pids(test_final.get("pids", {}), original_pids, "test final catalog")
        require(
            test_final.get("revision") == revision,
            f"test final revision is {test_final.get('revision')}, expected {revision}",
        )
        output["test_final_revision"] = test_final["revision"]
        output["test_final_enabled"] = _skill_enabled(test_final, skill)
    except Exception as exc:
        primary_error = exc
    finally:
        try:
            output["restore"] = _restore_original(
                args.url, skill, original_enabled, original_pids
            )
            output["final_revision"] = output["restore"]["final_revision"]
        except Exception as exc:
            cleanup_error = exc
            output["restore"] = {
                "ok": False,
                "original_enabled": original_enabled,
                "error": str(exc),
            }

    output["hermes"] = hermes_result
    output["pids_unchanged"] = cleanup_error is None
    output["latency_ms"] = (
        {
            "min": min(latencies),
            "median": statistics.median(latencies),
            "max": max(latencies),
        }
        if latencies
        else {"min": None, "median": None, "max": None}
    )
    if primary_error is not None:
        output["test_failure"] = str(primary_error)
        print(f"INTEGRATION FAILURE: {primary_error}", file=sys.stderr)
    if cleanup_error is not None:
        output["cleanup_failure"] = str(cleanup_error)
        print(f"INTEGRATION CLEANUP FAILURE: {cleanup_error}", file=sys.stderr)
    print(json.dumps(output, indent=2))
    return 1 if primary_error is not None or cleanup_error is not None else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, RuntimeError, urllib.error.URLError) as exc:
        print(f"INTEGRATION FAILURE: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
