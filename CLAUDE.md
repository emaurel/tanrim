# Tanrim

An **environment for agent-operated work**, drawn as a place. It owns a world
of rooms, a pool of workers, a durable ledger, a state machine, an approval
mechanism, an agent runner and an HTTP surface — and it knows nothing about
what the work IS.

Everything about the work arrives from a **plugin**. An install with no
plugins has no stages, no rooms, no agents and nothing to do, which is the
correct empty state rather than an error. There is a test that asserts exactly
that.

Four nouns this file uses from here on. A **record** is one unit of work. A
**room** is a place on the map, and a **workbench** within it declares which
stages are worked there — that is the routing table. A **castle** is one
running instance of a plugin, with its own rooms, sprites and records; a
plugin never sees one. A **gate** is something the operator decides before
anything irreversible happens. All four get a section of their own below.

This file is about the environment. If you are working on a plugin, its own
repository documents it; if you are writing one, start at
[docs/CONTRACT.md](docs/CONTRACT.md).

## Layout

```
backend/tanrim/     the environment — ~9,700 lines across 26 modules
app/                the operator's app — Flutter, ~7,400 lines in app/lib
plugins/            installed plugins (gitignored; clone them in)
plugins.example/    a worked example, deliberately NOT installed. Covered by
                    tests/test_example_plugin.py, which boots it and RUNS its
                    job, because it rotted once and nothing noticed
state/              JSON ledgers and whatever plugins write
docs/               the plugin contract, in full
```

The split is enforced by tests, not by convention. `backend/tanrim/` may not
import a plugin, may not name a stage, a role or a domain field, and may not
serve a route about the work. One test greps the whole core for the words a
plugin owns; another boots with only a synthetic plugin and asserts the real
ones' routes are gone.

## Running

```bash
uv venv .venv && uv pip install --python .venv/bin/python -e .
.venv/bin/python -m playwright install chromium   # only if a plugin renders pages
cp .env.example .env                              # ANTHROPIC_API_KEY at minimum

PYTHONPATH=backend .venv/bin/python -m uvicorn tanrim.server:app --port 8765
cd app && flutter run -d linux --release
```

`--release` matters. `flutter run` defaults to a debug build, which is
un-optimised JIT with every assertion on; for a map that repaints every frame
the difference is not subtle.

Boot prints what is installed and what each plugin serves:

```
[boot] 3 plugin(s): job_hunt, web_agency, website_recreation
[boot] 2 problem(s) reported by plugins:
[boot] job_hunt: /applications, /applications/{record_id}, …
[boot] web_agency: /health/domain-pricing, /health/google, /invoices, …
[boot] website_recreation: /leads/port
```

Elided with `…`; the real lines print every path. The problem count is
`check()` from each plugin — a missing prompt, an unset key — and is **not**
fatal: a half-configured environment you can see is more useful than one that
will not start.

Installing a plugin is putting a directory in `plugins/`. Removing one removes
its stages, rooms, agents, gates, tools and routes with it.

```bash
.venv/bin/python -m pytest -q          # core + every installed plugin's own
```

---

# The plugin contract

`backend/tanrim/contract.py` says what a plugin IS, and is the file to read
first. [docs/CONTRACT.md](docs/CONTRACT.md) is the reference; this is the
shape and the reasoning behind it.

## The environment never reads a plugin's files

It asks questions and the plugin answers.

The first version reached IN: it globbed `<plugin>/rooms/*.yaml`, parsed the
YAML itself, searched `<plugin>/prompts/` and scanned `<plugin>/tools/`. So
the contract was "put files of this kind in a directory with this name", and a
plugin could only ever be a directory laid out the way the core expected —
never rooms from a database, never generated per tenant, never defined in
Python.

`plugin_helpers.yaml_rooms()`, `file_prompts()` and `py_tools()` are
conveniences a plugin CALLS. They are not the mechanism.

A plugin answers: `pipelines()`, `record_model()`, `record_view()`, `rooms()`,
`agents()`, `gates()`, `step_gates()`, `tools()`, `routes()`, `hooks()`,
`prompt()`, `room_handlers()`, `overseer()`, `summary_fields()`,
`bulk_fields()`, `declares_prompts()`, `persist_room()`, `setup()`, `check()`.

