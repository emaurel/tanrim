# Vendored agent skills

Skills an agent can be granted by listing them in its room manifest
(`rooms/<id>.yaml`, key `skills:`). They are loaded per-run by
`agent_helpers.run_agent()`, which passes `skills=[...]` plus
`setting_sources=["project"]` so Claude Code discovers this directory.

## ui-ux-pro-max

Vendored from https://github.com/nextlevelbuilder/ui-ux-pro-max-skill (MIT),
v2.13.0 — the `.claude/skills/ui-ux-pro-max/` subtree only, which is
self-contained (SKILL.md + data + references + scripts, ~3.6 MB of CSVs).

Its SKILL.md invokes its search script as
`${CLAUDE_PLUGIN_ROOT}/.claude/skills/ui-ux-pro-max/scripts/search.py`,
because upstream ships it as a plugin. Rather than patch vendored content, the
runner sets `CLAUDE_PLUGIN_ROOT` to the repo root so that path resolves as-is.
Consequence: a room granted this skill also needs the `Bash` tool, since the
skill works by shelling out to Python.

To update: re-clone upstream and copy the same subtree; do not edit files here.
