# Pictures, for looking at

These are not tests. They render the UI with the REAL payloads and write a
PNG, so a person can see whether the thing reads at a glance — which is not
something an assertion can tell you.

They live outside `test/` on purpose: `flutter test` would pick them up, and
they cannot be asserted. The images carry relative times ("56m") and the map
animates, so a golden comparison fails on the second run for no reason at all.

    flutter test tool/map_render.dart --update-goldens      # build/map.png
    flutter test tool/console_render.dart --update-goldens  # build/console.png

Refresh the fixtures from a running environment first if the shape changed:

    curl -s localhost:8765/rooms              -o test/rooms_fixture.json
    curl -s "localhost:8765/leads?slim=1"     -o test/board_fixture.json

## Regenerating the test fixtures

`test/rooms_fixture.json` and `test/plugins_fixture.json` are captured from a
real server, so `castle_test.dart` checks the actual installation rather than a
synthetic one — two plugins really do make two castles, and their rooms really
do not overlap. Room positions are absolute tiles that nothing allocates, so
that last one is a genuine check: a new plugin picks its own corner by hand.

From the repository root, with the plugins you want installed:

```bash
PYTHONPATH=backend .venv/bin/python -c "
import json
from fastapi.testclient import TestClient
from tanrim.server import app
with TestClient(app) as c:
    for name, path in (('/rooms', 'rooms'), ('/plugins', 'plugins'),
                       ('/castles', 'castles')):
        open(f'app/test/{path}_fixture.json', 'w').write(
            json.dumps(c.get(name).json()))
"
```

## Running it fast

`flutter run -d linux` builds in DEBUG, which is un-optimised JIT with every
assertion on — Flutter's own docs say never to judge performance from it, and
for a map that repaints every frame the difference is not subtle. For anything
you are going to *use* rather than edit:

```bash
flutter build linux --release
./build/linux/x64/release/bundle/tanrim
```

or `flutter run -d linux --release`.