Every one has a default that contributes nothing, so the smallest legal plugin
is an id and a name.

## Binding is by reference, never by name

The first version passed dotted strings — `"my_plugin.agents.builder:run_build"`
— resolved on first use, to dodge an import cycle: the core's module-level
constants were built at import, so a plugin could not run code before the core
had decided what it was.

The cycle goes away with a boot step. `discovery.find()` → `environment.boot()`
happens after the core is importable and before anything queries it, so a
plugin hands over real functions and real objects. A typo is an
`AttributeError` where you wrote it rather than a test failure months later.

Boot asks every plugin everything **once**, merges the answers, and validates
before any `setup()` runs. Refusals worth knowing about: a job at a stage no
bench in that room declares, a room patch with no target, two plugins claiming
one gate or tool name, a misspelt hook.

## An extension patches rather than restates

`RoomPatch` adds a bench to a room another plugin owns; `AgentPatch` gives an
existing role a job at a stage the extension invented.

Both exist because the obvious alternative — returning a whole `Room` or
`AgentSpec` with the same id — boots perfectly cleanly and silently drops
everything the original had. The first version of the contract had that trap,
and the test that appeared to prove otherwise only passed because the
extension rebuilt the whole spec by hand.

## Four kinds of hook, because they compose differently

- **BROADCAST** fans out to every listener: `tick`, `startup`,
  `stage_changed`, `agent_report`, `escalation`, `inbound_message`,
  `inbound_bounce`.
- **VETO** consults each in turn and the first refusal wins.
  `before_stage_change` is how domain law — "do not redo this work while the
  other party is holding something we sent" — reaches a generic write.
- **TRANSFORM** chains, each output feeding the next. `normalise_write` is how
  a plugin cleans a field on every write without the ledger knowing what the
  field means.
- **SUPPLIER** takes the last plugin to answer.

Veto and transform run inside a durable write, so an `async def` listener is
refused at boot.

---

# Records and the state machine

## A record is the unit of work

`state/leads.json` holds one record per unit of work, whatever a plugin
decides that is. Every agent **enriches the same record** and moves its
`stage`; nothing reads "the most recent upstream artifact", because many
records sit at different stages at once.

The file is still called `leads.json`: it holds real records and renaming it
would be a migration with nothing to gain. `"lead_id"` survives as a dict key
for the same reason — it is the wire format, already written into
`state/*.json` and read by the app.

## The transition table is law, not documentation

`Pipeline.transitions` is enforced. `advance_record` refuses an undeclared
edge and logs why, naming what WAS allowed from there.

This was measured before it was enforced: across the real history, **23
declared edges, 44 actually taken, 182 transitions off the table**. A table
nothing checks drifts from the code the moment someone writes a new branch.

Three things make enforcement survivable:

- **The operator can always override.** The stage control passes
  `by_hand=True`, the only way off the table. The history entry is stamped
  `off_table: True`, so "who moved this, and was it a normal path" stays
  answerable.
- **Terminal states are reachable from anywhere.** Enumerating every edge into
  "cancelled" would say nothing the stage name does not, and refusing an agent
  the ability to give up is how work gets stuck rather than closed.
- **Replayed before shipping.** All 670 historical transitions were checked
  against the table before it was turned on.

## Kinds

A plugin may declare several pipelines. `record.kind` says which one a record
is on, and every transition declares which kinds it applies to — so two
pipelines can share a stage and move differently out of it.

**A record with no kind is resolved by its STAGE**, not by load order. The
fallback used to be "the first pipeline declared", which is discovery order,
which is alphabetical directory order — so installing a plugin whose name
sorted first silently moved 69 live records onto its pipeline, where none of
their stages existed. Nothing errored at the point of damage; the work simply
stopped moving.

## History

`advance_record` appends to the record's history in the same write that moves
it, so who moved what and why is always recoverable. Each entry records the
move, the agent, the note, whether it was an operator hand-move, and **which
fields the step actually produced** — names only, compared rather than listed,
because a step that rewrites a field identically has produced nothing.

