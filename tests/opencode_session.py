#!/usr/bin/env python3
"""Black-box OpenCode same-session skill hot-reload test using MiniMax."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


PID_KEYS = ("controller", "opencode", "hermes")
NOT_FOUND_MARKERS = ("not found", "not available", "unknown skill", "available skills")


class TestFailure(RuntimeError):
    """An assertion or protocol failure safe to include in the JSON summary."""


class HttpStatusError(RuntimeError):
    """HTTP failure that retains a parsed body without printing it."""

    def __init__(self, status: int, method: str, path: str, payload: Any):
        super().__init__(f"HTTP {status} from {method} {path}")
        self.status = status
        self.payload = payload


def require(condition: bool, message: str) -> None:
    if not condition:
        raise TestFailure(message)


def as_mapping(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TestFailure(f"{context} did not return a JSON object")
    return value


def as_list(value: Any, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise TestFailure(f"{context} did not return a JSON array")
    return value


def parse_json(raw: bytes, context: str) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TestFailure(f"{context} returned invalid JSON") from exc


@dataclass(frozen=True)
class HttpClient:
    base_url: str
    timeout: float

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: Any = None,
        query: dict[str, str] | None = None,
    ) -> Any:
        url = f"{self.base_url.rstrip('/')}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return parse_json(response.read(), f"{method} {path}")
        except urllib.error.HTTPError as exc:
            try:
                error_payload = parse_json(exc.read(), f"HTTP {exc.code}")
            except TestFailure:
                error_payload = None
            raise HttpStatusError(exc.code, method, path, error_payload) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TestFailure(f"{method} {path} transport failed ({type(exc).__name__})") from exc


def public_endpoint(url: str) -> str:
    """Strip possible URL userinfo before including an endpoint in output."""

    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urllib.parse.urlunsplit((parsed.scheme, host, parsed.path.rstrip("/"), "", ""))


def controller_catalog(controller: HttpClient) -> dict[str, Any]:
    catalog = as_mapping(controller.request("/skills"), "GET /skills")
    require(type(catalog.get("revision")) is int, "controller revision is not an integer")
    as_list(catalog.get("skills"), "controller skills")
    as_mapping(catalog.get("pids"), "controller pids")
    return catalog


def skill_enabled(catalog: dict[str, Any], name: str) -> bool:
    for item_raw in as_list(catalog["skills"], "controller skills"):
        item = as_mapping(item_raw, "controller skill")
        if item.get("name") != name:
            continue
        enabled = item.get("enabled")
        require(type(enabled) is bool, f"controller skill {name!r} has invalid enabled state")
        return enabled
    raise TestFailure(f"controller does not know skill {name!r}")


def pid_snapshot(payload: dict[str, Any], context: str) -> dict[str, int]:
    pids = as_mapping(payload.get("pids"), f"{context} pids")
    result: dict[str, int] = {}
    for key in PID_KEYS:
        value = pids.get(key)
        require(type(value) is int and value > 0, f"{context} has no live {key} pid")
        result[key] = value
    return result


def assert_pids(payload: dict[str, Any], expected: dict[str, int], context: str) -> None:
    actual = pid_snapshot(payload, context)
    require(actual == expected, f"{context} changed pids: expected {expected}, got {actual}")


def set_skill_enabled(
    controller: HttpClient,
    name: str,
    enabled: bool,
    expected_pids: dict[str, int] | None = None,
) -> dict[str, Any]:
    for _ in range(3):
        catalog = controller_catalog(controller)
        skill_enabled(catalog, name)
        revision = catalog["revision"]
        try:
            result = as_mapping(
                controller.request(
                    f"/skills/{urllib.parse.quote(name, safe='')}",
                    method="PUT",
                    payload={"enabled": enabled, "expected_revision": revision},
                ),
                "PUT /skills/{name}",
            )
        except HttpStatusError as exc:
            if exc.status == 409:
                continue
            raise
        require(result.get("ok") is True, "controller toggle did not report ok")
        require(result.get("enabled") is enabled, "controller toggle returned the wrong target state")
        require(type(result.get("revision")) is int, "controller toggle returned an invalid revision")
        if expected_pids is not None:
            assert_pids(result, expected_pids, "controller toggle")
        return result
    raise TestFailure("controller revision conflicted three consecutive times")


def opencode_skill_names(opencode: HttpClient, directory: str) -> set[str]:
    payload = as_list(
        opencode.request("/skill", query={"directory": directory}),
        "GET /skill",
    )
    names: set[str] = set()
    for item_raw in payload:
        item = as_mapping(item_raw, "OpenCode skill")
        name = item.get("name")
        if isinstance(name, str):
            names.add(name)
    return names


def assert_minimax_available(
    opencode: HttpClient,
    directory: str,
    provider: str,
    model: str,
) -> None:
    payload = as_mapping(
        opencode.request("/provider", query={"directory": directory}),
        "GET /provider",
    )
    connected = as_list(payload.get("connected"), "connected providers")
    require(provider in connected, f"MiniMax provider {provider!r} is not connected")

    providers = as_list(payload.get("all"), "provider catalog")
    match: dict[str, Any] | None = None
    for provider_raw in providers:
        item = as_mapping(provider_raw, "provider catalog item")
        if item.get("id") == provider:
            match = item
            break
    require(match is not None, f"provider catalog has no {provider!r}")

    models = match.get("models")
    if isinstance(models, dict):
        available = set(models)
    elif isinstance(models, list):
        available = {
            item.get("id")
            for raw in models
            if isinstance(raw, dict)
            for item in [raw]
            if isinstance(item.get("id"), str)
        }
    else:
        raise TestFailure(f"provider {provider!r} returned an invalid model catalog")
    require(model in available, f"MiniMax model {model!r} is not available from {provider!r}")


def create_session(
    opencode: HttpClient,
    directory: str,
    provider: str,
    model: str,
    agent: str,
) -> str:
    payload = {
        "title": f"skillpanel-hot-reload-{int(time.time())}",
        "agent": agent,
        "model": {"id": model, "providerID": provider},
        "permission": [{"permission": "skill", "pattern": "*", "action": "allow"}],
    }
    session = as_mapping(
        opencode.request(
            "/session",
            method="POST",
            payload=payload,
            query={"directory": directory},
        ),
        "POST /session",
    )
    session_id = session.get("id")
    require(isinstance(session_id, str) and session_id.startswith("ses"), "invalid session id")
    return session_id


def get_session(opencode: HttpClient, directory: str, session_id: str) -> dict[str, Any]:
    session = as_mapping(
        opencode.request(
            f"/session/{urllib.parse.quote(session_id, safe='')}",
            query={"directory": directory},
        ),
        "GET /session/{id}",
    )
    require(session.get("id") == session_id, "OpenCode returned a different session id")
    return session


def get_messages(opencode: HttpClient, directory: str, session_id: str) -> list[dict[str, Any]]:
    payload = as_list(
        opencode.request(
            f"/session/{urllib.parse.quote(session_id, safe='')}/message",
            query={"directory": directory},
        ),
        "GET /session/{id}/message",
    )
    return [as_mapping(item, "OpenCode message") for item in payload]


def message_id(message: dict[str, Any]) -> str:
    info = as_mapping(message.get("info"), "message info")
    value = info.get("id")
    require(isinstance(value, str), "message has no id")
    return value


def assert_message_session(message: dict[str, Any], session_id: str) -> None:
    info = as_mapping(message.get("info"), "message info")
    require(info.get("sessionID") == session_id, "message belongs to a different session")
    for part_raw in as_list(message.get("parts"), "message parts"):
        part = as_mapping(part_raw, "message part")
        if "sessionID" in part:
            require(part["sessionID"] == session_id, "message part belongs to a different session")


def round_prompt(skill: str, enabled: bool) -> str:
    arguments = json.dumps({"name": skill}, separators=(",", ":"))
    if enabled:
        return (
            "This is an automated black-box protocol check. You MUST invoke the skill tool "
            f"exactly once with {arguments} before answering. Do not infer its content before "
            "the tool returns. If it succeeds, reply only with the exact canary token contained "
            "in the loaded skill. If it fails, reply only SKILL_TOOL_ERROR."
        )
    return (
        "This is an automated black-box protocol check. You MUST invoke the skill tool "
        f"exactly once with {arguments}, even if that skill is absent from available skills. "
        "Do not answer before making the tool call. If it errors, reply only SKILL_NOT_FOUND. "
        "Do not repeat any canary token from an earlier turn."
    )


def run_round(
    opencode: HttpClient,
    *,
    directory: str,
    session_id: str,
    provider: str,
    model: str,
    agent: str,
    skill: str,
    canary: str,
    enabled: bool,
) -> dict[str, Any]:
    before = get_messages(opencode, directory, session_id)
    before_ids = {message_id(message) for message in before}
    response = as_mapping(
        opencode.request(
            f"/session/{urllib.parse.quote(session_id, safe='')}/message",
            method="POST",
            query={"directory": directory},
            payload={
                "model": {"providerID": provider, "modelID": model},
                "agent": agent,
                "parts": [{"type": "text", "text": round_prompt(skill, enabled)}],
            },
        ),
        "POST /session/{id}/message",
    )
    response_info = as_mapping(response.get("info"), "prompt response info")
    require(response_info.get("sessionID") == session_id, "prompt response changed session id")

    after = get_messages(opencode, directory, session_id)
    new_messages = [message for message in after if message_id(message) not in before_ids]
    require(new_messages, "prompt created no new messages")
    for message in new_messages:
        assert_message_session(message, session_id)

    assistant_messages = [
        message
        for message in new_messages
        if as_mapping(message.get("info"), "message info").get("role") == "assistant"
    ]
    require(assistant_messages, "prompt created no assistant messages")
    for message in assistant_messages:
        info = as_mapping(message["info"], "assistant info")
        require(info.get("providerID") == provider, "assistant used a different provider")
        require(info.get("modelID") == model, "assistant used a different model")

    tool_parts: list[dict[str, Any]] = []
    assistant_texts: list[str] = []
    for message in assistant_messages:
        for part_raw in as_list(message.get("parts"), "assistant parts"):
            part = as_mapping(part_raw, "assistant part")
            if part.get("type") == "text" and isinstance(part.get("text"), str):
                assistant_texts.append(part["text"])
            if part.get("type") != "tool" or part.get("tool") != "skill":
                continue
            state = as_mapping(part.get("state"), "skill tool state")
            tool_input = as_mapping(state.get("input"), "skill tool input")
            if tool_input.get("name") == skill:
                tool_parts.append(part)

    require(tool_parts, f"assistant did not call skill tool for {skill!r}")
    states = [as_mapping(part["state"], "skill tool state") for part in tool_parts]
    statuses = [state.get("status") for state in states]
    assistant_text = "\n".join(assistant_texts)

    if enabled:
        require(all(status == "completed" for status in statuses), "enabled skill tool did not complete")
        canary_in_tool = any(canary in str(state.get("output", "")) for state in states)
        require(canary_in_tool, "enabled skill tool output did not contain the canary")
        require(canary in assistant_text, "enabled assistant response did not contain the canary")
        not_found = False
    else:
        require(all(status == "error" for status in statuses), "disabled skill tool did not error")
        errors = "\n".join(str(state.get("error", "")) for state in states).lower()
        not_found = any(marker in errors for marker in NOT_FOUND_MARKERS)
        require(not_found, "disabled skill tool error was not a not-found error")
        require(assistant_text.strip() != "", "disabled assistant produced no text response")
        require(canary not in assistant_text, "disabled assistant repeated the prior canary")
        canary_in_tool = False

    return {
        "session_id_unchanged": True,
        "new_message_ids": [message_id(message) for message in new_messages],
        "assistant_message_ids": [message_id(message) for message in assistant_messages],
        "skill_tool_calls": len(tool_parts),
        "skill_tool_statuses": statuses,
        "canary_in_tool_output": canary_in_tool,
        "canary_in_assistant": canary in assistant_text,
        "not_found_error": not_found,
    }


def assert_stage_state(
    controller: HttpClient,
    opencode: HttpClient,
    *,
    directory: str,
    session_id: str,
    skill: str,
    enabled: bool,
    expected_pids: dict[str, int],
) -> dict[str, Any]:
    catalog = controller_catalog(controller)
    assert_pids(catalog, expected_pids, "stage catalog")
    require(skill_enabled(catalog, skill) is enabled, "controller has the wrong skill state")
    names = opencode_skill_names(opencode, directory)
    require((skill in names) is enabled, "OpenCode catalog has the wrong skill state")
    get_session(opencode, directory, session_id)
    return {
        "revision": catalog["revision"],
        "enabled": enabled,
        "pids_unchanged": True,
        "session_id_unchanged": True,
        "opencode_catalog_matches": True,
    }


def safe_error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, HttpStatusError):
        return {"type": type(exc).__name__, "status": exc.status, "message": str(exc)}
    if isinstance(exc, TestFailure):
        return {"type": type(exc).__name__, "message": str(exc)}
    return {"type": type(exc).__name__, "message": "unexpected test failure"}


def execute(args: argparse.Namespace) -> dict[str, Any]:
    started = time.monotonic()
    controller = HttpClient(args.controller_url, args.timeout)
    opencode = HttpClient(args.opencode_url, args.timeout)
    summary: dict[str, Any] = {
        "ok": False,
        "endpoints": {
            "controller": public_endpoint(args.controller_url),
            "opencode": public_endpoint(args.opencode_url),
        },
        "provider": args.provider,
        "model": args.model,
        "skill": args.skill,
        "stages": [],
    }
    baseline_pids: dict[str, int] | None = None
    original_enabled: bool | None = None
    session_id: str | None = None
    primary_error: dict[str, Any] | None = None
    restore_error: dict[str, Any] | None = None

    try:
        initial_catalog = controller_catalog(controller)
        original_enabled = skill_enabled(initial_catalog, args.skill)
        baseline_pids = pid_snapshot(initial_catalog, "initial catalog")
        summary["original_enabled"] = original_enabled
        summary["pids"] = baseline_pids

        health = as_mapping(opencode.request("/global/health"), "GET /global/health")
        require(health.get("healthy") is True, "OpenCode is not healthy")
        summary["opencode_version"] = health.get("version")
        assert_minimax_available(opencode, args.directory, args.provider, args.model)

        set_skill_enabled(controller, args.skill, True, baseline_pids)
        require(args.skill in opencode_skill_names(opencode, args.directory), "initial skill is not enabled")

        session_id = create_session(
            opencode,
            args.directory,
            args.provider,
            args.model,
            args.agent,
        )
        summary["session_id"] = session_id
        get_session(opencode, args.directory, session_id)

        enabled_round = run_round(
            opencode,
            directory=args.directory,
            session_id=session_id,
            provider=args.provider,
            model=args.model,
            agent=args.agent,
            skill=args.skill,
            canary=args.canary,
            enabled=True,
        )
        enabled_state = assert_stage_state(
            controller,
            opencode,
            directory=args.directory,
            session_id=session_id,
            skill=args.skill,
            enabled=True,
            expected_pids=baseline_pids,
        )
        summary["stages"].append({"name": "enabled", **enabled_state, **enabled_round})

        set_skill_enabled(controller, args.skill, False, baseline_pids)
        disabled_round = run_round(
            opencode,
            directory=args.directory,
            session_id=session_id,
            provider=args.provider,
            model=args.model,
            agent=args.agent,
            skill=args.skill,
            canary=args.canary,
            enabled=False,
        )
        disabled_state = assert_stage_state(
            controller,
            opencode,
            directory=args.directory,
            session_id=session_id,
            skill=args.skill,
            enabled=False,
            expected_pids=baseline_pids,
        )
        summary["stages"].append({"name": "disabled", **disabled_state, **disabled_round})

        set_skill_enabled(controller, args.skill, True, baseline_pids)
        reenabled_round = run_round(
            opencode,
            directory=args.directory,
            session_id=session_id,
            provider=args.provider,
            model=args.model,
            agent=args.agent,
            skill=args.skill,
            canary=args.canary,
            enabled=True,
        )
        reenabled_state = assert_stage_state(
            controller,
            opencode,
            directory=args.directory,
            session_id=session_id,
            skill=args.skill,
            enabled=True,
            expected_pids=baseline_pids,
        )
        summary["stages"].append({"name": "reenabled", **reenabled_state, **reenabled_round})
    except Exception as exc:  # The JSON summary intentionally replaces a traceback.
        primary_error = safe_error(exc)
    finally:
        if original_enabled is None:
            summary["restore"] = {
                "ok": False,
                "attempted": False,
                "reason": "original state was not available before the test failed",
            }
        else:
            try:
                # This helper fetches the latest catalog before every PUT and
                # retries revision conflicts, so cleanup never reuses a stale
                # test-stage revision.
                restored = set_skill_enabled(
                    controller, args.skill, original_enabled, baseline_pids
                )
                final_catalog = controller_catalog(controller)
                final_enabled = skill_enabled(final_catalog, args.skill)
                require(
                    final_enabled is original_enabled,
                    f"final skill state is {final_enabled}, expected {original_enabled}",
                )
                require(
                    final_catalog["revision"] == restored["revision"],
                    "final catalog revision differs from cleanup PUT revision",
                )
                if baseline_pids is not None:
                    assert_pids(final_catalog, baseline_pids, "final catalog")
                final_names = opencode_skill_names(opencode, args.directory)
                require(
                    (args.skill in final_names) is original_enabled,
                    "final OpenCode catalog does not match the original skill state",
                )
                if session_id is not None:
                    get_session(opencode, args.directory, session_id)
                summary["restore"] = {
                    "ok": True,
                    "original_enabled": original_enabled,
                    "final_enabled": final_enabled,
                    "put_changed": restored.get("changed"),
                    "put_revision": restored["revision"],
                    "final_revision": final_catalog["revision"],
                    "controller_state_matches": True,
                    "opencode_catalog_matches": True,
                    "pids_unchanged": baseline_pids is not None,
                    "session_id_unchanged": session_id is not None,
                }
            except Exception as exc:  # Preserve the primary failure; report cleanup separately.
                restore_error = safe_error(exc)
                summary["restore"] = {
                    "ok": False,
                    "attempted": True,
                    "original_enabled": original_enabled,
                    "error": restore_error,
                }

    if primary_error is not None:
        summary["error"] = primary_error
    if restore_error is not None:
        summary["cleanup_error"] = restore_error
        if primary_error is None:
            summary["error"] = restore_error
    summary["ok"] = primary_error is None and restore_error is None
    summary["duration_ms"] = round((time.monotonic() - started) * 1000, 2)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller-url", default="http://127.0.0.1:8787")
    parser.add_argument("--opencode-url", default="http://127.0.0.1:4096")
    parser.add_argument("--timeout", type=float, default=180.0, help="per-request timeout in seconds")
    parser.add_argument("--directory", default="/workspace")
    parser.add_argument("--provider", default="minimax-cn-coding-plan")
    parser.add_argument("--model", default="MiniMax-M2.7")
    parser.add_argument("--agent", default="build")
    parser.add_argument("--skill", default="canary-alpha")
    parser.add_argument("--canary", default="SKILLPANEL_ALPHA_7F3A")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    return args


def main() -> int:
    summary = execute(parse_args())
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
