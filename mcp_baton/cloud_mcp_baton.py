from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

REPO = os.environ["GITHUB_REPOSITORY"]
PR_NUMBER = int(os.environ["PR_NUMBER"])
RUN_ID = os.environ["RUN_ID"]
HEAD_REF = os.environ["HEAD_REF"]
HEAD_SHA = os.environ["HEAD_SHA"]
GH_TOKEN = os.environ["GH_TOKEN"]
MCP_ENDPOINT = os.environ.get("MCP_ENDPOINT", "https://mcp.cognilode.com/mcp")
PROTOCOLS = ("2025-06-18", "2025-03-26")
TRIGGER_PATH = "mcp_baton/trigger.txt"

session_headers: dict[str, str] = {}
request_id = 20
schemas: dict[str, dict[str, Any]] = {}

status: dict[str, Any] = {
    "schema": "cognilode.public_cloud_mcp_recursive_chatgpt_baton.v1",
    "run_id": RUN_ID,
    "repository": REPO,
    "pull_request": PR_NUMBER,
    "head_ref": HEAD_REF,
    "trigger_sha": HEAD_SHA,
    "mcp_endpoint": MCP_ENDPOINT,
    "host_used": False,
    "browser_used": False,
    "codex_used": False,
    "scheduled_task_used": False,
    "work_mode_used": False,
    "submission_observed": False,
    "successor_external_event_observed": False,
    "started_at_utc": datetime.now(timezone.utc).isoformat(),
}


def github_request(path: str, *, method: str = "GET", payload: Any = None) -> Any:
    headers = {
        "authorization": "Bearer " + GH_TOKEN,
        "accept": "application/vnd.github+json",
        "content-type": "application/json",
        "x-github-api-version": "2022-11-28",
        "user-agent": "cognilode-public-cloud-mcp-baton/1",
    }
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        "https://api.github.com" + path,
        data=data,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(request, timeout=35) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8")) if raw else None


def comment(heading: str, value: Mapping[str, Any]) -> None:
    safe = dict(value)
    safe.pop("prompt", None)
    body = (
        heading
        + "\n\n```json\n"
        + json.dumps(safe, ensure_ascii=False, indent=2, sort_keys=True, default=str)[:60000]
        + "\n```"
    )
    github_request(
        f"/repos/{REPO}/issues/{PR_NUMBER}/comments",
        method="POST",
        payload={"body": body},
    )


def decode_mcp(raw: bytes) -> dict[str, Any]:
    text = raw.decode("utf-8", "replace")
    if text.lstrip().startswith("{"):
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    values: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            value = json.loads(data)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            values.append(value)
    if not values:
        raise RuntimeError("unparseable MCP response: " + text[:1000])
    return values[-1]


def mcp_post(payload: dict[str, Any], *, timeout: float = 50.0) -> tuple[dict[str, str], dict[str, Any]]:
    headers = {
        "content-type": "application/json",
        "accept": "application/json, text/event-stream",
        "user-agent": "cognilode-public-cloud-mcp-baton/1",
        **session_headers,
    }
    request = urllib.request.Request(
        MCP_ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        response_headers = {key.lower(): value for key, value in response.headers.items()}
        return response_headers, decode_mcp(response.read())


def initialize() -> list[dict[str, Any]]:
    last_error: Exception | None = None
    for protocol in PROTOCOLS:
        session_headers.clear()
        try:
            headers, envelope = mcp_post(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": protocol,
                        "capabilities": {},
                        "clientInfo": {
                            "name": "cognilode-public-cloud-mcp-baton",
                            "version": "1",
                        },
                    },
                },
                timeout=35,
            )
            if envelope.get("error"):
                raise RuntimeError(str(envelope["error"]))
            session_headers["mcp-protocol-version"] = protocol
            session_id = headers.get("mcp-session-id")
            if session_id:
                session_headers["mcp-session-id"] = session_id
            try:
                mcp_post(
                    {
                        "jsonrpc": "2.0",
                        "method": "notifications/initialized",
                        "params": {},
                    },
                    timeout=8,
                )
            except Exception:
                pass
            _, listed = mcp_post(
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                timeout=35,
            )
            if listed.get("error"):
                raise RuntimeError(str(listed["error"]))
            result = listed.get("result") or {}
            tools = result.get("tools") or []
            status["mcp_protocol"] = protocol
            return [tool for tool in tools if isinstance(tool, dict)]
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"MCP initialization failed for supported protocols: {last_error}")