---

# The world

## Rooms and workbenches

A room's jobs are declared as **workbenches**: an id, a name, a one-line
`job`, and the stages worked there. That is the whole contract — adding a
bench needs no Python and no app change. Geometry is computed if omitted, so a
plugin never does tile arithmetic.

Benches are the single source of truth for routing. `rooms.stages_for_role()`
derives what a room accepts from its bench declarations, and `runners._wrong_stage`
refuses a record that arrives at the wrong stage. That is a real guard: a
chained dispatch can fire mid-run, before the reporting agent has persisted
anything.

The sprite walks to the bench for the duration of a job and returns to the
idle strip afterwards. The map draws each bench as a labelled plate.

## Rooms are staffed, not single-agent

A room's agent is a **role**, not one worker. `max_workers` sets how many may
run at once; when a second record needs a busy room, another worker is hired —
`builder` → `builder-2` — and retired when its record reaches a releasing
stage.
The base worker is permanent, so a room never looks abandoned.

The split runs through the whole codebase:

- **The role owns memory and context** — escalations, tool history, past
  outputs. Every worker filling a role shares one memory.
- **The worker owns the lock, the sprite, the status and the log line.**

Assignment order: a free worker already on this record → any free worker →
hire one → `RoomAtCapacity`. That last is a normal outcome, not an error.

`AgentSpec.singleton` exists because a second overseer would dispatch against
the first.

## Castles

A **castle is one running instance of a plugin** — its own rooms on the map,
its own sprites, its own records. Two castles of one plugin are two of
whatever that plugin does.

Nothing in the core was written for more than one of anything: room ids, agent
ids and worker roles are all plain strings used as dictionary keys. So a
castle is a **suffix** on those keys — `workshop@c7f2`, `builder@c7f2` — and
`castles.base()` turns one back into the other. The environment goes on
answering about `workshop` and `builder`; every accessor strips the castle
first,
centrally, because the alternative is twenty callers each remembering to.

**Plugins never see it.** A plugin's agent says `run_agent(role="builder")`
and `world.move_to_workbench("builder", "workshop", "bench")` — base ids,
correct in every castle. The dispatcher sets a `ContextVar` for the run's duration and
the world, the worker pool and `run_agent` scope against it. A ContextVar and
not a global because runs are concurrent.

An install with **no** castles behaves exactly as it did before they existed,
unscoped.

### The web

Plots sit on rings around a hub: ring `n` holds `6n`, at radius
`n × PLOT × RING_SPACING`. The geometry is served, not computed in the app —
the rooms are positioned from the same numbers, and two sides working it out
separately is two sides that can disagree.

**The spacing floor is about 1.4, and it is not where it looks.** A plot is an
axis-aligned square in tile space, so two are clear only when their centres
differ by a full span along one AXIS. Two plots 80 apart on a 45° diagonal are
57 apart on each axis and overlap — and the first version of the test measured
the distance between centres, which passed while the map plainly showed them
on top of each other.

---

# Running an agent

`agent_helpers.run_agent()` owns every agent's turn: sprite state, MCP server
assembly, streaming, token accounting, logging, and a per-agent lock. Two
non-obvious details it handles:

- Built-in file tools (`Write`/`Read`/`Edit`) only **execute** when
  `tools={"type": "preset", "preset": "claude_code"}` is set. Without it the
  model calls Write, the call silently no-ops, and the agent reports success
  having written nothing.
- `strict_mcp_config=True` + `setting_sources=[]`, so MCP servers from the
  operator's own config do not leak into agent runs.

## A retry that does not redo the work

If the final message is not the expected JSON, `run_agent` retries once with
the bad output quoted back. That retry is **text-only and single-turn**, and
takes the output contract from the `schema=` argument rather than re-sending
the original prompt.

The first version re-sent the whole prompt with the original options — so an
agent with file tools attached, told again to "write the files now", rebuilt
an entire site a second time, ran 11 minutes, and overwrote work that had
already been verified.

## Running out of turns is an interruption, not a crash

