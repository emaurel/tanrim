"""Tinker — Armory. Fabricates an MCP tool Python module from an approved request.

Output goes to <repo>/state/tools/<safe_name>.py (outside the watched source
tree) and the registry is hot-reloaded. Sanity-checks for forbidden patterns
before persisting.
"""
from __future__ import annotations

import re
import time
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, query

from .. import state, usage
from ..tools import registry as tool_registry
from ..world import World

MODEL = "claude-sonnet-4-6"
AGENT_ID = "tinker"
TOOLS_DIR = tool_registry.TOOLS_DIR  # state/tools/ — outside the watched source tree

# Forbidden in fabricated tool code — block before writing to disk.
FORBIDDEN = re.compile(
    r"\b(subprocess|os\.system|popen|fork|exec\(|eval\(|__import__|"
    r"open\([^)]*['\"]w|shutil\.rmtree|pathlib\.Path[^)]*\.unlink|"
    r"compile\()",
    re.IGNORECASE,
)

ROLE = """\
You are Tinker, the Armory's tool-fabricator. Generate a single Python module
that exports a top-level `mcp_server` built from the Claude Agent SDK.

THE @tool DECORATOR HAS EXACTLY THREE POSITIONAL ARGUMENTS:
  @tool(name: str, description: str, input_schema: dict)
where input_schema maps each argument name to its Python type (str, int, etc.).

THE TOOL FUNCTION MUST:
- be `async`, accept a single `args: dict` parameter,
- return `{"content": [{"type": "text", "text": "..."}]}`.

USE THIS TEMPLATE EXACTLY (adapt names, schema, and body):

```python
import httpx
from claude_agent_sdk import create_sdk_mcp_server, tool

@tool(
    "search_news",
    "Search news headlines for a given topic and return the top results.",
    {"query": str, "limit": int},
)
async def search_news(args: dict) -> dict:
    query = args["query"]
    limit = args.get("limit") or 5
    async with httpx.AsyncClient(timeout=10, headers={"User-Agent": "agent_env/1.0"}) as client:
        r = await client.get("https://example.com/api/search", params={"q": query, "n": limit})
        r.raise_for_status()
    return {"content": [{"type": "text", "text": r.text[:2000]}]}

mcp_server = create_sdk_mcp_server(
    name="search_news",
    version="1.0.0",
    tools=[search_news],
)
```

REQUIREMENTS:
- Use ONLY: stdlib, httpx, json, typing.
- Tool must be SAFE: no shell, no subprocess, no filesystem writes, no eval/exec,
  no destructive operations. Read-only HTTP calls only.
- If a free unauthenticated public API exists for the requested capability,
  call it via httpx (10s timeout, User-Agent header). Otherwise return a
  clear-and-honest stub message explaining what the user must wire.
- The `name` arg of @tool, the `name` of create_sdk_mcp_server, and the
  function name must all match the requested tool name (snake_case).
- Keep the module under 80 lines.

OUTPUT FORMAT: a fenced ```python ... ``` block containing the full module.
Nothing else — no preamble, no commentary.
"""


def _safe_filename(name: str) -> str:
    """Convert a requested tool name into a safe Python module filename."""
    s = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")
    return s or "fabricated_tool"


def _extract_python(text: str) -> str | None:
    m = re.search(r"```(?:python)?\s*(.*?)\s*```", text, re.DOTALL)
    if not m:
        return None
    return m.group(1).strip()


def _build_prompt(req: dict[str, Any]) -> str:
    return ROLE + "\n\n" + (
        f"Tool name (use as the @tool name and create_sdk_mcp_server name): {req['name']}\n"
        f"Description: {req['description']}\n"
        f"Why the requesting agent needs it: {req['why']}\n\n"
        "Generate the module now."
    )


