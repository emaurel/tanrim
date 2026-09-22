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
