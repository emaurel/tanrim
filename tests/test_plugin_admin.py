"""Installing, disabling and removing a plugin from outside the process.

The dangerous one is removing. A plugin's prompts are gitignored on purpose —
they are the part of this project worth keeping private, so they exist on
exactly one machine — and `rm -rf plugins/web_agency` therefore destroys 88
files that no checkout brings back, while looking exactly like removing
something cloned this morning.
"""
from __future__ import annotations

import subprocess

import pytest

from tanrim import discovery, plugin_admin


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A `plugins/` directory inside its own git repository."""
    root = tmp_path / "repo"
    (root / "plugins").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / ".gitignore").write_text("plugins/*/prompts/\n")

    monkeypatch.setattr(discovery, "ROOT", root)
    monkeypatch.setattr(discovery, "PLUGINS_DIR", root / "plugins")
    monkeypatch.setattr(plugin_admin, "ROOT", root)
    monkeypatch.setattr(plugin_admin, "PLUGINS_DIR", root / "plugins")
    return root


def _plugin(home, name: str, *, prompts: bool = False) -> None:
    d = home / "plugins" / name
    d.mkdir(parents=True)
    (d / "plugin.py").write_text(
        "from tanrim.contract import Plugin\n"
        f"class P(Plugin):\n    id = {name!r}\n    name = {name!r}\n"
        "PLUGIN = P()\n")
    if prompts:
        (d / "prompts").mkdir()
        (d / "prompts" / "ROLE.md").write_text("the only copy of this text")
    subprocess.run(["git", "add", "-A"], cwd=home, check=True)
    subprocess.run(["git", "commit", "-qm", name], cwd=home, check=True)


def test_a_disabled_plugin_is_on_disk_but_not_found(home):
    _plugin(home, "alpha")
    assert [p.id for p in discovery.find()] == ["alpha"]

    plugin_admin.disable("alpha", reason="misbehaving")
    assert discovery.find() == []

    # But it is still visible, which is the whole point — something you cannot
    # see is something you cannot switch back on.
    entry = discovery.catalog()[0]
    assert entry["id"] == "alpha"
    assert entry["enabled"] is False
    assert "misbehaving" in entry["reason"]

    plugin_admin.enable("alpha")
    assert [p.id for p in discovery.find()] == ["alpha"]


def test_describing_a_disabled_plugin_does_not_run_it(home):
    """Importing IS running a plugin's code.

    One disabled because it misbehaves must not get to misbehave merely by
    being listed in the panel.
    """
    d = home / "plugins" / "bad"
    d.mkdir(parents=True)
    (d / "plugin.py").write_text("raise RuntimeError('I ran')\n")
    (d / plugin_admin.DISABLED).write_text("off\n")

    # Listed without being imported.
    assert [e["id"] for e in discovery.catalog()] == ["bad"]
    assert discovery.find() == []

    # And this is what the marker is standing between you and: enabled, the
    # same plugin runs its module body and takes the boot down with it.
    plugin_admin.enable("bad")
    with pytest.raises(discovery.DiscoveryError):
        discovery.find()


def test_removing_refuses_when_it_would_lose_the_prompts(home):
    _plugin(home, "alpha", prompts=True)
    lost = plugin_admin.unrecoverable("alpha")
    assert lost == ["plugins/alpha/prompts/"]

    with pytest.raises(plugin_admin.PluginAdminError) as e:
        plugin_admin.remove("alpha")
    assert "permanently" in str(e.value)
    assert "Disable it instead" in str(e.value)
    assert (home / "plugins" / "alpha" / "prompts" / "ROLE.md").exists()


def test_removing_a_plugin_git_can_restore_is_allowed(home):
    _plugin(home, "alpha")
    assert plugin_admin.unrecoverable("alpha") == []
    plugin_admin.remove("alpha")
    assert not (home / "plugins" / "alpha").exists()


def test_bytecode_is_not_treated_as_something_to_lose(home):
    """A check that cries wolf is how a warning stops being read."""
    _plugin(home, "alpha")
    cache = home / "plugins" / "alpha" / "__pycache__"
    cache.mkdir()
    (cache / "plugin.cpython-312.pyc").write_bytes(b"\x00")
    assert plugin_admin.unrecoverable("alpha") == []


def test_force_deletes_anyway(home):
    """The operator is allowed to mean it — and is told what went."""
    _plugin(home, "alpha", prompts=True)
    out = plugin_admin.remove("alpha", force=True)
    assert out["lost"] == ["plugins/alpha/prompts/"]
    assert not (home / "plugins" / "alpha").exists()


@pytest.mark.parametrize("bad", ["../escape", "/etc", "Alpha", "", "a b"])
def test_an_id_cannot_name_a_directory_outside_plugins(home, bad):
    """It is also a directory name, and `install` builds a path from it."""
    with pytest.raises(plugin_admin.PluginAdminError):
        plugin_admin.disable(bad)


def test_installing_clones_a_repository(home, tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    (origin / "plugin.py").write_text(
        "from tanrim.contract import Plugin\n"
        "class P(Plugin):\n    id = 'cloned'\n    name = 'Cloned'\n"
        "PLUGIN = P()\n")
    subprocess.run(["git", "init", "-q"], cwd=origin, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=origin, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=origin, check=True)
    subprocess.run(["git", "add", "-A"], cwd=origin, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=origin, check=True)

    got = plugin_admin.install(str(origin), "cloned")
    assert got["id"] == "cloned"
    assert [p.id for p in discovery.find()] == ["cloned"]
    # It knows where it came from, which is also "can this be deleted safely".
    assert discovery.catalog()[0]["git"].endswith("origin")


def test_installing_something_that_is_not_a_plugin_keeps_nothing(home, tmp_path):
    """A directory with no `plugin.py` is skipped by discovery, so leaving it
    would make the id unusable while being invisible."""
    origin = tmp_path / "notaplugin"
    origin.mkdir()
    (origin / "README.md").write_text("hello")
    subprocess.run(["git", "init", "-q"], cwd=origin, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=origin, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=origin, check=True)
    subprocess.run(["git", "add", "-A"], cwd=origin, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=origin, check=True)

    with pytest.raises(plugin_admin.PluginAdminError) as e:
        plugin_admin.install(str(origin), "notaplugin")
    assert "no plugin.py" in str(e.value)
    assert not (home / "plugins" / "notaplugin").exists()


def test_installing_over_an_existing_directory_refuses(home):
    _plugin(home, "alpha", prompts=True)
    with pytest.raises(plugin_admin.PluginAdminError) as e:
        plugin_admin.install("https://example.invalid/x.git", "alpha")
    assert "already exists" in str(e.value)
    assert (home / "plugins" / "alpha" / "prompts" / "ROLE.md").exists()


@pytest.mark.parametrize("source,expected", [
    ("git@github.com:emaurel/tanrim-job-hunt.git", "tanrim_job_hunt"),
    ("https://github.com/emaurel/web_agency", "web_agency"),
    ("https://github.com/emaurel/Some.Thing.git/", "some_thing"),
])
def test_a_clone_url_implies_a_directory_name(source, expected):
    assert plugin_admin.name_from(source) == expected
