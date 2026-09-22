# Tests

Run them with `.venv/bin/python -m pytest`.

The suite is in two halves and the split is deliberate.

**Synthetic (`test_plugin_*.py`, `test_state_machine.py`)** builds throwaway
plugins in a temp directory and asserts against those. These are the ones to
keep green while the core is pulled further from the web agency: they describe
the CONTRACT, so they should only fail when the contract breaks.

**Real (`test_real_plugins.py`)** checks the plugins actually installed, and is
deliberately thin. It asserts what must never break however much the domain
changes — both pipelines walk end to end, the two do not bleed into each other,
every stage with an edge is worked by somebody, every non-informational
approval kind has a branch, and `web_agency` contains no word about ports.
Anything that would fail for a GOOD reason (a stage count, a room count)
belongs in the synthetic half instead.

`test_plugin_contract.py` is the one to extend as abstraction continues. Each
test there is a promise a plugin author is entitled to rely on — a plugin needs
only an id and a name; it may live anywhere on disk; removing it removes
everything it contributed; two plugins may define the same stage for different
kinds.

## What they have already caught

- `prompts.load` built its error messages with `path.relative_to(ROOT)`, which
  raises `ValueError` for a plugin installed outside the repository — a
  crash while constructing the message for a different error.
- `agent_crashed` was declared as a gate with no branch to resolve it. It is
  informational and cleared by dismissal, which is now stated in the
  declaration rather than assumed.