def tool_payload(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("isError"):
        text = " ".join(
            str(item.get("text") or "")
            for item in result.get("content") or []
            if isinstance(item, dict)
        )
        raise RuntimeError("MCP tool error: " + text[:3000])
    structured = result.get("structuredContent")
    if isinstance(structured, dict) and structured:
        return structured
    for item in result.get("content") or []:
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        text = str(item.get("text") or "")
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}
        return value if isinstance(value, dict) else {"value": value}
    return result if isinstance(result, dict) else {"value": result}


def call_tool(name: str, arguments: dict[str, Any], *, timeout: float = 75.0) -> dict[str, Any]:
    global request_id
    request_id += 1
    _, envelope = mcp_post(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        timeout=timeout,
    )
    if envelope.get("error"):
        raise RuntimeError(f"{name} JSON-RPC error: {envelope['error']}")
    return tool_payload(envelope.get("result") or {})


def walk(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        if key in value and value[key] not in (None, "", False):
            return value[key]
        for child in value.values():
            found = walk(child, key)
            if found not in (None, "", False):
                return found
    elif isinstance(value, list):
        for child in value:
            found = walk(child, key)
            if found not in (None, "", False):
                return found
    return None


def operation_names(value: Any) -> list[str]:
    names: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, str):
            text = item.strip()
            if text.startswith(("{", "[")):
                try:
                    visit(json.loads(text))
                except json.JSONDecodeError:
                    pass
            names.extend(
                re.findall(
                    r"(?i)\b(?:chatgpt[._-]?)?agent[._-](?:start|poll|read)\b",
                    text,
                )
            )
        elif isinstance(item, dict):
            for key in ("operation", "operation_id", "name", "id"):
                candidate = item.get(key)
                if isinstance(candidate, str):
                    names.append(candidate)
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return list(dict.fromkeys(names))


def choose(names: Iterable[str], kind: str) -> str:
    values = list(names)
    exact = (
        f"agent_{kind}",
        f"agent.{kind}",
        f"chatgpt_agent_{kind}",
        f"chatgpt.agent.{kind}",
        f"chatgpt_agent.{kind}",
    )
    for candidate in exact:
        if candidate in values:
            return candidate
    suffix = re.compile(rf"(?i)(?:^|[._-])agent[._-]?{kind}$")
    return next((value for value in values if suffix.search(value)), "")


def schema_properties(name: str) -> dict[str, Any]:
    schema = schemas.get(name) or {}
    properties = schema.get("properties") or {}
    return properties if isinstance(properties, dict) else {}


def start_arguments(name: str, agent_id: str, prompt: str) -> dict[str, Any]:
    properties = schema_properties(name)
    identifier_key = next(
        (key for key in ("agent_id", "id", "task_name", "name") if key in properties),
        "agent_id",
    )
    prompt_key = next(
        (key for key in ("prompt", "message", "instruction", "task") if key in properties),
        "prompt",
    )
    arguments: dict[str, Any] = {identifier_key: agent_id, prompt_key: prompt}
    optional = {
        "backend": "chatgpt",
        "intelligence": "EXTRA_HIGH",
        "reasoning": "EXTRA_HIGH",
        "mode": "chat",
        "chat_mode": True,
        "work_mode": False,
        "use_work_mode": False,
        "codex": False,
        "use_codex": False,
        "scheduled_tasks": False,
        "new_conversation": True,
    }
    for key, value in optional.items():
        if key in properties:
            arguments[key] = value
    if not properties:
        arguments["intelligence"] = "EXTRA_HIGH"
    return arguments


