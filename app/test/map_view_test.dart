import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:tanrim/model/castle.dart';
import 'package:tanrim/model/world.dart';
import 'package:tanrim/ui/map_view.dart';
import 'package:tanrim/world/painter.dart';

Room _room(String id, double x, double y, [double w = 12, double h = 8]) =>
    Room.fromJson({
      'id': id,
      'name': id,
      'position': {'x': x, 'y': y},
      'size': {'w': w, 'h': h},
      'color': '#445566',
    });

final _rooms = [_room('a', 0, 0), _room('b', 14, 0), _room('c', 0, 10)];
final _castles =
    castlesFrom(_rooms, {'p': ['a', 'b', 'c']}, {'p': 'Plugin'});

/// Castles as the server groups them: one per plugin that declares rooms.
List<Castle> castlesFrom(
  List<Room> rooms,
  Map<String, List<String>> byPlugin,
  Map<String, String> names,
) {
  final out = <Castle>[];
  for (final e in byPlugin.entries) {
    final mine = rooms.where((r) => e.value.contains(r.id)).toList();
    if (mine.isEmpty) continue;
    out.add(Castle(
      id: e.key,
      pluginId: e.key,
      pluginName: names[e.key] ?? e.key,
      name: names[e.key] ?? e.key,
      ring: 1,
      slot: out.length,
      centre: (0, 0),
      span: 64,
      records: 0,
      installed: true,
      rooms: mine,
    ));
  }
  return out;
}


/// Pump a map and hand back its state.
Future<dynamic> _map(WidgetTester t,
    {Web web = const Web(),
    Set<(int, int)> taken = const {},
    List<Castle>? castles,
    void Function(int ring, int slot)? onPlotTapped,
    void Function(Castle)? onCastleTapped}) async {
  t.view
    ..physicalSize = const Size(1200, 800)
    ..devicePixelRatio = 1.0;
  addTearDown(t.view.reset);

  final key = GlobalKey();
  await t.pumpWidget(MaterialApp(
    home: Scaffold(
      body: MapView(
        key: key,
        rooms: _rooms,
        agents: const [],
        badges: const {},
        castles: castles ?? _castles,
        castleBadges: const {},
        web: web,
        taken: taken,
        onPlotTapped: onPlotTapped,
        onCastleTapped: onCastleTapped,
        onRoomTapped: (_) {},
      ),
    ),
  ));
  await t.pump(const Duration(milliseconds: 16));
  return key.currentState;
}

Future<void> _zoomOut(WidgetTester t, int steps) async {
  final centre = t.getCenter(find.byType(MapView));
  for (var i = 0; i < steps; i++) {
    final p = TestPointer(1, PointerDeviceKind.mouse);
    t.binding.handlePointerEvent(p.hover(centre));
    t.binding.handlePointerEvent(p.scroll(const Offset(0, 60)));
    await t.pump();
  }
}