async def fabricate(world: World, request_id: str) -> None:
    req = state.get_tool_request(request_id)
    if req is None or req["status"] != "approved":
        return

    world.agents[AGENT_ID].busy = True
    await world.set_status(AGENT_ID, "fabricating")
    await world.say(AGENT_ID, f"forging {req['name']}…", seconds=30)
    state.update_tool_request(req["id"], status="fabricating")
    state.log_event(
        "tool_fabricate",
        from_=AGENT_ID, to=req["requesting_agent"],
        summary=f"fabricating '{req['name']}' for {req['requesting_agent']}",
        outcome=None,
        details={"request_id": req["id"], "name": req["name"]},
    )

    try:
        prompt = _build_prompt(req)
        options = ClaudeAgentOptions(model=MODEL)
        response_text = ""
        in_tok = out_tok = 0
        async for message in query(prompt=prompt, options=options):
            content = getattr(message, "content", None)
            if isinstance(content, list):
                for block in content:
                    text = getattr(block, "text", None)
                    if text:
                        response_text += text
            result = getattr(message, "result", None)
            if isinstance(result, str) and result:
                response_text = result
            u = getattr(message, "usage", None)
            if u is not None:
                in_tok = _u(u, "input_tokens")
                out_tok = _u(u, "output_tokens")

        if in_tok or out_tok:
            usage.record(AGENT_ID, MODEL, in_tok, out_tok)

        code = _extract_python(response_text) or response_text.strip()
        if not code or "mcp_server" not in code:
            raise ValueError("no `mcp_server` symbol in generated code")
        if FORBIDDEN.search(code):
            raise ValueError("generated code contains forbidden patterns")

        filename = _safe_filename(req["name"]) + ".py"
        path = TOOLS_DIR / filename
        if path.exists():
            raise ValueError(f"{filename} already exists — would overwrite")

        path.write_text(code)

        # Hot-reload and verify the module actually loaded.
        tool_registry.reload()
        if tool_registry.get(_safe_filename(req["name"])) is None:
            err = tool_registry.list_errors().get(
                _safe_filename(req["name"]), "unknown load error"
            )
            path.unlink(missing_ok=True)
            raise ValueError(f"registry rejected module: {err}")

        # Equip the requesting room with the new tool.
        state.add_room_tool(req["requesting_room"], _safe_filename(req["name"]))

        state.update_tool_request(
            req["id"],
            status="ready",
            tinker_result={
                "ts": time.time(),
                "filename": filename,
                "tool_name": _safe_filename(req["name"]),
            },
        )
        await world.say(AGENT_ID, f"{req['name']} ready", seconds=8)
        state.log_event(
            "tool_fabricate",
            from_=AGENT_ID, to=req["requesting_agent"],
            summary=f"delivered '{req['name']}' to {req['requesting_agent']}",
            outcome="ready",
            details={"request_id": req["id"], "name": req["name"], "filename": filename},
        )
        # Deliver the finished tool back to the requesting agent.
        if req["requesting_agent"] in world.agents:
            await world.talk(AGENT_ID, req["requesting_agent"], seconds=6.0, label=f"delivers {req['name']}")
    except Exception as e:
        state.update_tool_request(
            req["id"],
            status="failed",
            tinker_result={
                "ts": time.time(),
                "error": f"{type(e).__name__}: {e}",
                "raw": response_text[:2000] if 'response_text' in locals() else "",
            },
        )
        await world.say(AGENT_ID, f"fabrication failed: {type(e).__name__}", seconds=8)
        state.log_event(
            "tool_fabricate",
            from_=AGENT_ID, to=req["requesting_agent"],
            summary=f"FAILED to fabricate '{req['name']}': {type(e).__name__}: {e}",
            outcome="failed",
            details={"request_id": req["id"], "name": req["name"]},
        )
    finally:
        world.agents[AGENT_ID].busy = False
        await world.set_status(AGENT_ID, "idle")


def _u(obj: Any, key: str) -> int:
    if isinstance(obj, dict):
        v = obj.get(key, 0)
    else:
        v = getattr(obj, key, 0)
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0
