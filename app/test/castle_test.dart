import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:tanrim/model/castle.dart';
import 'package:tanrim/model/world.dart';
import 'package:tanrim/ui/menu.dart';
import 'package:tanrim/world/iso.dart';
import 'package:tanrim/world/painter.dart';

Room _room(String id, double x, double y, [double w = 4, double h = 4]) =>
    Room.fromJson({
      'id': id,
      'name': id,
      'position': {'x': x, 'y': y},
      'size': {'w': w, 'h': h},
      'color': '#445566',
    });

void main() {
  group('castles', () {
    test('a plugin that declares rooms is one; an extension is not', () {
      // `website_recreation` adds benches to two of the web agency's rooms
      // and declares none of its own — it lives INSIDE that castle, which is
      // exactly what an extension is.
      final castles = Castle.group(
        [_room('a', 0, 0), _room('b', 4, 0)],
        {'base': ['a', 'b'], 'ext': []},
        {'base': 'Base', 'ext': 'Ext'},
      );
      expect(castles.length, 1);
      expect(castles.single.pluginId, 'base');
      expect(castles.single.rooms.length, 2);
    });

    test('two plugins with rooms are two castles', () {
      final castles = Castle.group(
        [_room('a', 0, 0), _room('b', 40, 40)],
        {'one': ['a'], 'two': ['b']},
        {'one': 'One', 'two': 'Two'},
      );
      expect(castles.map((c) => c.pluginId), ['one', 'two']);
    });

    test('a room no plugin claims is still shown', () {
      // An unclaimed room is a bug worth SEEING. Dropping it would hide the
      // one thing that says something is wrong.
      final castles = Castle.group(
        [_room('a', 0, 0), _room('orphan', 9, 9)],
        {'one': ['a']},
        {'one': 'One'},
      );
      expect(castles.length, 2);
      expect(castles.last.rooms.single.id, 'orphan');
    });

    test('bounds cover every room', () {
      final c = Castle(pluginId: 'p', name: 'P', rooms: [
        _room('a', 2, 3, 4, 5),
        _room('b', 10, 1, 2, 2),
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
    // Captured from a running server with three plugins installed:
    // web_agency (12 rooms), job_hunt (5) and website_recreation, which
    // declares none of its own. Regenerate with the snippet in tool/README.md.
    List<Room> rooms() => (jsonDecode(
            File('test/rooms_fixture.json').readAsStringSync()) as List)
        .map((r) => Room.fromJson((r as Map).cast<String, dynamic>()))
        .toList();

    List<Map<String, dynamic>> plugins() =>
        ((jsonDecode(File('test/plugins_fixture.json').readAsStringSync())
                as Map)['plugins'] as List)
            .map((p) => (p as Map).cast<String, dynamic>())
            .toList();

    List<Castle> build() {
      final list = plugins();
      return Castle.group(
        rooms(),
        {for (final p in list)
          p['id'] as String: ((p['rooms'] ?? []) as List).cast<String>()},
        {for (final p in list)
          p['id'] as String: (p['name'] ?? p['id']) as String},
      );
    }

    test('two plugins with rooms make two castles, and nothing is orphaned', () {
      final castles = build();
      expect(castles.map((c) => c.pluginId).toSet(), {'web_agency', 'job_hunt'});
      expect(castles.any((c) => c.pluginId.isEmpty), isFalse,
          reason: 'an unclaimed room means a plugin lost one');
      final counted = castles.fold<int>(0, (n, c) => n + c.rooms.length);
      expect(counted, rooms().length, reason: 'every room lives in a castle');
    });

    test('the castles do not overlap on the map', () {
      // Room positions are absolute tiles, and nothing allocates them — a new
      // plugin picks its own corner. Two castles sharing tiles would draw one
      // through the other, and the estate view would put two labels in one
      // place.
      final castles = build();
      for (var i = 0; i < castles.length; i++) {
        for (var j = i + 1; j < castles.length; j++) {
          final a = castles[i].bounds, b = castles[j].bounds;
          final apart = a.$1 + a.$3 <= b.$1 ||
              b.$1 + b.$3 <= a.$1 ||
              a.$2 + a.$4 <= b.$2 ||
              b.$2 + b.$4 <= a.$2;
          expect(apart, isTrue,
              reason: '${castles[i].pluginId} overlaps ${castles[j].pluginId}');
        }
      }
    });

    test('an extension lives inside the castle it extends', () {
      // `website_recreation` adds benches to the web agency's rooms and
      // declares none of its own, so it is not a castle — it is part of one.
      expect(plugins().where((p) => p['id'] == 'website_recreation').single['rooms'],
          isEmpty);
      expect(build().map((c) => c.pluginId), isNot(contains('website_recreation')));
    });
  });
}
