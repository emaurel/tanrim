"""Skills belong to the plugin that needs them.

They used to live in one fixed directory the core owned,
`<repo>/.claude/skills/`, and a room granted them by name. That put a plugin's
dependency outside the plugin: cloning `web_agency` gave you a Factory that
grants four design skills and none of the skills — and `resolve` drops a
missing one in silence, so the only symptom was worse pages.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from tanrim import environment, skills


def make_skill(root: Path, name: str, description: str = "does a thing") -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(textwrap.dedent(f"""\
        ---
        name: {name}
        description: {description}
        ---
        # {name}
        """))
    return d


def test_a_plugin_supplies_its_own(plugins, tmp_path):
    """The whole point: the name resolves to something the PLUGIN ships."""
    shipped = make_skill(tmp_path / "alpha_skills", "palette")
    plugins.install({"alpha": f'''
        class Alpha(Plugin):
            id, name = "alpha", "Alpha"
            def skills(self):
                return {{"palette": Path({str(shipped)!r})}}
        PLUGIN = Alpha()
    '''})
    assert environment.current().skills()["palette"] == shipped
    assert "palette" in skills.available()
    assert skills.resolve(["palette", "absent"]) == ["palette"]


def test_two_plugins_cannot_claim_one_name(plugins, tmp_path):
    """A room grants by NAME, so a collision would hand it something it has
    never seen. Refused at boot, like a duplicate gate or tool."""
    a = make_skill(tmp_path / "a", "palette")
    b = make_skill(tmp_path / "b", "palette")
    with pytest.raises(Exception) as caught:
        plugins.install({
            "alpha": f'''
                class Alpha(Plugin):
                    id, name = "alpha", "Alpha"
                    def skills(self): return {{"palette": Path({str(a)!r})}}
                PLUGIN = Alpha()
            ''',
            "beta": f'''
                class Beta(Plugin):
                    id, name = "beta", "Beta"
                    def skills(self): return {{"palette": Path({str(b)!r})}}
                PLUGIN = Beta()
            '''})
    assert "palette" in str(caught.value)


def test_an_agent_sees_only_what_its_room_was_granted(plugins, tmp_path,
                                                      monkeypatch):
    """The tree Claude Code discovers is the tree, not the prompt.

    One shared `agent_home` only ever ADDED, so it accumulated every skill any
    room had ever been granted and every agent could invoke all of them. A
    Factory granted four could reach a fifth belonging to another room — and,
    once plugins ship their own, another plugin.
    """
    monkeypatch.setattr(skills, "AGENT_HOME", tmp_path / "home")
    root = tmp_path / "skills"
    for n in ("palette", "type", "secret"):
        make_skill(root, n)
    plugins.install({"alpha": f'''
        from tanrim.plugin_helpers import dir_skills
        class Alpha(Plugin):
            id, name = "alpha", "Alpha"
            def skills(self): return dir_skills(Path({str(root)!r}))
        PLUGIN = Alpha()
    '''})

    first, second = tmp_path / "run1", tmp_path / "run2"
    first.mkdir(); second.mkdir()
    skills.prepare(first, ["palette", "type"])
    skills.prepare(second, ["palette"])

    def visible(cwd: Path) -> list[str]:
        return sorted(p.name for p in (cwd / ".claude" / "skills").iterdir())

    assert visible(first) == ["palette", "type"]
    assert visible(second) == ["palette"], "a run saw a skill it was not granted"


def test_a_missing_skill_is_dropped_rather_than_faked(plugins, tmp_path):
    """`resolve` filtering is what makes a forgotten fetch silent, so it is
    worth pinning: it returns fewer names, never a broken one."""
    shipped = make_skill(tmp_path / "s", "palette")
    plugins.install({"alpha": f'''
        class Alpha(Plugin):
            id, name = "alpha", "Alpha"
            def skills(self): return {{"palette": Path({str(shipped)!r})}}
        PLUGIN = Alpha()
    '''})
    assert skills.resolve(["palette", "gone"]) == ["palette"]
    assert skills.describe(["gone"]) == ""
    assert "palette" in skills.describe(["palette", "gone"])


def test_the_drop_still_works_and_cannot_shadow_a_plugin(plugins, tmp_path,
                                                         monkeypatch):
    """`.claude/skills/` survives as a home for a skill that belongs to no
    plugin, exactly like `state/tools/` — but a plugin owns its own names."""
    drop = tmp_path / "drop"
    make_skill(drop, "loose")
    make_skill(drop, "palette", description="the drop's version")
    owned = make_skill(tmp_path / "owned", "palette", description="the plugin's")
    monkeypatch.setattr(skills, "DROP_DIR", drop)
    plugins.install({"alpha": f'''
        class Alpha(Plugin):
            id, name = "alpha", "Alpha"
            def skills(self): return {{"palette": Path({str(owned)!r})}}
        PLUGIN = Alpha()
    '''})
    found = skills.index()
    assert found["loose"] == drop / "loose"
    assert found["palette"] == owned, "the drop shadowed a plugin's skill"