`max_turns` is a backstop against a model that never stops polishing. It is
not a budget and says nothing about whether the work was good — but the SDK
reports hitting it as a terminal error, so every caller read it as one. On a
build that is the most expensive possible reading: an agent wrote a whole
site, reported it, hit the ceiling one turn later, and the caller's rollback
restored the PREVIOUS version over the finished one. Re-dispatching did it
again, because the same input reaches the same ceiling.

A turn ceiling now **resumes** the run: `ResultError` carries
`subtype == "error_max_turns"` and the `session_id`, and the continuation
passes that to the CLI's own `--resume`, so the model gets back everything it
actually did rather than a summary. It never re-sends the original brief.

Three brakes, because this is a natural money fire:

- `MAX_TURN_CONTINUATIONS = 2`, each with half the previous allowance.
- **The dollar budget carries across** — each continuation gets
  `max_budget_usd` minus what the run has spent.
- **A budget stop is never continued.** That ceiling IS the guard.

Every pass is billed as it ends, or a resumed run reports a fraction of its
real spend.

## Token accounting

`usage.record()` takes `cache_write` and `cache_read` alongside fresh input.
The SDK reports a 7,000-token prompt as `input_tokens: 10` with the remainder
under `cache_creation_input_tokens`, so counting only the first field
under-reported input spend by about a thousandfold — every run in the ledger
showed `in=22`. Cache writes bill at 1.25×, reads at 0.1×.

## Delegation

An agent can hire a specialist for one subtask mid-run:
`delegate_subtask(name, instruction, deliverable)` takes a worker from the
same room, runs it in the parent's directory, and returns what it made. The
specialist can call `request_review(reviewer, question, files)` to have
another room judge its work, then dies.

The constraints are all deliberate: **it blocks** (fire-and-forget would need
the parent to poll, which models handle badly); **`MAX_DEPTH = 1`** (recursive
hiring with a model deciding when to stop is a money fire); **`MAX_PER_RUN = 3`**,
held in the meta server's closure so it is per-turn.

Having the capability is not enough — an agent uses it only if its own prompt
says when to.

---

# How work moves

`Orchestrator._advance_records` is the transport, and it is deterministic: when
a record's stage changes, the room whose workbenches declare that stage gets
dispatched. No inference.

It used to run through an overseer agent reacting to reports, which put an LLM
reading an event log in the critical path — and it failed exactly as you would
expect. An agent finished and reported; the overseer's memory still held the
previous cycle, concluded the next room had already been dispatched, and
ignored the report. The work sat with nobody looking at it and no error
anywhere.

## Work has to get in somehow

`Orchestrator._advance_records` moves work that EXISTS. Nothing derives the
first record: the transport keys on a stage, and a record that has not been
created is at no stage, so the room that would create one is dispatched by
nothing — not the sweep, and not the app, whose Run buttons all hang off a
queue row.

Both sourcing rooms reached the same workaround independently, subclassing the
record handler and blanking the queue by hand with the same comment in each
plugin — *"it does not consume a queue, it creates one"* — and neither was
reachable from the app at all. `job_hunt` shipped with no way to start it.

So a plugin declares its openings with `starts()`, and the app builds the
control from the `inputs` rather than from a hardcoded panel. Declaring the
inputs is what makes that possible: Nova REFUSES an empty prompt, Scout takes
an optional set of boards, and a port commission is six fields typed by hand.
A bare button serves exactly one of those.

The input kinds are a small fixed vocabulary — the same bargain as the record
blocks, and unknown kinds degrade to a text box rather than being dropped.
A start is scoped to a castle by its `kind`, through the same map that decides
where a record lands, so an extension's opening is offered in the castle its
records actually go to.

## One dispatch per record per role

Several things can dispatch the same work in the same instant — an operator
button, the stage sweep, an overseer chaining off a report — and the
per-worker lock does not stop it, because each dispatch hires a *different*
worker.

`run_agent` therefore claims `(role, record_id)` **synchronously, before its
first await**. The loser raises `AgentBusy` and is logged as skipped.
Duplicate dispatch is treated as normal; the guarantee is that one proceeds.

