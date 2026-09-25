# Writing a plugin

A plugin supplies everything Tanrim does not know: what a unit of work is,
what states it passes through, which rooms work it, who staffs them, what the
operator is asked, and what any of it means.

The environment asks questions. Your plugin answers them. It never reads your
files, never imports your modules on its own, and never assumes a layout — so
a plugin can keep its rooms in YAML, in Python, or in a database, and all
three work identically.

The smallest legal plugin is:

```python
from tanrim.contract import Plugin

class Minimal(Plugin):
    id = "minimal"
    name = "Minimal"

PLUGIN = Minimal()
```

That adds a name to `/plugins` and nothing else. Every method below has a
default that contributes nothing, so you implement only what you have.

`plugins.example/` in the environment's repository is a complete worked plugin
— a pipeline, a room, an agent, a gate, a hook, a record schema,
prompts and a self-check — kept small enough to read in one sitting.

---

## Installing

Before any of this, you need Tanrim itself installed and importable — the
README's "Running it" section, which ends with `tanrim` on the path and a
server on :8765. Everything below assumes it.

A plugin is a directory in `plugins/` containing `plugin.py` that exposes
`PLUGIN`. Restart the server, or reload without one:

```bash
curl -X POST http://127.0.0.1:8765/plugins/reload
```

Reload covers a plugin **appearing or disappearing**. It does not cover a
plugin whose code changed — Python caches modules — so restart while you are
editing.

A plugin can be switched off without being removed: a `.disabled` file in its
directory, or the switch in the app's Plugins panel. A disabled plugin is
listed but never imported, because importing is what runs its code.

---

## The machine

### `pipelines() -> Iterable[Pipeline]`

The kinds of work you define, their states and their legal moves.

```python
Pipeline(
    kind="greeting",            # `record.kind`
    entry="drafting",           # where a new record starts
    note="Name in, greeting out.",
    stages=(
        Stage("drafting", "waiting for the agent to write it"),
        Stage("written",  "written; waiting on you"),
        Stage("sent",     "done", releases_worker=True),
        Stage("abandoned","dropped", terminal=True),
    ),
    transitions=(
        Transition("drafting", "written", "greeter",  "forward"),
        Transition("written",  "sent",    "operator", "forward"),
        Transition("written",  "drafting","operator", "reject"),
    ),
)
```

**`Stage`**

| field | meaning |
|---|---|
| `id` | the stage name, unique across the whole install |
| `note` | one line, shown on the board and the pipeline diagram |
| `terminal` | an ENDING. Reachable from any stage, and nothing works it |
| `releases_worker` | nothing will move it on the pipeline's own clock, so a worker hired for this record can be retired |

`terminal` and `releases_worker` are different and both are needed. A stage
where you are waiting for someone else to reply is not an ending — they may
still answer — but nothing here will move it. A stage a rebuild is dispatched
from immediately is neither.

**Stage ids are global.** Two pipelines sharing a stage is the normal case and
they de-duplicate by id — but two UNRELATED pipelines both calling a stage
`sourced` get the same `Stage` object, with one plugin's `note` silently
winning. Name yours for what they are.

**`Transition`** — `frm`, `to`, `role`, and an advisory `kind`
(`forward | branch | reject | park | bounce`). A transition whose only role is
`operator` is a GATE: the environment raises a card and waits rather than
dispatching anyone.

The table is **enforced**. `advance_record` refuses an undeclared edge and
names what was allowed instead. Declare the rejections and the loops, not just
the happy path — those are the edges people forget and then find in
production.

### `record_model() -> type[BaseModel] | None`

The shape of one unit of work. **Purely declarative today** — nothing
validates a write against it, nothing in the core reads `env.record_model`,
and it is not served over HTTP. It documents the record for a human and is
there for whatever comes to read it. Returning None means "any JSON object",
which is what an early plugin wants.

`contract.py`'s docstring used to claim it "buys validation on write"; it was
corrected rather than left to mislead the next reader of either file.

