import 'dart:convert';
import 'dart:io';

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

    List<Plot> plots() =>
        ((load('castles_fixture.json')['plots'] ?? []) as List)
            .map((p) => Plot.fromJson((p as Map).cast<String, dynamic>()))
            .toList();

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

    test('empty plots are offered, and none is under a castle', () {
      final free = plots();
      expect(free, isNotEmpty, reason: 'there must be somewhere to build');
      final taken = {for (final c in castles()) '${c.ring}:${c.slot}'};
      for (final p in free) {
        expect(taken.contains('${p.ring}:${p.slot}'), isFalse,
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
}