Two safeguards on the sweep: on boot it seeds every record's current stage, so
a restart does not re-fire settled work; and a record sitting at a workable
stage with no worker and no pending approval is recovered once per
`(record, stage)`, at most one per tick, never if untouched for six hours.

**A pending approval suppresses dispatch entirely.** Gates belong to the
operator, and a card already waiting needs nothing from an agent.

## The rerun loop is braked twice

When an agent escalates and the overseer answers, the agent is re-fired so the
retry sees the guidance. That path is a natural infinite loop and ran as one:
a rerun with nothing new to attempt escalates again, which is answered again —
a full agent run plus a full overseer run every forty seconds.

Marking the escalation `rerun_dispatched` is **not** a brake: the new
escalation is a new record with a fresh allowance. So there are two:

1. **The overseer's veto.** Its response carries `rerun_agent`; when absent it
   is inferred from the guidance text. In the incident it said "stand down"
   five times and was re-fired every time, because nothing read it.
2. **A hard ceiling per task** (`state.MAX_TASK_RERUNS`, keyed by agent plus a
   hash of the task) that holds regardless of anyone's judgement. Hitting it
   raises a card, because an agent stuck on one task needs the operator.

---

# Asking the operator

`state.add_user_approval(kind, room_id, ...)` surfaces something for the
operator; the map draws a badge and the panel renders the cards. A `Gate`
declares what a kind MEANS and what deciding it does; a `StepGate` stops
before a step runs and asks.

A step gate is keyed by `(stage, pipeline)` rather than by an edge, because
the operator is asked BEFORE the room runs — at which point which edge it will
take is not yet known. Inferring "this is a gate" from "every transition out
of here is the operator's" also fails outright for a stage that has both.

`permanent=True` means it cannot be switched off from the settings panel.
Anything irreversible or outward-facing should be; those must never depend on
a checkbox.

`validate` is checked BEFORE the card is resolved. Refusing after resolving
consumes the card and leaves the work undone, which is a mistake this
environment has already made once.

---

# Prompts

Every agent role, output schema and tool description loads from
`plugins/<plugin>/prompts/<module>/<NAME>.md` via `tanrim/prompts.py`.

- `_P = prompts.loader("builder")` at the top of a module, then
  `ROLE = _P("ROLE")`.
  Read once and cached; restart to pick up an edit.
- **A missing or empty file raises**, naming the path. It must never fall back
  to an empty string: an agent with no instructions does not fail, it
  improvises.
- Resolution is **by kind**. `prompts.kind_loader` searches the plugins that
  declare that kind first, so an extension overrides one prompt without
  shipping the rest — and neither plugin knows the other exists.
- A plugin **declares** what it needs (`declares_prompts`) and `check()`
  reports at boot what is missing, so you find out before the first run.

---

# Tools and skills

A tool is a module exporting `mcp_server`, returned from `Plugin.tools()` and
granted to a room by name in its manifest. `state/tools/` is a runtime drop
for a tool that belongs to no plugin; it is empty by design, and every tool in
use today ships with the plugin that needs it.

There was once an Armory: an agent emitted `request_tool`, an overseer
reviewed it, and another agent wrote a module and hot-reloaded the registry.
In four weeks it produced two tools and nothing since — every tool actually
used was written by hand. A gatekeeper loop, an agent, a room, a panel and an
approval kind for a capability nobody reached for is cost without return.

## Skills

A room grants Claude Code skills via `skills:` in its manifest, and **the
plugin supplies them** — `Plugin.skills()` answers `name -> directory`, with
`plugin_helpers.dir_skills()` as the convenience for the ordinary case.

They used to live in one fixed directory the core owned, `<repo>/.claude/skills/`,
which put a plugin's dependency outside the plugin: cloning a plugin gave you a
room granting four skills and none of them. `resolve` drops a missing skill
without a word, so the only symptom was worse output. `<repo>/.claude/skills/`
survives as a drop for a skill that belongs to no plugin, exactly like
`state/tools/`; a plugin owns its own names and the drop cannot shadow one.

Four non-obvious parts:

- Claude Code discovers project skills relative to the run's **cwd**, and
  agents are scoped to a per-record directory. Rather than widen an agent's
  cwd to the repo, each run gets a `<cwd>/.claude` symlink pointing at a
  skills-only tree. Anything that walks or copies a run directory must
  therefore skip symlinked dirs.
- **One tree per distinct grant, not one shared tree.** The shared one only
  ever added, so it accumulated every skill any room had ever been granted —
  and Claude Code discovers what is in the TREE, not what the prompt mentioned.
  A room granted four could invoke a fifth belonging to another room, and now
  that plugins ship their own, to another plugin.
- A skill that ships as a plugin and calls its own script via
  `$CLAUDE_PLUGIN_ROOT` needs that set; the runner points it at whoever
  supplies the granted skills, and at their common ancestor for a mixed grant.
- A skill that works by shelling out needs `Bash` in the room's
  `builtin_tools`.

## Remote MCP servers

A room manifest can declare them, so granting a third-party toolset is a
manifest change rather than a code change:

```yaml
mcp_servers:
  - id: cloudflare
    url: https://mcp.cloudflare.com/mcp
    auth_env: CLOUDFLARE_API_TOKEN     # env var NAME, never the value
    tools: [docs, search]              # allowlist
    deny: [execute]
```

`tools` is an allowlist applied per tool name — a remote server decides what
it exposes and can add to it whenever it likes. `deny` exists because the
allowlist only blocks invocation: without it the model sees a tool, tries it,
is refused, and has burned a turn learning that. Verified both ways.

---

# The app

Flutter, `app/`. An isometric map of the world with floating windows over it.

- **Windows** are draggable, resizable and stack by what you last clicked.
  Each window's geometry is its own `ValueNotifier<Rect>` and its contents are
  passed as `child` to a `ValueListenableBuilder` — built once and handed
  through untouched — so dragging rebuilds the `Positioned` and nothing else.
- **Three zoom steps.** Close: rooms, benches and names. Middle: the layout
  alone, because at that distance text is grey fuzz over the thing you are
  looking at. Far: one block per castle. The approval badge survives at every
  distance; it is the one thing that means "come here".
- **The zoom floor adapts to the world.** A fixed floor is a promise that
  stops being true as soon as somebody builds far enough out.
- **A record opens as BLOCKS** — a small fixed vocabulary (`section`, `text`,
  `fields`, `list`, `table`, `images`, `timeline`, `raw`) the app knows how to
  draw. A plugin answers `record_view` with them, or says nothing and gets one
  inferred from its JSON. Variety lives in a per-value `format` rather than in
  more block types, because each block is a renderer in a binary that ships on
  its own schedule.
- **`source` is a property of a row**, not a block, so a renderer can mark
  which facts are cited without being told what the record is.
- **The timeline is the core's**, built from the history for every kind. A
  plugin cannot know it better and one that forgot would leave the only always-
  answerable part of a record unanswered.
- **Unknown blocks and unknown socket frames are drawn or ignored, never
  fatal.** The environment gains behaviour by gaining plugins.

Performance lessons that cost a day each: sprites were drawn one art pixel at
a time (~150 `drawRect` per figure, 5,000 a frame) and are now recorded once
per pose as a `Picture`; every label was laid out from scratch every frame and
is now cached; and a record's rows were built eagerly in a `ListView(children:)`,
601 of them in one frame, with every value a `SelectableText`.

---

# Adding to the environment

**Adding a room** = adding a manifest to a plugin. The backend serves the list
at `GET /rooms` and the app builds the map from it. Do not hardcode the room
list in either side.

**A room panel** = a `RoomHandler` registered under the room id.
`RecordRoomHandler` supplies the queue, the one-run-at-a-time guard and the
error surface; a subclass declares `agent_id`, `action_name`,
`accepts_stages` and a `run()`.

One handler is built **per room on the map**, not per room a plugin declares —
two castles of one plugin each need their own queue and their own in-flight
task. It is constructed with the world alone and told where it is afterwards,
so a plugin's handler does not have to change signature.

**A refusal after a run starts lands in `last_result`, not `last_error`.** A
task that declines does not throw. Half the refusals in this environment are
deliberate, and every one of them was invisible until the panel read it.