### `summary_fields()` and `bulk_fields()`

A board row carries the summary fields plus any other field small enough to be
worth having.

`bulk_fields` names the LARGE ones so they are skipped without being measured.
Purely a shortcut — deciding whether a field fits means serialising it, which
cost 9 ms per board of seventy records, on the loop thread agent runs share.
Missing one costs a little CPU and never a payload.

---

## The world

### `rooms() -> Iterable[Room | RoomPatch]`

```python
Room(
    id="hall",
    name="The Hall",
    purpose="Where greetings are written.",
    position=(0, 0), size=(12, 8),        # tiles
    color="#334455",
    max_workers=3,
    tools=("my_tool",),
    skills=("some-skill",),
    workbenches=(
        Workbench(id="desk", name="The Desk",
                  job="Writing one greeting",
                  stages=("drafting",)),
    ),
)
```

Or `plugin_helpers.yaml_rooms(HERE / "rooms")` to read a directory of
manifests. That helper is a convenience you call — the environment does not
know the directory exists.

**Workbenches are the router.** The `stages` a bench declares are what make a
stage reachable in that room. A job at a stage no bench declares is refused at
boot, because every dispatch to it would be refused at runtime.

Geometry is computed if you omit it. Give `position`/`size` only to override.

**`RoomPatch`** adds to a room another plugin owns:

```python
RoomPatch(extends="hall",
          workbenches=(Workbench(id="side", stages=("my_stage",)),))
```

Merging is asymmetric on purpose: a bench with a new id is ADDED, a bench with
an existing id has its `stages` and `tasks` UNIONED, and everything else
overrides when given. Returning a whole `Room` with the same id would boot
cleanly and silently drop everything the original had.

### `agents() -> Iterable[AgentSpec | AgentPatch]`

```python
AgentSpec(
    role="greeter", name="Greeter", room="hall",
    description="Writes one friendly sentence.",
    color="#9ad1b0", model="claude-haiku-4-5",
    jobs={"drafting": write_greeting},     # stage -> coroutine
    default_job=None,                      # reached when no stage matches
    station="desk",                        # stood at even when idle
    singleton=False,                       # exactly one worker, ever
)
```

A **job** is `(world, task) -> result`, awaited. A dict rather than positional
arguments, because not every job is about a record at a stage: one agent may
be given a place to search and no record at all, and another may branch on
something the dispatcher chose. The environment puts `record_id` in the task
when there is one. Read what you need.

**Declare `model` here**, not by reading a constant off your agent module —
otherwise merely building the agent table imports every agent, each of which
reads its prompts as it imports, from an environment that is still being
assembled.

**`AgentPatch`** gives an existing role a job at a stage you invented.

### Importing lazily

`plugin.py` is a manifest. Importing it must not drag in every agent module.
The pattern the real plugins use:

```python
def _agent(module: str, function: str):
    cache = []
    def resolve():
        if not cache:
            mod = importlib.import_module(f"{__package__}.agents.{module}")
            cache.append(getattr(mod, function))
        return cache[0]
    return resolve

def _with_instruction(module: str, function: str):
    resolve = _agent(module, function)
    async def job(world, task):
        return await resolve()(world, task["record_id"],
                               task.get("instruction", ""))
    return job
```

A resolver you can CALL is what lets a test prove the name exists without
running the job. Naming the functions directly produced three separate import
cycles.

### `room_handlers() -> Mapping[str, type]`

`room id -> a handlers.RoomHandler subclass` — what a room's panel shows and
which actions it offers.

`RecordRoomHandler` supplies the queue, the one-run-at-a-time guard and the
error surface. A subclass declares:

```python
class HallHandler(RecordRoomHandler):
    agent_id, action_name = "greeter", "run_greeting"
    accepts_stages = ("drafting",)

    async def run(self, payload):
        record_id = payload.get("lead_id")     # the wire name
        if not record_id:
            return {"ok": False, "error": "lead_id required"}
        # `_agent` from "Importing lazily" above. A `Job` takes (world, task),
        # so build the task dict rather than passing arguments positionally.
        return await _agent("greeter", "run_greeting")()(
            self.world, {"record_id": record_id})
```

One handler is built **per room on the map**, not per room you declare — two
castles of one plugin each need their own queue. Yours is constructed with the
world alone and told where it is afterwards, so you need not change signature.

**A refusal your `run()` returns lands in `last_result`, not `last_error`.**
The POST that started it has already answered `ok: true`, because starting the
task succeeded. Return `{"ok": False, "error": "..."}` and the panel shows it.

---

## Asking the operator

### `gates() -> Iterable[Gate]`

```python
Gate(
    kind="greeting_ready",
    means="the greeting is written — send it?",
    on_decision=handler,      # (world, card, decision, reason) -> None
    validate=check,           # (card, decision, reason) -> str | None
    informational=False,      # a card cleared by being dismissed
)
```

`validate` is checked BEFORE the card is resolved. Returning a string refuses
and leaves the card pending; refusing after resolving consumes the card and
leaves the work undone.

Name your handlers lazily, as with agents — `approvals.py` usually imports
half your plugin, and merely asking what your gates ARE should not load it.

### `step_gates() -> Iterable[StepGate]`

Stop before a STEP and ask, rather than before an edge:

```python
StepGate(
    stage="written", gate="greeting_ready",
    build=lambda world, record: {"lead_id": record["id"], ...},
    kinds=("greeting",), room="hall", agent="greeter",
    permanent=True,
    reason="a greeting reaches a person and cannot be taken back",
)
```

Keyed by `(stage, pipeline)` because the operator is asked before the room
runs, when which edge it will take is not yet known.

`permanent=True` cannot be switched off from the settings panel. Anything
irreversible or outward-facing should be — those must never depend on a
checkbox.

`room` and `agent` say where the card is filed and who shows as waiting.
Leave them out and it is filed against "any room", which survives a
one-room plugin and is wrong the moment there are two.

`build` makes the card's payload, so the operator sees what they are deciding
about rather than a JSON dump. Two keys in it are special:

- **`what_this_means`** — the only part written for the person deciding.
- **`summary`** — lifted out of the payload to become the card's headline.
  Without it the card is titled `"<name> is at '<stage>'"`, which tells the
  operator nothing they did not already know.

And remember the bench: a step gate at a stage no workbench declares is never
raised at all. See `pipelines()`.

---

## Reacting

### `hooks() -> Mapping[str, Callable]`

| hook | kind | signature |
|---|---|---|
| `stage_changed` | broadcast | `(world, record, frm, to)` |
| `agent_report` | broadcast | `(world, event)` |
| `escalation` | broadcast | `(world, escalation_id)` |
| `inbound_message` | broadcast | `(world, record_id, message)` |
| `inbound_bounce` | broadcast | `(world, record_id, address, permanent, detail)` |
| `tick` | broadcast | `(world)` — every orchestrator pass |
| `startup` | broadcast | `(world)` — once, after the server is assembled |
| `before_stage_change` | veto | `(record, frm, to) -> str \| None` |
| `normalise_write` | transform | `(kind, fields) -> fields` |
| `subtask_review_model` | supplier | `() -> str` |

Broadcast fans out to every listener. Veto consults each in turn and the first
refusal wins. Transform chains, each output feeding the next. Supplier takes
the last plugin to answer.

**Veto and transform run inside a durable write**, so they must be pure and
synchronous. An `async def` listener is refused at boot.

`normalise_write` is how a field gets cleaned on every write without the
ledger knowing what the field means — a guard at each call site is a guard
that gets forgotten.

---

## Talking to models

### `prompt(module, name, kind) -> str | None` and `declares_prompts()`

