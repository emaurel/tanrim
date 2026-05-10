"""Per-agent meta MCP tools — capabilities every agent has by default.

Currently: `request_tool` — the agent emits a tool request that flows to
Ultron for review and (if approved) Tinker for fabrication.
"""
from __future__ import annotations

from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from . import state


def make_meta_server(
    agent_id: str,
    room_id: str,
    world: Any = None,
    original_task: dict[str, Any] | None = None,
) -> Any:
    """Return an MCP server scoped to a particular agent's identity.
    `original_task` (e.g., {"prompt": "..."}) is stored on the request so the
    orchestrator can auto-rerun this agent with the same task once Tinker has
    delivered the new tool.
    """

    @tool(
        "request_tool",
        "Request a new capability you don't currently have. The Armory will "
        "review and fabricate it. As soon as it's ready you will be auto-rerun "
        "with this same prompt and the new tool available — do NOT wait or "
        "loop here; finish this run with your best training-data answer and "
        "note that real data is coming on the rerun.",
        {
            "name": str,
            "description": str,
            "why": str,
        },
    )
    async def request_tool_fn(args: dict[str, Any]) -> dict[str, Any]:
        req = state.add_tool_request(
            requesting_agent=agent_id,
            requesting_room=room_id,
            name=args["name"],
            description=args["description"],
            why=args["why"],
            original_task=original_task,
        )
        state.log_event(
            "tool_request",
            from_=agent_id, to="ultron",
            summary=f"requested '{args['name']}': {args.get('why', '')[:160]}",
            outcome=None,
            details={"request_id": req["id"], "name": args["name"]},
        )
        if world is not None:
            await world.talk(agent_id, "ultron", seconds=6.0, label=f"requests {args['name']}")
        return {
            "content": [{
                "type": "text",
                "text": (
                    f"Tool request submitted (id={req['id']}). "
                    f"The Armory will review it. The tool will not be "
                    f"available on this run — finish your task without it."
                ),
            }]
        }

    @tool(
        "ask_ultron",
        "Ping Ultron when you hit a blocker or question that's NOT a tool request. "
        "Use for: missing API credentials, ambiguous instructions, deciding between "
        "approaches, scope clarifications, anything that needs his judgment. He'll "
        "respond and you'll be auto-rerun with his guidance on the next iteration. "
        "Don't use this for tool requests — use `request_tool` for those.",
        {"message": str},
    )
    async def ask_ultron_fn(args: dict[str, Any]) -> dict[str, Any]:
        rec = state.add_escalation(
            agent=agent_id,
            room=room_id,
            message=args["message"],
            original_task=original_task,
        )
        state.log_event(
            "ask_ultron",
            from_=agent_id, to="ultron",
            summary=args["message"][:200],
            outcome=None,
            details={"escalation_id": rec["id"]},
        )
        if world is not None:
            await world.talk(agent_id, "ultron", seconds=6.0, label=f"asks: {args['message'][:30]}")
        return {
            "content": [{
                "type": "text",
                "text": (
                    f"Question submitted to Ultron (id={rec['id']}). He'll respond "
                    f"and you'll be auto-rerun with his guidance. For THIS run, "
                    f"give your best answer with the limitations you have, and "
                    f"clearly note the blocker."
                ),
            }]
        }

    return create_sdk_mcp_server(
        name=f"meta_{agent_id}",
        version="1.0.0",
        tools=[request_tool_fn, ask_ultron_fn],
    )
