# agent_environment

Claude Agent SDK app that runs an Etsy print-on-demand pipeline as a pixel-art world. Each workflow stage is a **room**; each agent is a sprite that walks between rooms. Owner approves listings in batches before publish.

## Layout

```
rooms/                       declarative YAML manifests, read by both sides
backend/agent_env/           Python — Claude Agent SDK orchestrator + FastAPI WS server
frontend/                    Vite + TS + Phaser SPA — renders the world
```

## Modularity contract

Adding a room = adding `rooms/<id>.yaml`. The backend serves the manifest list at `GET /rooms` and the frontend builds the map from it. **Do not hardcode the room list** in either side. A room may optionally ship a Python module under `backend/agent_env/rooms/<id>.py` exposing a `run(ctx)` coroutine; if absent the room is decorative.

### Per-room menus

Click a room → a side panel opens. Each room gets a custom panel by:

1. **Backend handler (optional)** — `agent_env/handlers.py` registers a `RoomHandler` under the room id. `state()` returns data for the panel; `action(name, payload)` performs writes. Without a handler, `GET /rooms/<id>/state` still returns basic info but `POST /rooms/<id>/action` returns 404.
2. **Frontend renderer (optional)** — drop a file at `frontend/src/panels/<id>.ts` exporting `open(roomId)`, then register it in `panels/index.ts`. Without a renderer, the room falls back to the generic info panel.

The Archives room is the reference implementation: `ArchivesHandler` in `handlers.py` + `panels/archives.ts` provides a feedback ledger (read past notes, write `note` / `feedback` / `approval` / `rejection`). Notes are persisted to `state/notes.json`.

### Tool creation (Nova → Ultron → Tinker)

Agents that hit a missing capability emit a `request_tool` call (via the per-agent meta MCP server in `meta_tools.py`). The flow:

1. Nova calls `request_tool(name, description, why)` mid-run → `state.add_tool_request(...)` (status=`pending`).
2. The orchestrator's gatekeeper loop (`Orchestrator._gatekeeper_loop`) polls every 3s.
3. Pending requests go to `agents/ultron.py:review` — a Sonnet call that returns approve/deny. Risky requests (regex match on `subprocess|exec|delete|...`) are escalated as a `tool_review` user-approval instead.
4. Approved requests go to `agents/tinker.py:fabricate` — a Sonnet call that generates a Python MCP module, sanity-checks it against forbidden patterns, writes it to `tools/<safe_name>.py`, and hot-reloads the registry (`tools/registry.py`).
5. The new tool name is appended to `state/room_tool_overrides.json` for the requesting room. `GET /rooms/<id>/state` returns `resolved_tools = manifest_tools + overrides`, filtered to those actually registered.
6. Future runs of the requesting agent build their `ClaudeAgentOptions.mcp_servers` from the resolved list — the new tool becomes callable.

### User approvals + room badges

Any agent (or system code) can call `state.add_user_approval(kind, room_id, requesting_agent, summary, payload)` to surface something for the operator. The world map renders a red circle badge with count on any room with pending approvals, and the room panel auto-renders the approval cards (via `frontend/src/approvals.ts`) at the top of the body before the room's own UI. Resolving via `POST /approvals/<id>` (`approved` | `rejected`) emits a WS `approvals_updated` event so the badges and panel update live.

Today's only `kind` is `tool_review` (Ultron's risky-request escalation). Future kinds — `create_room`, `delete_room`, `over_budget`, etc. — slot in without further plumbing.

## Rooms (current)

Modeled on the reference video Edgar shared (see memory `reference_dungeon_video.md`):

| Room          | Inhabitant | Function                                                            |
|---------------|------------|---------------------------------------------------------------------|
| Throne        | Ultron     | Overseer. Dispatches briefs, schedules retros.                      |
| Research Lab  | Nova       | Studies winning Etsy stores; copies concepts → Forge.               |
| Factory       | Forge      | Generates designs (image-gen) + composes Printify mockups.          |
| Copy Desk     | Scribe     | Etsy SEO titles, descriptions, 13 tags.                             |
| Shipping Bay  | Courier    | Pushes to Etsy. `etsy.publish_listing` is gated by approval.        |
| Communications| Echo       | Unified inbox: TikTok / YouTube / email / Etsy.                     |
| Archives      | Sage       | Persistent memory + feedback ledger fed back into agent context.    |
| Armory        | Tinker     | Builds/registers MCP tools and AgentDefinitions.                    |
| Treasury      | Coin       | Tracks API spend per agent per day; alerts on overruns.             |
| War Room      | (gathers)  | Daily/weekly retro. All agents converge for performance review.     |

## Approval gates

The orchestrator uses `can_use_tool` from `claude-agent-sdk` to pause on `publish_*` tools and surface a batch to the operator via the WS event `approval_request`. The operator answers with `approval_response` events; the SDK callback resolves accordingly.

## Running

```
uv pip install -e .
cp .env.example .env  # fill ANTHROPIC_API_KEY at minimum
uvicorn agent_env.server:app --reload --port 8765
# in another terminal:
cd frontend && npm install && npm run dev
```