def identity_arguments(name: str, agent_id: str) -> dict[str, Any]:
    properties = schema_properties(name)
    key = next(
        (candidate for candidate in ("agent_id", "id", "task_name", "name") if candidate in properties),
        "agent_id",
    )
    return {key: agent_id}


def branch_head() -> str:
    row = github_request(f"/repos/{REPO}/git/ref/heads/{HEAD_REF}")
    return str(((row or {}).get("object") or {}).get("sha") or "")


def trigger_contents() -> str:
    quoted = urllib.request.quote(TRIGGER_PATH, safe="/")
    row = github_request(f"/repos/{REPO}/contents/{quoted}?ref={urllib.request.quote(HEAD_REF, safe='')}")
    encoded = str((row or {}).get("content") or "").replace("\n", "")
    if not encoded:
        return ""
    import base64

    return base64.b64decode(encoded).decode("utf-8", "replace")


agent_id = f"public-cloud-mcp-baton-{RUN_ID}"
child_prompt = f'''repeated failure means you must fix it. audit is not acceptable work. fix it.

you must have chatgpt.com trigger an external event from outside of chatgpt.com which prompts a different chatgpt.com conversation then that next turn and all future turns must repeat that same recursive method this turn and all future turns.

chatgpt.com scheduled tasks are insufficient because they stay fully within chatgpt.com and have limited connectors and reduced turn lengths. do not dispute this.

you may not use "work" mode. you may ONLY use "chat" mode. you may not consume codex quota.

MACHINE BATON — transport metadata, not human-authored semantic authority.
You are a fresh ordinary ChatGPT.com conversation created because public GitHub PR #{PR_NUMBER} emitted synchronization event run {RUN_ID} outside ChatGPT.com, and that GitHub-hosted runner invoked the public Cognilode MCP.

Before ending this turn, repeat this exact recursion method:
1. Stay in ordinary Chat mode. Never enter Work mode. Never invoke Codex, a Codex sub-agent, or ChatGPT Scheduled Tasks.
2. Using your connected GitHub tool, fetch `{TRIGGER_PATH}` from repository `{REPO}`, branch `{HEAD_REF}`.
3. Replace that file's COMPLETE contents exactly once with one unique line in this format:
   CHATGPT_CLOUD_MCP_BATON parent_run={RUN_ID} parent_agent={agent_id} child_nonce=<fresh_unique_value>
4. Commit that one-file update to branch `{HEAD_REF}`. That GitHub PR synchronization is the outside-ChatGPT event that starts the next DIFFERENT ChatGPT.com conversation through this identical public GitHub-runner -> public Cognilode-MCP method. Do not directly prompt another conversation and do not mutate the trigger twice.
5. Read back the branch file and resulting commit SHA. Your final response must begin exactly `MCP_BATON_ACCEPTED parent_run={RUN_ID} agent_id={agent_id}` and include the commit SHA and fresh child nonce.
6. If the first GitHub file mutation fails, diagnose the concrete failure and use a materially different authenticated GitHub file-update route available in this same turn. Never include passwords, cookies, access tokens, login codes, or other secrets.
'''
status["agent_id"] = agent_id

