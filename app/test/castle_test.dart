import 'dart:convert';
import 'dart:math' as math;
import 'dart:io';
import 'dart:ui' show PictureRecorder;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:tanrim/model/castle.dart';
import 'package:tanrim/model/world.dart';
import 'package:tanrim/ui/menu.dart';
import 'package:tanrim/world/iso.dart';
import 'package:tanrim/world/painter.dart';

Room _room(String id, double x, double y,
        [double w = 4, double h = 4, String castle = '']) =>
    Room.fromJson({
      'id': castle.isEmpty ? id : '$id@$castle',
      'base_id': id,
      'castle_id': castle,
      'name': id,
      'position': {'x': x, 'y': y},
      'size': {'w': w, 'h': h},
      'color': '#445566',
    });

/// A castle as `/castles` serves one.
Castle _castle(String id, String plugin,
        {String name = '', double x = 0, double y = 0, int records = 0,
        bool installed = true}) =>
    Castle.fromJson({
      'id': id,
      'plugin': plugin,
      'plugin_name': plugin,
      'name': name.isEmpty ? plugin : name,
      'ring': 1,
      'slot': 0,
      'x': x,
      'y': y,
      'span': 64,
      'records': records,
      'installed': installed,
    });

void main() {
  group('castles', () {
    test('two castles of ONE plugin each take their own rooms', () {
      // The thing the old grouping could not express: it keyed castles by
      // plugin, so a second instance of the same plugin was impossible.
      final rooms = [
        _room('hall', 0, 0, 4, 4, 'aaa'),
        _room('hall', 100, 0, 4, 4, 'bbb'),
      ];
      final first = _castle('aaa', 'p', name: 'First').withRooms(rooms);
      final second = _castle('bbb', 'p', name: 'Second').withRooms(rooms);

      expect(first.rooms.single.id, 'hall@aaa');
      expect(second.rooms.single.id, 'hall@bbb');
      expect(first.rooms.single.baseId, 'hall');
    });

    test('an install with no castles still lands its rooms somewhere', () {
      // Rooms come back unscoped from a server that predates castles, and a
      // castle with no rooms would draw as an empty plot.
      final c = _castle('aaa', 'p').withRooms([_room('hall', 0, 0)]);
      expect(c.rooms.single.id, 'hall');
    });

    test('a castle whose plugin is gone says so', () {
      final c = _castle('aaa', 'gone', installed: false);
      expect(c.installed, isFalse);
      // And falls back to its plot, so it is still somewhere on the map.
      final (_, _, w, h) = c.bounds;
      expect(w, 64);
      expect(h, 64);
    });

    test('bounds cover every room', () {
      final c = _castle('aaa', 'p').withRooms([
        _room('a', 2, 3, 4, 5, 'aaa'),
        _room('b', 10, 1, 2, 2, 'aaa'),
      ]);
      final (x, y, w, h) = c.bounds;
      expect(x, 2);
      expect(y, 1);
      expect(x + w, 12);   // 10 + 2
      expect(y + h, 8);    // 3 + 5
    });
  });

  group('zoomed out', () {
    WorldPainter painter(double zoom) => WorldPainter(
          rooms: const [],
          agents: const [],
          camera: Offset.zero,
          zoom: zoom,
          iso: const Iso(),
          tick: 0,
        );

    test('there is a distance at which rooms give way to castles', () {
      expect(painter(1.0).far, isFalse);
      expect(painter(WorldPainter.farZoom - 0.01).far, isTrue);
    });

    testWidgets('the estate paints with no rooms at all', (t) async {
      // An install with no plugins has no castles either, and that is a
      // legitimate state rather than an error.
      await t.pumpWidget(MaterialApp(
        home: CustomPaint(
          size: const Size(300, 200),
          painter: WorldPainter(
            rooms: const [],
            agents: const [],
            camera: Offset.zero,
            zoom: 0.1,
            iso: const Iso(),
            tick: 0,
            castles: const [],
          ),
        ),
      ));
      expect(t.takeException(), isNull);
    });
  });

  group('menus', () {
    testWidgets('a menu shows its first tab and switches', (t) async {
      await t.pumpWidget(MaterialApp(
        home: Builder(builder: (context) {
          return Scaffold(
            body: Center(
              child: TextButton(
                onPressed: () => MenuPanel.show(context, title: 'X', tabs: [
                  MenuTab(
                      id: 'one',
                      title: 'One',
                      icon: Icons.circle,
                      build: (_) => const Text('first body')),
                  MenuTab(
                      id: 'two',
                      title: 'Two',
                      icon: Icons.square,
                      build: (_) => const Text('second body')),
                ]),
                child: const Text('open'),
              ),
            ),
          );
        }),
      ));
      await t.tap(find.text('open'));
      await t.pumpAndSettle();
      expect(find.text('first body'), findsOneWidget);
      expect(find.text('second body'), findsNothing);

      await t.tap(find.text('Two'));
      await t.pumpAndSettle();
      expect(find.text('second body'), findsOneWidget);
    });

    testWidgets('adding a tab is one entry, and it carries a badge',
        (t) async {
      await t.pumpWidget(MaterialApp(
        home: Builder(builder: (context) {
          return Scaffold(
            body: Center(
              child: TextButton(
                onPressed: () => MenuPanel.show(context, title: 'X', tabs: [
                  MenuTab(
                      id: 'a',
                      title: 'A',
                      icon: Icons.circle,
                      build: (_) => const Text('a')),
                  MenuTab(
                      id: 'b',
                      title: 'B',
                      icon: Icons.square,
                      badge: 3,
                      build: (_) => const Text('b')),
                ]),
                child: const Text('open'),
              ),
            ),
          );
        }),
      ));
      await t.tap(find.text('open'));
      await t.pumpAndSettle();
      expect(find.text('A'), findsOneWidget);
      expect(find.text('B'), findsOneWidget);
      expect(find.text('3'), findsOneWidget);
    });
  });

  group('the real installation', () {
    // Captured from a running server: three plugins, two castles, and the
    // empty land around them. Regenerate with the snippet in tool/README.md.
    Map<String, dynamic> load(String name) =>
        jsonDecode(File('test/$name').readAsStringSync()) as Map<String, dynamic>;

    List<Room> rooms() =>
        (jsonDecode(File('test/rooms_fixture.json').readAsStringSync()) as List)
            .map((r) => Room.fromJson((r as Map).cast<String, dynamic>()))
            .toList();

    List<Castle> castles() {
      final all = rooms();
      return ((load('castles_fixture.json')['castles'] ?? []) as List)
          .map((c) => Castle.fromJson((c as Map).cast<String, dynamic>())
              .withRooms(all))
          .toList();
    }

    Web web() => Web.fromJson(
        (load('castles_fixture.json')['web'] as Map).cast<String, dynamic>());

    Set<(int, int)> taken() => {
          for (final t in (load('castles_fixture.json')['taken'] as List))
            ((t as List)[0] as int, t[1] as int),
        };

    test('every room belongs to exactly one castle', () {
      final built = castles();
      expect(built.length, 2);
      final counted = built.fold<int>(0, (n, c) => n + c.rooms.length);
      expect(counted, rooms().length,
          reason: 'a room in no castle is a room that draws nowhere');
    });

    test('the castles do not overlap', () {
      // Room positions are absolute tiles and the plots are laid out by the
      // server, so this is the check that the two agree.
      final built = castles();
      for (var i = 0; i < built.length; i++) {
        for (var j = i + 1; j < built.length; j++) {
          final a = built[i].bounds, b = built[j].bounds;
          final apart = a.$1 + a.$3 <= b.$1 ||
              b.$1 + b.$3 <= a.$1 ||
              a.$2 + a.$4 <= b.$2 ||
              b.$2 + b.$4 <= a.$2;
          expect(apart, isTrue,
              reason: '${built[i].name} overlaps ${built[j].name}');
        }
      }
    });

    test('a castle sits inside its own plot', () {
      for (final c in castles()) {
        final (px, py, pw, ph) = c.plot;
        final (bx, by, bw, bh) = c.bounds;
        expect(bx, greaterThanOrEqualTo(px));
        expect(by, greaterThanOrEqualTo(py));
        expect(bx + bw, lessThanOrEqualTo(px + pw));
        expect(by + bh, lessThanOrEqualTo(py + ph));
      }
    });

    test('this app computes the same plot centres as the server', () {
      // The equation lives in both languages, which is the one place the two
      // can drift. Everything ELSE about a castle's position comes from the
      // server precisely so they cannot.
      final w = web();
      for (final c in castles()) {
        final (x, y) = w.centre(c.ring, c.slot);
        expect(x, closeTo(c.centre.$1, 0.001),
            reason: 'ring ${c.ring} slot ${c.slot}');
        expect(y, closeTo(c.centre.$2, 0.001),
            reason: 'ring ${c.ring} slot ${c.slot}');
      }
    });

    test('zooming out reveals more land', () {
      // The point of an infinite web. A fixed list of plots meant the map
      // plainly stopped somewhere, however many were sent.
      final w = web();
      final built = taken();
      int seen(double half) => w
          .visible(Rect.fromLTRB(-half, -half, half, half), built)
          .length;

      final near = seen(200);
      final mid = seen(800);
      final away = seen(4000);
      expect(mid, greaterThan(near));
      expect(away, greaterThan(mid));
    });

    test('no two plots overlap', () {
      // Measured on the AXES, not as a distance. A plot is an axis-aligned
      // square in tile space, so two are clear only when their centres differ
      // by a full span along one axis — two plots 80 apart on a 45-degree
      // diagonal are 57 apart on each axis and overlap. The first version of
      // this check measured the distance and passed while the map plainly
      // showed them on top of one another.
      final w = web();
      final all = <(int, int, double, double)>[];
      for (var ring = 1; ring <= 8; ring++) {
        for (var slot = 0; slot < w.slotsOn(ring); slot++) {
          final (x, y) = w.centre(ring, slot);
          all.add((ring, slot, x, y));
        }
      }
      for (var i = 0; i < all.length; i++) {
        for (var j = i + 1; j < all.length; j++) {
          final a = all[i], b = all[j];
          if ((a.$1 - b.$1).abs() > 1) continue;   // far rings cannot reach
          final gap = math.max((a.$3 - b.$3).abs(), (a.$4 - b.$4).abs());
          expect(gap, greaterThanOrEqualTo(w.span),
              reason: 'ring${a.$1}s${a.$2} and ring${b.$1}s${b.$2} overlap');
        }
      }
    });

    test('a plot is big enough for what gets built on it', () {
      // The span cannot go below the largest plugin footprint, or a castle
      // spills out of its own ground.
      for (final c in castles()) {
        final (_, _, bw, bh) = c.bounds;
        expect(bw, lessThanOrEqualTo(web().span));
        expect(bh, lessThanOrEqualTo(web().span));
      }
    });

    test('land that is built on is never offered', () {
      final w = web();
      final built = taken();
      expect(built, isNotEmpty);
      final free = w.visible(
          const Rect.fromLTRB(-4000, -4000, 4000, 4000), built);
      expect(free, isNotEmpty, reason: 'there must be somewhere to build');
      for (final p in free) {
        expect(built.contains((p.ring, p.slot)), isFalse,
            reason: 'ring ${p.ring} slot ${p.slot} is already built on');
      }
    });

    test('an extension has no castle of its own', () {
      // `website_recreation` patches the agency's rooms and declares none.
      final buildable =
          ((load('castles_fixture.json')['buildable'] ?? []) as List)
              .map((b) => (b as Map)['id'])
              .toSet();
      expect(buildable, {'job_hunt', 'web_agency'});
      expect(buildable.contains('website_recreation'), isFalse);
    });
  });

  group('zoom steps', () {
    WorldPainter at(double zoom) => WorldPainter(
          rooms: const [],
          agents: const [],
          camera: Offset.zero,
          zoom: zoom,
          iso: const Iso(),
          tick: 0,
        );

    test('three steps, not two', () {
      // Close: rooms and their names. Middle: the layout alone, because at
      // that distance 13px of text is six pixels of grey fuzz over the thing
      // you are looking at. Far: one block per castle.
      final close = at(1.0);
      expect(close.far, isFalse);
      expect(close.labelled, isTrue);

      final middle = at((WorldPainter.farZoom + WorldPainter.labelZoom) / 2);
      expect(middle.far, isFalse, reason: 'the layout is still drawn');
      expect(middle.labelled, isFalse, reason: 'but nothing is lettered');

      final away = at(WorldPainter.farZoom - 0.01);
      expect(away.far, isTrue);
    });

    test('the steps are ordered', () {
      expect(WorldPainter.farZoom, lessThan(WorldPainter.labelZoom));
    });
  });

  group('render distance', () {
    /// How many drawing operations a paint issues.
    int opsFor({required Offset camera, required List<Plot> plots}) {
      final recorder = PictureRecorder();
      final canvas = Canvas(recorder);
      WorldPainter(
        rooms: const [],
        agents: const [],
        camera: camera,
        // Below farZoom, so the estate path runs and the plots are drawn.
        zoom: WorldPainter.farZoom - 0.01,
        iso: const Iso(),
        tick: 0,
        plots: plots,
      ).paint(canvas, const Size(1200, 800));
      return recorder.endRecording().approximateBytesUsed;
    }

    test('land that is off screen is not drawn', () {
      // The web is infinite by construction, and even the bounded slice the
      // server offers is 120 diamonds most of which are nowhere near the
      // camera. Drawing them all is work per frame that buys nothing, and it
      // gets worse the further out you build.
      final plots = [
        for (var i = 0; i < 120; i++)
          Plot(ring: 1, slot: i, centre: (i * 400.0, i * 400.0), span: 64),
      ];
      final near = opsFor(camera: Offset.zero, plots: plots);
      final away = opsFor(camera: const Offset(-900000, -900000), plots: plots);

      expect(away, lessThan(near),
          reason: 'a camera pointed at nothing should draw nearly nothing');
    });
  });

  group('withRooms', () {
    test('a castle survives it intact', () {
      // `withRooms` repeats every field, which is a trap: `status` and
      // `waiting` were added to the constructor and to `fromJson` and
      // forgotten here, so every castle the app drew reverted to `idle` with
      // nothing waiting — while the server reported `working` throughout.
      // Nothing failed; the dot and the block were simply never green.
      final before = Castle.fromJson({
        'id': 'c1',
        'plugin': 'web_agency',
        'plugin_name': 'Web agency',
        'name': 'Nimes office',
        'ring': 2,
        'slot': 5,
        'x': 111.0,
        'y': -222.0,
        'span': 52,
        'records': 77,
        'installed': false,
        'status': 'working',
        'waiting': 4,
      });
      final after = before.withRooms(const []);

      expect(after.id, before.id);
      expect(after.pluginId, before.pluginId);
      expect(after.pluginName, before.pluginName);
      expect(after.name, before.name);
      expect(after.ring, before.ring);
      expect(after.slot, before.slot);
      expect(after.centre, before.centre);
      expect(after.span, before.span);
      expect(after.records, before.records);
      expect(after.installed, before.installed);
      expect(after.status, before.status);
      expect(after.waiting, before.waiting);
      expect(after.working, isTrue);
    });

    test('and picks up only its own rooms', () {
      final c = _castle('aaa', 'p').withRooms([
        _room('hall', 0, 0, 4, 4, 'aaa'),
        _room('hall', 0, 0, 4, 4, 'bbb'),
      ]);
      expect(c.rooms.single.castleId, 'aaa');
    });
  });
}