Prompt text lives in files, usually outside version control, so the plugin
DECLARES what it needs and `check()` reports at boot what is missing. The
declaration survives a checkout when the text does not.

```python
PROMPTS = file_prompts(HERE / "prompts")     # a helper you call
NEEDS = ("greeter/ROLE", "greeter/SCHEMA")

def declares_prompts(self): return NEEDS
def prompt(self, module, name, kind=None): return PROMPTS(module, name, kind)
def check(self): return [f"missing prompt: {m}" for m in PROMPTS.missing(NEEDS)]
```

Returning None falls through to the next plugin. Prompts resolve
**plugins-that-own-the-kind first**, so an extension overrides one prompt
without shipping the rest.

**A missing prompt must raise, never return empty.** An agent with no
instructions does not fail — it improvises.

### Running an agent

```python
from tanrim.agent_helpers import run_agent

result = await run_agent(
    world,
    role="greeter", room_id="hall", model="claude-haiku-4-5",
    prompt=built_prompt,
    summary="greeting someone",
    workbench="desk",
    say="writing…",
    original_task={"lead_id": record_id},
    max_turns=4,
    schema=SCHEMA,              # pass this whenever the agent has side effects
    max_budget_usd=2.0,
)
data = result.data or {}
```

`role` and `room_id` are your BASE ids. The environment scopes them to
whichever castle the run belongs to; you never see it.

`schema=` is the output contract, used to re-ask if the agent ends on prose
instead of JSON. That retry is text-only and single-turn — it does not re-send
your prompt, which is how an agent with file tools once rebuilt a whole site a
second time.

`builtin_tools=["Write", "Read", "Edit"]` opts into the SDK's file tools; pair
it with `cwd` so the agent is scoped to one directory.

---

## Showing a record

### `record_view(record, kind) -> list[dict] | None`

How one record is drawn, as blocks. Returning None — the default — infers a
view from the JSON, which is why a plugin has a usable record window before
anyone writes presentation for it.

```python
from tanrim import view

def record_view(self, record, kind):
    p = record.get("profile") or {}
    return [
        view.text(p.get("summary", "")),
        view.fields([
            view.row("Trading as", p.get("name"), source=p.get("source_url")),
            view.row("Closes", p.get("closes"), conflict=True),
        ], title="Identity"),
        view.table(["Item", "Price"], [
            {"cells": [i["name"], i.get("price")], "source": i.get("source_url")}
            for i in p.get("items", [])
        ], title="Offering"),
    ]
```

Blocks: `section`, `text`, `fields`, `list`, `table`, `images`, `raw` — plus
`timeline`, which the core builds for every kind from the record's history and
which you should not emit yourself.

The builder for a `list` block is `view.listing(items, title, style)`;
`view.list` would shadow the builtin. Value
formats: `text`, `money`, `bytes`, `colour`, `url`, `datetime`, `percent`.

`source` and `conflict` are properties of a ROW, a list item or a table row —
so a renderer can mark which facts are cited and which are contested without
being told what your record is.

**The timeline is not yours.** The environment builds one from the record's
history for every kind and appends it after whatever you return.

`view.infer` is importable, so you can lay out the parts you care about and
hand the rest back to inference.

---

## The rest

### `skills() -> Mapping[str, Path]`

Claude Code skills this plugin ships, `name -> directory`. A room grants one
by NAME in its manifest, and the name must resolve to a directory holding a
`SKILL.md`.

```python
from tanrim.plugin_helpers import dir_skills

def skills(self):
    return dir_skills(HERE / "skills")
```

Ship them with the plugin that needs them. The environment used to look every
name up in `<repo>/.claude/skills/`, which meant cloning a plugin gave you a
room granting skills it did not have — and a missing skill is DROPPED
silently, so nothing tells you.

Two plugins claiming one name is refused at boot, like a duplicate gate or
tool. `<repo>/.claude/skills/` still works as a drop for a skill belonging to
no plugin, and cannot shadow one a plugin ships.