void main() {
  testWidgets('the clock runs', (t) async {
    // It did not. `late final Ticker _t = Ticker(..)..start()` is LAZY,
    // and the only other mention of the field was in `dispose` — so it was
    // never initialised, the sprites never breathed, and a camera flight was
    // created correctly and then never advanced a single frame.
    final s = await _map(t);
    await t.pump(const Duration(milliseconds: 300));
    // ignore: avoid_dynamic_calls
    expect(s.debugTick, greaterThan(0.0));
  });

  testWidgets('tapping a castle from far off flies to it', (t) async {
    final s = await _map(t);
    await _zoomOut(t, 30);
    // ignore: avoid_dynamic_calls
    expect(s.debugZoom < WorldPainter.farZoom, isTrue,
        reason: 'the wheel must be able to reach the estate view at all');

    final size = t.getSize(find.byType(MapView));
    // The painter centres the world a THIRD of the way down, not halfway, so
    // the castle is not under the middle of the widget.
    // ignore: avoid_dynamic_calls
    final onCastle = s.debugScreenOf(13.0, 9.0, size) as Offset;
    // ignore: avoid_dynamic_calls
    expect(s.debugCastleAt(onCastle, size), 'p');

    // ignore: avoid_dynamic_calls
    final before = s.debugZoom as double;
    await t.tapAt(onCastle);
    await t.pump();
    // ignore: avoid_dynamic_calls
    expect(s.debugFlying, isTrue, reason: 'the tap should start a flight');

    await t.pump(const Duration(milliseconds: 700));
    // ignore: avoid_dynamic_calls
    expect(s.debugZoom, greaterThan(before * 3),
        reason: 'it should end up close enough to read the rooms');
    // ignore: avoid_dynamic_calls
    expect(s.debugFlying, isFalse, reason: 'and then stop');
  });

  testWidgets('a flight lands with the castle under the camera', (t) async {
    final s = await _map(t);
    await _zoomOut(t, 30);
    final size = t.getSize(find.byType(MapView));
    // ignore: avoid_dynamic_calls
    await t.tapAt(s.debugScreenOf(13.0, 9.0, size) as Offset);
    await t.pump(const Duration(milliseconds: 700));

    // Where the castle's centre ended up on screen: near the point the
    // painter treats as the middle of the view.
    // ignore: avoid_dynamic_calls
    final at = s.debugScreenOf(13.0, 9.0, size) as Offset;
    expect(at.dx, closeTo(size.width / 2, 2));
    expect(at.dy, closeTo(size.height / 3, 2));
  });

  testWidgets('taking the wheel cancels a flight', (t) async {
    // The operator always wins: a camera still travelling while they are
    // scrolling is a camera fighting them.
    final s = await _map(t);
    await _zoomOut(t, 30);
    final size = t.getSize(find.byType(MapView));
    // ignore: avoid_dynamic_calls
    await t.tapAt(s.debugScreenOf(13.0, 9.0, size) as Offset);
    await t.pump();
    // ignore: avoid_dynamic_calls
    expect(s.debugFlying, isTrue);

    await _zoomOut(t, 1);
    // ignore: avoid_dynamic_calls
    expect(s.debugFlying, isFalse);
  });

  testWidgets('close up, a tap opens a room instead of travelling', (t) async {
    String? opened;
    t.view
      ..physicalSize = const Size(1200, 800)
      ..devicePixelRatio = 1.0;
    addTearDown(t.view.reset);

    final key = GlobalKey();
    await t.pumpWidget(MaterialApp(
      home: Scaffold(
        body: MapView(
          key: key,
          rooms: _rooms,
          agents: const [],
          badges: const {},
          castles: _castles,
          castleBadges: const {},
          onRoomTapped: (r) => opened = r.id,
        ),
      ),
    ));
    await t.pump(const Duration(milliseconds: 16));

    final s = key.currentState as dynamic;
    final size = t.getSize(find.byType(MapView));
    // ignore: avoid_dynamic_calls
    expect(s.debugZoom > WorldPainter.farZoom, isTrue);
    // Room 'a' spans tiles 0..12 x 0..8.
    // ignore: avoid_dynamic_calls
    await t.tapAt(s.debugScreenOf(6.0, 4.0, size) as Offset);
    await t.pump();
    expect(opened, 'a');
  });

  testWidgets('empty land offers to be built on', (t) async {
    // A plot is the one thing on the map that is not there yet, so a tap on
    // it asks rather than travels: flying to somewhere that may not get built
    // on is a camera move you did not want.
    final tapped = <(int, int)>[];
    final s = await _map(t, onPlotTapped: (r, sl) => tapped.add((r, sl)));

    await _zoomOut(t, 30);
    final size = t.getSize(find.byType(MapView));
    // Ring 1 slot 3 is due south of the hub, and nothing is built there.
    const web = Web();
    final (px, py) = web.centre(1, 3);
    // ignore: avoid_dynamic_calls
    await t.tapAt(s.debugScreenOf(px, py, size) as Offset);
    await t.pump();

    expect(tapped, [(1, 3)]);
    // ignore: avoid_dynamic_calls
    expect(s.debugFlying, isFalse, reason: 'it must not travel to empty land');
  });

  testWidgets('a castle covers the plot it stands on', (t) async {
    // The plots and the castles come from the same geometry, so one sits on
    // the other. Land that is built on is not empty land.
    final tapped = <(int, int)>[];
    final s = await _map(t, onPlotTapped: (r, sl) => tapped.add((r, sl)));

    await _zoomOut(t, 30);
    final size = t.getSize(find.byType(MapView));
    // The castle's own rooms sit around (7, 4), which is inside a plot too.
    // ignore: avoid_dynamic_calls
    await t.tapAt(s.debugScreenOf(7.0, 4.0, size) as Offset);
    await t.pump();

    expect(tapped, isEmpty, reason: 'the castle is there, not empty land');
    // ignore: avoid_dynamic_calls
    expect(s.debugFlying, isTrue, reason: 'it travelled to the castle instead');
  });

  testWidgets('you can always zoom out far enough to see everything',
      (t) async {
    // The web of plots is infinite by construction, so a FIXED zoom floor is
    // a promise that stops being true as soon as somebody builds far enough
    // out — past about ring 4 the estate no longer fits at 0.06 and there is
    // no way to pull back further.
    // A castle far out: the floor is measured from what is BUILT, because the
    // web of land is infinite and measuring that would shrink the map to a
    // dot.
    const web = Web();
    final (fx, fy) = web.centre(9, 0);
    final s = await _map(t, castles: [
      ..._castles,
      Castle(
        id: 'far', pluginId: 'p', pluginName: 'P', name: 'Far',
        ring: 9, slot: 0, centre: (fx, fy), span: 64,
        records: 0, installed: true, rooms: const [],
      ),
    ]);

    await _zoomOut(t, 90);
    // ignore: avoid_dynamic_calls
    final zoom = s.debugZoom as double;
    expect(zoom, lessThan(0.06),
        reason: 'the floor must drop for a world this big');

    // And at that zoom the whole world FITS in the viewport — which is what
    // the floor can promise. Whether you are looking at it is the camera's
    // business: the wheel zooms about its anchor and does not re-centre, so
    // asserting a particular point is on screen would be testing where the
    // operator happened to leave the map.
    final size = t.getSize(find.byType(MapView));
    // ignore: avoid_dynamic_calls
    final near = s.debugScreenOf(0.0, 0.0, size) as Offset;
    // ignore: avoid_dynamic_calls
    final far = s.debugScreenOf(fx, fy, size) as Offset;
    expect((far.dy - near.dy).abs(), lessThanOrEqualTo(size.height));
    expect((far.dx - near.dx).abs(), lessThanOrEqualTo(size.width));
  });

  testWidgets('a small world still stops at the ordinary floor', (t) async {
    // Lowering the floor for everyone would let you zoom into the middle
    // distance and lose the map entirely.
    final s = await _map(t);
    await _zoomOut(t, 90);
    // ignore: avoid_dynamic_calls
    expect(s.debugZoom, closeTo(0.02, 0.0001));
  });

  testWidgets('empty land can be built on at the layout step', (t) async {
    // The step this is here for: rooms still drawn, nothing lettered. The
    // plots used to appear only in the estate view, so the one distance where
    // you can see a castle's shape AND the ground around it showed no ground.
    final tapped = <(int, int)>[];
    final s = await _map(t, onPlotTapped: (r, sl) => tapped.add((r, sl)));

    // Out to the layout band, and no further.
    for (var i = 0; i < 40; i++) {
      // ignore: avoid_dynamic_calls
      if ((s.debugZoom as double) < WorldPainter.labelZoom) break;
      await _zoomOut(t, 1);
    }
    // ignore: avoid_dynamic_calls
    final zoom = s.debugZoom as double;
    expect(zoom, lessThan(WorldPainter.labelZoom));
    expect(zoom, greaterThan(WorldPainter.farZoom),
        reason: 'the layout step, not the estate');

    final size = t.getSize(find.byType(MapView));
    // Find a screen point that genuinely lands on empty ground. Working back
    // from a plot's centre does not: the isometric view is a diamond and a
    // plot can be inside its bounding box while sitting off the corner.
    Offset? spot;
    Plot? want;
    for (var gx = 1; gx < 10 && spot == null; gx++) {
      for (var gy = 1; gy < 8 && spot == null; gy++) {
        final at = Offset(size.width * gx / 10, size.height * gy / 8);
        // ignore: avoid_dynamic_calls
        final hit = s.debugPlotAt(at, size) as Plot?;
        if (hit != null) {
          spot = at;
          want = hit;
        }
      }
    }
    expect(spot, isNotNull, reason: 'land should be visible at this step');

    await t.tapAt(spot!);
    await t.pump();
    expect(tapped, [(want!.ring, want.slot)],
        reason: 'land is clickable before the estate view');
  });

  testWidgets('the wheel zooms about the pointer, not the middle', (t) async {
    // Scaling the camera about the centre means the thing you are pointing at
    // slides away as you zoom towards it, and you chase it with the drag.
    final s = await _map(t);
    final size = t.getSize(find.byType(MapView));

    // A point well off-centre, and whatever tile is under it.
    final under = Offset(size.width * 0.78, size.height * 0.28);
    // ignore: avoid_dynamic_calls
    final before = s.debugTileAt(under, size) as Offset;

    final p = TestPointer(1, PointerDeviceKind.mouse);
    t.binding.handlePointerEvent(p.hover(under));
    for (var i = 0; i < 4; i++) {
      t.binding.handlePointerEvent(p.scroll(const Offset(0, -60)));
      await t.pump();
    }

    // ignore: avoid_dynamic_calls
    final after = s.debugTileAt(under, size) as Offset;
    expect(after.dx, closeTo(before.dx, 0.6));
    expect(after.dy, closeTo(before.dy, 0.6));
    // ignore: avoid_dynamic_calls
    expect(s.debugZoom, greaterThan(1.0), reason: 'it did zoom in');
  });

  testWidgets('a notch moves twice as far as it used to', (t) async {
    final s = await _map(t);
    // ignore: avoid_dynamic_calls
    final start = s.debugZoom as double;

    final centre = t.getCenter(find.byType(MapView));
    final p = TestPointer(1, PointerDeviceKind.mouse);
    t.binding.handlePointerEvent(p.hover(centre));
    t.binding.handlePointerEvent(p.scroll(const Offset(0, -60)));
    await t.pump();

    // 1.1 squared: a notch moves exactly twice as far in the scale the zoom
    // actually works in, which is a multiplier and not an amount.
    // ignore: avoid_dynamic_calls
    expect(s.debugZoom, closeTo(start * 1.21, 0.0001));
  });

  testWidgets('a castle can be opened from its own land, zoomed in',
      (t) async {
    // Zoomed in among the rooms, the land between them is the only part of a
    // castle left to click — without it you had to zoom out to open the
    // castle you were standing in.
    final opened = <String>[];
    final s = await _map(t, onCastleTapped: (c) => opened.add(c.id));
    final size = t.getSize(find.byType(MapView));

    // ignore: avoid_dynamic_calls
    expect(s.debugZoom > WorldPainter.labelZoom, isTrue, reason: 'close up');
    // Inside the plot, in the gap between the rooms.
    // ignore: avoid_dynamic_calls
    await t.tapAt(s.debugScreenOf(13.0, 9.0, size) as Offset);
    await t.pump();
    expect(opened, ['p']);
  });

  testWidgets('a room still wins over the land it stands on', (t) async {
    final opened = <String>[];
    final rooms = <String>[];
    t.view
      ..physicalSize = const Size(1200, 800)
      ..devicePixelRatio = 1.0;
    addTearDown(t.view.reset);

    final key = GlobalKey();
    await t.pumpWidget(MaterialApp(
      home: Scaffold(
        body: MapView(
          key: key,
          rooms: _rooms,
          agents: const [],
          badges: const {},
          castles: _castles,
          castleBadges: const {},
          onCastleTapped: (c) => opened.add(c.id),
          onRoomTapped: (r) => rooms.add(r.id),
        ),
      ),
    ));
    await t.pump(const Duration(milliseconds: 16));

    final s = key.currentState as dynamic;
    final size = t.getSize(find.byType(MapView));
    // ignore: avoid_dynamic_calls
    await t.tapAt(s.debugScreenOf(6.0, 4.0, size) as Offset);
    await t.pump();
    expect(rooms, ['a']);
    expect(opened, isEmpty);
  });
}
