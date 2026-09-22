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
    Castle.group(_rooms, {'p': ['a', 'b', 'c']}, {'p': 'Plugin'});

/// Pump a map and hand back its state.
Future<dynamic> _map(WidgetTester t) async {
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
}