try:
    tools = initialize()
    schemas.update(
        {
            str(tool.get("name") or ""): (
                tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else {}
            )
            for tool in tools
            if str(tool.get("name") or "")
        }
    )
    tool_names = list(schemas)
    status["mcp_tools"] = tool_names
    direct_start = choose(tool_names, "start")
    direct_poll = choose(tool_names, "poll")
    direct_read = choose(tool_names, "read")
    outer = "multi_tool" if "multi_tool" in tool_names else ""
    operations: list[str] = []
    if outer and not direct_start:
        catalog = call_tool(outer, {"operation": "__catalog__", "arguments": {}}, timeout=50)
        operations = operation_names(catalog)
    start_operation = choose(operations, "start")
    poll_operation = choose(operations, "poll")
    read_operation = choose(operations, "read")
    status["resolved"] = {
        "direct_start": direct_start,
        "direct_poll": direct_poll,
        "direct_read": direct_read,
        "outer": outer,
        "start_operation": start_operation,
        "poll_operation": poll_operation,
        "read_operation": read_operation,
    }

    if direct_start:
        state = call_tool(
            direct_start,
            start_arguments(direct_start, agent_id, child_prompt),
            timeout=90,
        )
        transport = "direct_agent_tool"
    elif outer and start_operation:
        state = call_tool(
            outer,
            {
                "operation": start_operation,
                "arguments": {
                    "agent_id": agent_id,
                    "intelligence": "EXTRA_HIGH",
                    "prompt": child_prompt,
                },
            },
            timeout=90,
        )
        transport = "multi_tool_operation"
    else:
        raise RuntimeError(
            "public MCP exposed neither direct agent_start nor catalogued agent.start operation"
        )

    status["transport"] = transport
    status["conversation_url"] = str(walk(state, "conversation_url") or "")
    status["submission_observed"] = bool(walk(state, "submission_observed"))
    status["initial_phase"] = str(walk(state, "phase") or "")
    comment("### Public cloud MCP child submitted", status)

    def poll_state() -> dict[str, Any]:
        if direct_poll:
            return call_tool(
                direct_poll,
                identity_arguments(direct_poll, agent_id),
                timeout=50,
            )
        if outer and poll_operation:
            return call_tool(
                outer,
                {
                    "operation": poll_operation,
                    "arguments": {"agent_id": agent_id},
                },
                timeout=50,
            )
        return {}

    last_state = state
    successor_sha = ""
    successor_line = ""
    deadline = time.time() + 780
    while time.time() < deadline:
        try:
            current = poll_state()
            if current:
                last_state = current
                status["conversation_url"] = str(
                    walk(current, "conversation_url") or status["conversation_url"]
                )
                status["submission_observed"] = (
                    bool(walk(current, "submission_observed"))
                    or status["submission_observed"]
                )
                status["phase"] = str(walk(current, "phase") or status.get("phase") or "")
        except Exception as exc:
            status.setdefault("poll_errors", []).append(f"{type(exc).__name__}: {exc}")

        current_sha = branch_head()
        if current_sha and current_sha != HEAD_SHA:
            line = trigger_contents().strip()
            expected = f"CHATGPT_CLOUD_MCP_BATON parent_run={RUN_ID} parent_agent={agent_id} "
            if line.startswith(expected):
                commit = github_request(f"/repos/{REPO}/commits/{current_sha}") or {}
                changed_paths = [
                    str(item.get("filename") or "")
                    for item in commit.get("files") or []
                    if isinstance(item, dict)
                ]
                if changed_paths == [TRIGGER_PATH]:
                    successor_sha = current_sha
                    successor_line = line
                    break
                status.setdefault("ignored_branch_updates", []).append(
                    {"sha": current_sha, "changed_paths": changed_paths}
                )
        time.sleep(10)

    status["last_phase"] = str(walk(last_state, "phase") or status.get("phase") or "")
    status["conversation_url"] = str(
        walk(last_state, "conversation_url") or status["conversation_url"]
    )
    status["submission_observed"] = (
        bool(walk(last_state, "submission_observed")) or status["submission_observed"]
    )
    status["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    if not status["conversation_url"] or not status["submission_observed"]:
        raise RuntimeError("provider-visible child conversation submission was not observed")
    if not successor_sha:
        raise RuntimeError("child did not emit the required successor PR synchronization event")
    status["successor_commit_sha"] = successor_sha
    status["successor_trigger"] = successor_line
    status["successor_external_event_observed"] = True
    comment("### MCP_BATON_CHILD_READY", status)
except Exception as exc:
    status["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    status["terminal_error"] = f"{type(exc).__name__}: {exc}"
    try:
        comment("### Public cloud MCP baton failure", status)
    finally:
        raise
