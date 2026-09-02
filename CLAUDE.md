# agent_environment

Claude Agent SDK app that runs an **agent-operated web agency** as a pixel-art
world. Agents find local businesses that trade but have no website (or a bad
one), build them one on spec, verify the UI actually renders, publish a
preview, and email the owner a link plus a quote. Each workflow stage is a
**room**; each agent is a sprite. The operator passes two approval gates.

*(This project began as an Etsy print-on-demand pipeline and was pivoted
2026-09-01. The dungeon machinery was kept; the goal was replaced.)*

## Layout

```
rooms/                       declarative YAML manifests, read by both sides
backend/agent_env/           Python — Claude Agent SDK orchestrator + FastAPI WS server
frontend/                    Vite + TS + Phaser SPA — renders the world
state/                       JSON ledgers, generated sites, runtime-fabricated tools
```

## The Lead is the unit of work

`state/leads.json` holds one record per prospect. Every agent **enriches the
same record** and moves its `stage`; nothing reads "the most recent upstream
artifact", because many leads sit at different stages at once.

```
sourced → needs_review → qualified → enriched → visualised → built → qa_passed → published → contacted → replied → won
   ↓            ↓            ↓ ↑                               ↑ ↓                              ↓          ↓
   └────────────┴─→ disqualified└─(too thin)                qa_failed ←─ change request ────────┘     handover card
                                                                                                ↓
                                                                                              lost (declined, or
                                                                                               21 days of silence)
```

A change request from the business is **not** a special case: their words go into
`qa.problems` and the lead returns to `qa_failed` — the same path as an operator
rejection — so it walks the whole build, QA and publish loop again. Only
`revision.requested_by == "client"` marks it, which tells Forge the business has
already seen this page (change what they mentioned, leave the rest) and Scribe to
send a two-line note instead of pitching again.

`replied` means they accepted. Echo raises a `handover` card carrying the
checklist, and approving that card is what marks the lead `won` — registering a
domain is irreversible and spends real money, so none of the handover is
automated. `config.NO_REPLY_DAYS` turns silence into `lost` on a timer.

`needs_review` is where a lead goes when it turns out to already HAVE a
website. Nothing may call an existing site bad until Lens has rendered it in a
real browser and looked at it — see "Judging an existing site" below.

Agents are always addressed with a `lead_id` (except Nova, which takes a
place). `state.advance_lead()` is the only way stage changes — it appends to
the lead's `history` in the same write, so who moved what and why is always
recoverable.

## Rooms

| Room | Agent | Function |
|---|---|---|
| Throne | Ultron | Reads the lead board, dispatches one lead to one room |
| Watchtower | Nova | Sources businesses without websites (OpenStreetMap) |
| Assay Room | Probe | `sourced`: qualify cheaply · `qualified`: research the dossier |
| Factory | Forge | Writes the actual site to `state/sites/<lead_id>/` |
| Gallery | Lens | Renders and **looks**: their site, their photos, our build, plus reviews for other agents |
| Copy Desk | Scribe | Site copy, and the outreach email + quote |
| Shipping Bay | Courier | Publishes a preview — **gate 1** |
| Communications | Echo | Sends the outreach — **gate 2** |
| Archives | Sage | Feedback ledger + activity log, fed back into agent context |
| Armory | Tinker | Fabricates new MCP tools at runtime |
| Treasury | Coin | Token spend per agent; cost per lead |
| War Room | (gathers) | Retro |

## Rules the code enforces, not the prompts

These are the ones that must not depend on a model behaving:

- **Quote, never invoice.** An unsolicited invoice is a deceptive-billing
  pattern. Scribe is told this, and `run_outreach` additionally scans the draft
  for billing language and flags it; Echo's preflight blocks a flagged draft.
- **Identifiable sender + opt-out.** `config.outreach_footer()` is appended in
  code so it cannot go missing, and `outreach_config_problems()` blocks sending
  entirely until `AGENT_ENV_AGENCY_NAME` and `AGENT_ENV_SENDER_EMAIL` are set.
- **No contact route, no lead.** Probe's verdict is overridden to
  `disqualified` if it qualified a lead without an email.
