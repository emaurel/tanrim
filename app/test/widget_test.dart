import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:tanrim/model/world.dart';
import 'package:tanrim/world/iso.dart';
import 'package:tanrim/world/painter.dart';

void main() {
  group('isometric projection', () {
    const iso = Iso(tileW: 64);

    test('a tile round-trips through the projection', () {
      // Hit-testing inverts it, so any drift here puts the click on the wrong
      // room — and the operator's click is how every panel opens.
      for (final p in [const Offset(0, 0), const Offset(12, 4),
                       const Offset(47.5, 27.5)]) {
        final back = iso.toTile(iso.toScreen(p.dx, p.dy));
        expect(back.dx, closeTo(p.dx, 1e-9));
        expect(back.dy, closeTo(p.dy, 1e-9));
      }
    });

    test('tiles are 2:1 diamonds', () {
      // Anything other than exactly half leaves hairline seams between floor
      // tiles that no amount of overdraw hides.
      expect(iso.tileH, 32);
      final right = iso.toScreen(1, 0);
      final down = iso.toScreen(0, 1);
      expect(right.dx, 32);
      expect(right.dy, 16);
      expect(down.dx, -32);
      expect(down.dy, 16);
    });

    test('depth grows away from the camera', () {
      // Painter's order depends on this: a nearer sprite must sort after the
      // wall it stands in front of.
      expect(isoDepth(0, 0) < isoDepth(1, 0), isTrue);
      expect(isoDepth(0, 0) < isoDepth(0, 1), isTrue);
    });
  });

  test('a colour that will not parse costs a sprite its tint, not the map', () {
    expect(parseColor('#bcd35f'), 0xFFBCD35F);
    expect(parseColor('bcd35f'), 0xFFBCD35F);
    expect(parseColor(null), 0xFFFFFFFF);
    expect(parseColor('not a colour'), 0xFFFFFFFF);
  });

  test('a room parses from what the backend actually serves', () {
    final room = Room.fromJson(const {
      'id': 'assay',
      'name': 'Assay Room',
      'purpose': 'Probe weighs each lead',
      'position': {'x': 12, 'y': 4},
      'size': {'w': 12, 'h': 8},
      'color': '#4a5a2b',
      'agents': [
        {'id': 'probe', 'name': 'Probe', 'role': 'Qualifier.',
         'color': '#bcd35f', 'station': null}
      ],
      'workbenches': [
        {'id': 'qualify', 'name': 'Weighing Bench', 'job': 'Cheap first pass',
         'stages': ['sourced'], 'position': {'x': 2, 'y': 2},
         'size': {'w': 4, 'h': 3}}
      ],
      'tools': ['site_audit'],
      'skills': [],
    });
    expect(room.size.x, 12);
    expect(room.size.y, 8);
    expect(room.agents.single.color, 0xFFBCD35F);
    expect(room.workbenches.single.stages, ['sourced']);
  });

  test('a bench with no geometry is skipped, not invented', () {
    // Layout is computed server-side; a bench without it simply is not placed.
    final b = Workbench.fromJson(const {'id': 'x', 'stages': <String>[]});
    expect(b.position, isNull);
    expect(b.size, isNull);
  });

  testWidgets('the world paints without a server', (tester) async {
    // It must survive an empty environment: an install with no plugins has no
    // rooms, and that is a legitimate state rather than an error.
    await tester.pumpWidget(MaterialApp(
      home: CustomPaint(
        size: const Size(400, 300),
        painter: WorldPainter(
          rooms: const [],
          agents: const [],
          camera: Offset.zero,
          zoom: 1,
          iso: const Iso(),
          tick: 0,
        ),
      ),
    ));
    expect(tester.takeException(), isNull);
  });
}