### `tools() -> Iterable[Tool]`

An MCP server a room may be granted by name. `plugin_helpers.py_tools(dir,
package)` imports a directory of modules each exporting `mcp_server`.

### `routes() -> APIRouter | None`

An HTTP surface of your own, included as given — you own your prefix. Two
plugins claiming a path is a collision you resolve between yourselves.

The environment serves the machinery: rooms, castles, approvals, workers, the
plugin listing, `/records/{id}/view`. Everything ABOUT the work is yours.

### `overseer() -> str`

The role agents escalate to and report to. The meta tools are named
`ask_<role>` and `report_to_<role>` and their prompts are looked up under the
same name, so naming the role here is the whole change.

### `persist_room(room)`

Called when the environment changes a room at runtime — only `max_workers`
does, from the crew slider. Write it back wherever your rooms came from, or do
nothing and let the change not survive a restart.

### `setup(env)` and `check()`

`setup` runs once after every plugin is merged and validated — for anything
needing the finished environment. Raising refuses the boot.

`check` returns problems as strings, printed at startup. They do not stop the
boot: a half-configured environment you can see is more useful than one that
will not start.

---

## Testing your plugin

Put tests in `tests/` inside your plugin. The environment's `pytest.ini`
collects `plugins/*/tests`, so they run with everything else.

The environment's own suite builds **synthetic** plugins in a temporary
directory — see its `tests/conftest.py`. The `plugins` fixture writes a
`plugin.py`, boots it, and tears it down, which is the cheapest way to test
contract behaviour without touching real ones.

Two habits worth copying from the environment's tests:

- **Check that a test fails without the fix.** Several tests here passed with
  the bug present and had to be rewritten; a test you have not watched fail is
  a test you do not know the meaning of.
- **Assert provenance, not a fixed list.** A test asserting the core's gates
  EQUAL your plugin's exact set fails the moment a second plugin is installed
  — for doing the thing the test exists to encourage.

---

## Saving what a job produced

The one thing every job must do, and the reference used not to name it:

```python
from tanrim import state

record = state.get_record(record_id)                   # read
state.update_record(record_id, **fields)               # patch, no stage move
state.advance_record(record_id, "written",             # move AND patch,
                     agent="greeter", note="…",        #   in one write
                     **fields)
state.add_record("Ada", kind="greeting", recipient="Ada")   # open a new one
```

`advance_record` is the ONLY way a stage may change. It appends to the
record's history in the same write — the move, the agent, the note, whether it
was an operator hand-move, and which fields the step actually changed (names
only, compared rather than listed, because a step that rewrites a field
identically has produced nothing).

**It refuses an edge your pipeline does not declare**, logging what WAS
allowed from there, and returns `None`. It does not raise — so a job that
ignores the return value reports success having moved nothing, which is a
real class of bug in this codebase's own history.

`add_record` starts a record at its pipeline's `entry` stage. There is no
generic HTTP route for it: what a unit of work IS belongs to you, so opening
one does too. Serve it from `routes()` or create them from a sourcing agent.

## Words this reference uses

- **record** — one unit of work. Stored in `state/leads.json`, which keeps its
  old name because renaming it would be a migration with nothing to gain;
  `"lead_id"` survives as a dict key for the same reason. It is the wire
  format.
- **room** — a place on the map. **workbench** — a job within it, and the
  routing table: declaring a stage on a bench is what makes that stage
  reachable.
- **role** vs **worker** — a room's agent is a role, which owns memory and
  context. Workers are hired against it, and own the lock, the sprite and the
  log line. You address a role; you never name a worker.
- **castle** — one running instance of your plugin, with its own rooms,
  sprites and records. You never see one: the environment scopes ids to the
  current castle for the duration of a run, and your plugin says
  `run_agent(role="greeter")` either way.
- **gate** — something the operator decides. A `Gate` says what a decision
  MEANS; a `StepGate` stops before a step and asks.