- **Previews are marked.** Courier injects `noindex` and an "unofficial
  preview, not affiliated" banner into every published page.
- **QA cannot pass unseen.** Lens's `pass` is downgraded to `fail` if
  `visually_verified` is false. A build that never rendered never ships.
- **Forge must actually produce files.** If `index.html` is absent after a
  build, the run fails and the lead reverts, whatever the model reported.
- **An existing site can't be condemned unseen.** Probe's `qualified` is
  overridden to `needs_review` whenever `existing_site.url` is set, and Lens's
  `rebuild_worth_it` is downgraded to `their_site_is_fine` if
  `visually_verified` is false. Both failures bias towards leaving the business
  alone.

## The dossier

Map data is a name, an address and a phone number. That builds a business card,
not a website — a restaurant page with no menu is pointless. So qualification
and research are **two separate passes in the Assay Room**, split by stage:
`sourced` → qualify (cheap, and most leads fail here), `qualified` → research
(expensive, only for leads we've committed to).

`probe.run_enrich` has `WebSearch` and `WebFetch` and produces
`lead["profile"]`: itemised offering with prices, verified hours, specialities,
practical facts, contact routes, and a `build_readiness` of
`ready` | `thin` | `not_enough`. `not_enough` parks the lead back at
`qualified` and raises a `thin_content` card rather than building a page with
nothing on it.

Two rules the schema enforces:

- **Every fact carries a source URL.** Anything uncited goes in `unverified`,
  and Forge is forbidden from using it. Deep research raises the fabrication
  risk, so the dossier is the *only* source of page facts and each one is
  traceable.
- **Reviews are context for us, never content for the page.**
  `reputation_for_us_only` tells the writer what to lead with. Reproducing
  review text on a commercial page is a copyright problem and, misattributed, a
  lie — and Forge's no-testimonials rule still stands.

Conflicts are reported, not resolved silently. On the first real dossier, two
sources disagreed on closing time (17:00 vs 19:00) and two different phone
numbers were in circulation; both landed in `hours.conflicts` and
`content_gaps` as "must call before building". That is the correct outcome —
the alternative is publishing a confident wrong fact to the owner.

## Reading their photographs

Text research establishes that a business exists. Photographs establish what its
walls actually look like and, often, what is chalked on the board. At the
Gallery's *Light Box*, Lens harvests published photos (`collect_images`) and
opens every one.

On the first lead this returned things no text source anywhere had: the dominant
wall colour (`#C0352A` terracotta, where we had guessed burgundy from "French
bistro"), two priced items off a chalkboard (*Planche charcuterie 13€*,
*Viognier Pays d'Oc 13€*), the guitars and violins on the walls, a coin-mosaic
room divider, and the fact that **no exterior signage is legible in any of the
twelve photographs** — which is why that lead needs a logo designed rather than
reproduced.

Priced items read off a board are folded into the dossier's `offering.items`
with `from_photo: true`, because a photograph may be years old: Forge presents
them as examples of what they serve, never as a current price list.

Rights are tracked per photo. Review-platform photos are user-submitted and
platform-hosted — we READ them for information and never republish them. The
build uses captioned image *slots* the owner fills, not other people's
photographs.

## Judging an existing site

The single most expensive mistake this pipeline can make is telling a business
their working website is broken. It happened on the first real lead:
`example-bistro.fr` returned **403 to every HTTP client and 200 to a browser** —
its WAF refuses scripts. The audit scored that as `opportunity_score: 100,
"site does not load at all"`, and the outreach draft opened by telling a
restaurant with a working WooCommerce shop (and a second location we hadn't
noticed) that their site was down.

So the rules are:

- `audit_website` is a plain HTTP fetch. It **cannot** see JavaScript-rendered
  content, and healthy sites routinely refuse it. It returns
  `inconclusive: true` with `opportunity_score: null` for 401/403/406/429/503
  and for any non-DNS failure. Only a domain that does not resolve is treated
  as hard evidence.
- Probe therefore never judges site quality. It finds the URL and hands off.
  Its fetch findings are carried as `unverified_observations`, never `defects`.
- `render_url` (Playwright, 390px + 1280px) is the only thing that decides.
  Lens opens the PNGs with `Read` and judges by eye, and its verdict records
  `probe_was_wrong_about` — on the first run that list had four entries.
- Lens's default is "leave them alone". Dated-but-tidy, page-builder, or
  e-commerce sites are all disqualifications: we cannot beat "already paid for
  and working".

Reading images through the SDK needs `max_buffer_size` raised well above the
1 MB default (a single page render overflows it and kills the run), and
screenshots are height-capped at 4000px.

## Workbenches

A room's jobs are declared as **workbenches** in its manifest — an id, a name, a
one-line `job`, and the lead `stages` worked there. That is the whole contract:
adding a bench is a few lines of YAML and needs no Python or TypeScript change.
Geometry is computed by `rooms._layout_workbenches` if you omit it, so you never
do the tile arithmetic; give a `position`/`size` only to override.

Benches are the single source of truth for routing. `rooms.stages_for_role()`
derives what a room accepts from its bench declarations, and `runners._wrong_stage`
refuses a lead that arrives at the wrong stage — which is a real guard, not a
formality: Ultron chains the next agent off `report_to_ultron`, and that fires
*mid-run*, before the reporting agent has persisted anything. Forge once built a
site from a lead whose photo report Lens had not finished writing.

The sprite walks to the bench for the duration of a job (`world.move_to_workbench`)
and returns to the idle strip at the bottom of the room afterwards. The map draws
each bench as a labelled plate; the panel shows one tab per bench, scoped to that
bench's queue and whoever is standing at it.

Current benches: Assay has *Weighing Bench* / *Dossier Desk*; the Gallery has
*Incumbent Wall* / *The Light Box* / *Inspection Bay* / *Comparison Bench*; the
Factory has *The Build Floor* / *Craft Bench*; the Copy Desk has *Copy Bench* /
*The Pitch Desk*; Communications has *Outbox* / *Inbox*.

## Delegation

An agent can hire a specialist for one subtask mid-run: `delegate_subtask(name,
instruction, deliverable)` takes a worker from the same room, runs it in the
parent's directory at the *Craft Bench*, and returns what it made plus how to
use it. The specialist can call `request_review(reviewer, question, files)` to
have another room judge its work — Lens actually renders and looks — and iterate
before handing back. Then it dies; the sweep retires its worker.

The design constraints, all deliberate:

- **It blocks.** The parent asked for a thing and gets the thing back as the
  tool result. Fire-and-forget would need the parent to poll, which models
  handle badly.
- **`MAX_DEPTH = 1`.** A specialist cannot delegate. Recursive hiring with a
  model deciding when to stop is a money fire.
- **`MAX_PER_RUN = 3`,** held in the meta server's closure so it is per-turn.
- Exposure is conditional: `delegate_subtask` only when depth allows and the
  agent has a `cwd`; `request_review` whenever there is a `cwd`.

Having the capability is not enough — an agent uses it only if its own prompt
says when to. Forge's does: a logo when the business has no legible signage in
any photograph, a CSS motif, an icon set; never the page, the copy, or anything
doable in a line.

## Rooms are staffed, not single-agent

A room's manifest agent is a **role**, not one worker. `rooms/<id>.yaml` sets
`max_workers` (3 for every pipeline room); when a second lead needs a room whose
workers are all busy, another is hired — `forge` → `forge-2` ("Forge II") —
and retired once its lead finishes the pipeline. The base worker is permanent,
so a room never looks abandoned. Ultron is a singleton by construction
(`workers.SINGLETON_ROLES`): a second overseer would dispatch against the first.

The split that matters, and it runs through the whole codebase:

- **The role owns memory and context** — escalations, tool history, past
  outputs, the meta MCP server. Every Forge shares one memory.
- **The worker owns the lock, the sprite, the status and the log line.**
  `run_agent(role=...)` calls `workers.acquire()` to pick one, and returns its
  id as `RunResult.worker_id` so agent modules attribute their own `run_end`
  to the individual rather than the role.

Assignment order: a free worker already on this lead (so the same sprite follows
a lead across repeat visits) → any free worker → hire one → `RoomAtCapacity`.
That last one is a normal outcome, not an error: the lead simply stays at its
stage and Ultron can dispatch it again.

Retirement runs on the orchestrator's tick (`workers.sweep`) and only touches
idle, ephemeral, unlocked workers whose lead has reached a stage in
`workers.DONE_STAGES`. The frontend creates a sprite on first sight of an
unknown agent id, so hiring needs no new wire event; retiring emits
`agent_removed`.

## Skills

A room grants skills to its agent via `skills:` in its manifest; they live under
`<repo>/.claude/skills/<name>/` with provenance in
`.claude/skills/sources.json` (which is what makes each skill chip in the room
panel a link to its upstream repo). `agent_env/skills.py` resolves, describes
and installs them.

The wiring has three non-obvious parts:

- Claude Code discovers project skills relative to the run's **cwd**, and our
  agents are scoped to a per-lead build directory. Rather than widen an agent's
  cwd to the repo (letting a file-writing agent roam), each run gets a
  `<cwd>/.claude` symlink pointing at `state/agent_home/.claude` — a
  skills-only tree, so no settings, hooks or MCP config leak in. Anything that
  walks or copies a build directory must therefore skip symlinked dirs.
- `ui-ux-pro-max` ships as a plugin and calls its own search script via
  `$CLAUDE_PLUGIN_ROOT`. The runner sets that to the repo root so the path
  resolves without patching vendored content.
- Because that skill works by shelling out to Python, a room granted it also
  needs `Bash` in `builtin_tools`. Forge has it, scoped to the lead's build dir.

Forge is told to query the skill for palette, type and touch-target rules and to
report what it got back in `design_rationale` — which is also how you check it
actually used it rather than inventing hex codes.

## Prompts live outside the source tree

Every agent role, output schema and MCP tool description loads from
`prompts/<module>/<NAME>.md` via `agent_env/prompts.py`. `prompts/` is
**gitignored** — the prompts are the part of this project worth keeping
private — and `prompts.example/` is committed with a stub per file describing
what it is for, with no excerpt of the real text.

- `_P = prompts.loader("forge")` at the top of a module, then
  `ROLE = _P("ROLE")`. Read once and cached; restart to pick up an edit.
- A missing or empty file raises `MissingPrompt` naming the path and pointing
  at the stub. It must never fall back to an empty string: an agent with no
  instructions does not fail, it improvises.
- `server.py` calls `prompts.check_all()` at boot and prints anything missing,
  so you find out before the first run rather than during it.
- Four short framing strings are still inline (`forge` NO_DOSSIER,
  `tinker` secrets note, `agent_helpers` secrets notice, `ultron` react tail).
  They are conditional fragments woven into f-strings; extracting them would
  cost more readability than it buys privacy.

## Modularity contract

Adding a room = adding `rooms/<id>.yaml`. The backend serves the manifest list
at `GET /rooms` and the frontend builds the map from it. **Do not hardcode the
room list** in either side.

### Per-room menus

1. **Backend handler (optional)** — `handlers.py` registers a `RoomHandler`
   under the room id. Pipeline rooms subclass `LeadRoomHandler`, which supplies
   the queue, the one-run-at-a-time guard and the error surface; a subclass
   only declares `agent_id`, `action_name`, `accepts_stages` and a `run()`.
2. **Frontend renderer (optional)** — `frontend/src/panels/<id>.ts` exporting
   `open(roomId)`, registered in `panels/index.ts`. Most pipeline rooms are one
   call to `makeLeadRoom({...})` in `panels/leadRoom.ts`.

### Agent runs

`agent_helpers.run_agent()` owns every agent's turn: sprite busy state, MCP
server assembly (meta tools + the room's resolved tools), streaming, token
accounting, logging, and a per-agent `asyncio.Lock` so Ultron's chained
dispatch can't race the operator's button. Two non-obvious details it handles:

- Built-in file tools (`Write`/`Read`/`Edit`) only **execute** when
  `tools={"type": "preset", "preset": "claude_code"}` is set. Without it the
  model calls Write, the call silently no-ops, and the agent reports success
  having written nothing.
- `strict_mcp_config=True` + `setting_sources=[]`, so MCP servers from the
  operator's own Claude Code config don't leak into agent runs.

If the final message isn't the expected JSON, `run_agent` retries once with the
bad output quoted back — agents otherwise lose a whole run by mistaking a
`report_to_ultron` call for delivering their work. That retry is **text-only and
single-turn** (`allowed_tools=[]`, `max_turns=1`), and takes the output contract
from the `schema=` argument rather than re-sending the original prompt. The
first version re-sent the whole prompt with the original options, so Forge —
told again to "write the files now", with its file tools still attached — rebuilt
the entire site a second time, ran 11 minutes, and overwrote work Lens had
already verified. Every agent with side effects passes `schema=`; Forge also
carries a `max_budget_usd` ceiling.

### Tool creation (agent → Ultron → Tinker)

Unchanged from the original design: an agent emits `request_tool`, the
gatekeeper loop polls, `ultron.review` approves/denies (risky requests escalate
to a `tool_review` user approval), `tinker.fabricate` writes a module to
`state/tools/<name>.py` and hot-reloads the registry, and the name is appended
to that room's overrides in `state/room_tool_overrides.json`.

Static tools shipped with the pivot: `osm_business_search` (Overpass, with
mirror fallback), `site_audit` (fetch + defect scoring), `site_inspect`
(structural checks + Playwright screenshots at 390px/1280px).

### How work actually moves between rooms

`Orchestrator._advance_leads` is the transport, and it is deterministic: when a
lead's stage changes, the room whose workbenches declare that stage gets
dispatched. No inference.

It used to run through Ultron reacting to `report_to_ultron`, which put an LLM
reading an event log in the critical path — and it failed exactly as you would
expect. Forge finished a rebuild and reported; Ultron's memory still held the
*previous* cycle's QA pass and courier dispatch, concluded "Courier was already
dispatched after Lens's QA pass", and ignored the report. The build sat at
`built` with nobody looking at it and no error anywhere.

Ultron still reacts to reports, and his prompt now says the pipeline moves work
on its own — so he supervises and comments rather than being the wire.

**One dispatch per lead per role.** Several things can dispatch the same work in
the same instant — an operator decision, the stage sweep, Ultron chaining off a
report — and the per-worker lock does not stop it, because each dispatch simply
hires a *different* worker. Rejecting a build put `forge` and `forge-2` on the
same lead two seconds apart, both writing the same site directory.

`run_agent` therefore claims `(role, lead_id)` **synchronously, before its first
await**, so two dispatches in one tick cannot both pass. The loser raises
`AgentBusy` and is logged as skipped. Duplicate dispatch is treated as normal
and expected; the guarantee is that only one of them proceeds.

Two safeguards on the sweep:

- On boot it seeds every lead's current stage, so a restart does not re-fire
  work that is already settled.
- A lead sitting at a workable stage with no worker on it and no pending
  approval is recovered — once per `(lead, stage)`, at most one per tick, and
  never if it has been untouched for six hours. That is what unsticks a lead
  after a crash without a thundering herd at startup.

A pending approval on a lead suppresses dispatch entirely: gates belong to the
operator, and a card already waiting needs nothing from an agent.

### The rerun loop, and why it is braked twice

When an agent escalates, Ultron answers and the gatekeeper re-fires the agent on
its original task so the retry sees the guidance. That path is a natural
infinite loop and it ran as one: a rerun with nothing new to attempt escalates
again, which is answered again, which reruns again — a full agent run plus a
full Ultron run every forty seconds, until the agent happened to stop asking.

Marking the escalation record `rerun_dispatched` is **not** a brake: the new
escalation is a new record with a fresh allowance. So there are two:

1. **Ultron's veto.** His escalation response carries `rerun_agent`. He is told
   to set it false when telling an agent to stand down, when the operator has
   closed the work, or when re-asking an answered question. In the incident he
   said "stand down" five times and was re-fired every time, because nothing
   read his guidance. When the field is absent it is inferred from the guidance
   text, so an older or sloppier response still brakes.
2. **A hard ceiling per task** (`state.MAX_TASK_RERUNS`, keyed by agent + a hash
   of the task) that holds regardless of anyone's judgement. Hitting it stops
   the reruns and raises a `rerun_halted` card, because an agent stuck on one
   task needs the operator, not another attempt. The denied-tool rerun path
   shares the same ceiling.

### The outreach email is a template, not a fresh invention

`prompts/scribe/OUTREACH_TEMPLATE.md` holds the canonical email: the process
facts — what the offer is, what is included, that it is unsolicited and carries
no obligation — plus slots marked `{{ADAPT}}` that Scribe writes per business.
`{{PREVIEW_URL}}`, `{{PRICE}}` and `{{DOMAIN}}` are substituted before the
prompt is built.

The split is the point. The personalised parts *should* vary; the description of
what someone is buying should not. Regenerating "what you get for €450" from
scratch on every lead is how a customer ends up misled about the offer, and it
is the one part of the email a misdescription actually matters in.

`{{KEEP}}` sections may be reworded for tone but must not change what they
promise or be dropped.

The opt-out and sender identity are still appended in code so they cannot go
missing — but `config.outreach_footer(language)` now writes them in the
language of the email. A French business receiving a French pitch with an
English legal notice reads as a template, which undermines the one paragraph
that has to be believed.

### Attaching a third-party MCP server to a room

`rooms/<id>.yaml` can declare remote MCP servers, so granting a room a
third-party toolset is a manifest change rather than a code change:

```yaml
mcp_servers:
  - id: cloudflare
    url: https://mcp.cloudflare.com/mcp
    auth_env: CLOUDFLARE_API_TOKEN     # env var name, never the value
    tools: [docs, search]              # allowlist
    deny: [execute]                    # and don't even offer this one
```

Three details that matter:

- **`tools` is an allowlist, applied per tool name** rather than the
  `mcp__<server>__*` wildcard used for local tool servers. A remote server
  decides what it exposes and can add tools whenever it likes; a room gets the
  ones it was granted. Omitting `tools` grants everything, now and in future,
  which is almost never right.
- **`deny` exists because the allowlist only blocks invocation.** The server
  still advertises everything it has, so without a deny the model sees a tool,
  tries it, is refused, and has burned a turn learning that. Verified both
  ways: with only the allowlist the agent called `execute` and got "requires
  explicit user permission"; with the deny it is not listed at all.
- **`auth_env` names an environment variable.** Manifests are committed;
  secrets are not. A missing variable skips the server and logs why rather
  than failing the run.

No room currently uses one. Cloudflare's server is left commented in
`rooms/publish.yaml` as the worked example: deploying is done deterministically
by `hosting.py`, and Courier makes no model call in the publish path, so the
tools would never be reached. Attaching an unused remote server just adds a
handshake and two tools to every run's context.

It did pay for itself once while being evaluated — asked for the custom-domain
endpoint, `search` returned
`POST /accounts/{account_id}/pages/projects/{project_name}/domains`, which is
what the Launch Pad will need.

### Hosting, and where domains come from

`agent_env/hosting.py` deploys an approved build to Cloudflare Pages, giving a
public `<slug>.pages.dev` URL. Before this, the "preview link" in an outreach
email was `127.0.0.1` — unopenable by the person it was written for, which made
the whole outreach step a dead end.

It is plain HTTP, not a model call: putting a built site on a URL is mechanical
and must not vary. Cloudflare also publishes an MCP server (`docs`, `search`,
`execute` over 2,500+ endpoints, and it accepts a plain API token as a bearer so
it works headlessly) — that is worth having for diagnosing a failure or a
one-off change, but not for the deploy itself.

The direct-upload flow is barely documented, so it was established empirically:
`upload-token` → `assets/check-missing` → `assets/upload` → `deployments` with a
`manifest` field mapping each path to a content hash. Cloudflare's own tooling
hashes with blake3, which is not in the stdlib, but the hash is an opaque
content key — a stable md5 works, verified against the live API. A brand-new
project 522s for a few seconds while its certificate provisions.

`domains.py` checks availability over **RDAP**, the protocol that replaced
WHOIS: free, keyless, answered by the registry. It reports availability only.
Registration is irreversible and spends real money, so a human buys the domain
at the registrar and pastes it back — the system never holds a card. Courier
generates candidates at publish time so the outreach email can name a free one,
and the prompt insists on "available", never "reserved": someone can take it
between the email and the reply, and promising a domain we do not hold is the
kind of small dishonesty that loses a client at the worst moment.

### Seeing a build before approving it

`/staging/<lead_id>/` serves any build straight off disk, published or not, and
the `publish_site` approval card embeds it in an iframe at 390px. The gate asks
"may this go out" — answering that without seeing the page is a rubber stamp,
and until this existed the only URL appeared *after* approving.

Rejecting sends the lead back to `qa_failed` **with the operator's reason merged
into `qa.problems` as a critical**, because that is where Forge reads its
rebuild instructions from. A rejection whose reason went only into a history
note would produce a rebuild identical to the one you rejected.

### Judging what is on the page

Whoever checks a page for invented facts must hold the same evidence the builder
had. Lens's QA prompt is given the dossier and the photo report in full. It did
not used to be, and the result was a build failed on four "critical" fabrications
— a named front-of-house, the opening hours, two dishes and a price range — every
one of which was sourced in the dossier Lens had not been shown. A false
fabrication flag is worse than a missed one: it sends back a good build and
teaches Forge to strip out true, specific detail.

A recorded *conflict* is different from an invented fact. Where the dossier says
two sources disagree, a page stating one as settled fact is a real defect — the
missing caveat is the fault, not the fact.

### Why a build is slow, and what to look at

Output generation is serial and runs at roughly 50–80 tokens/second, so a run's
wall-clock is almost entirely its **output token count**. Forge builds were
taking 15 minutes; the measurement:

- prompt: ~7,300 tokens (not the problem)
- finished site: index.html + styles.css ≈ 6,500 tokens
- output generated: **63,319 tokens — 9.7× the finished artifact**

`Write` re-emits the whole file every call, so a build that writes each file
four or five times pays for the site nine times over. The fix is instruction
rather than architecture: write each file once, use `Edit` for every subsequent
change, and cap turns (60 → 32) because Lens is the reviewer and Forge polishing
alone for ten minutes helps nobody. `site.run_stats` now records output tokens,
tool calls, and the Write/Edit split, so the next time a build drags you can see
which it was instead of guessing.

### Token accounting

`usage.record()` takes `cache_write` and `cache_read` alongside fresh input.
This matters more than it sounds: the SDK reports a 7,000-token prompt as
`input_tokens: 10` with the remainder under `cache_creation_input_tokens`, so
counting only the first field under-reported input spend by about a thousandfold
— every Forge run in the ledger showed `in=22`. Cache writes bill at 1.25× the
input rate, cache reads at 0.1×. Records carry `billed_input_tokens` as the
honest total; older rows lack it and the Treasury falls back to the old field.

### Alerting the operator

The approval gates only buy anything if you can walk away, so
`frontend/src/notify.ts` reaches you without the tab being focused: a WebAudio
chime (synthesised, no asset), a `(n)` count in the tab title, and an optional
desktop notification when the tab is hidden. Toggles live in the crew header and
persist in `localStorage`.

It fires only for rooms whose pending count **went up** — resolving an approval
also changes the totals, and re-chiming on your own click is the quickest way to
make someone mute an alert for good. The first sync of a session badges any
existing backlog without chiming for it. Browsers block audio until a real
gesture, so the AudioContext is created lazily by `installUnlockHandlers()`.

### User approvals + room badges

`state.add_user_approval(kind, room_id, ...)` surfaces something for the
operator; the map draws a badge and the panel renders the cards. Kinds today:
`tool_review`, `escalation_alert`, `ultron_message`, and the two pipeline
gates `publish_site` and `send_outreach` — the latter two get bespoke card
rendering in `approvals.ts`, because a JSON dump is not good enough when the
decision is "does a stranger receive this email".

## Running

```
uv venv .venv && uv pip install --python .venv/bin/python -e .
.venv/bin/python -m playwright install chromium     # Lens needs a browser
cp .env.example .env                                # ANTHROPIC_API_KEY at minimum
PYTHONPATH=backend .venv/bin/python -m uvicorn agent_env.server:app --port 8765
# in another terminal:
cd frontend && npm install && npm run dev           # http://localhost:5173
```
