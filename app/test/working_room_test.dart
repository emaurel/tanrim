import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:tanrim/model/world.dart';
import 'package:tanrim/world/iso.dart';
import 'package:tanrim/world/painter.dart';

/// Records what was filled and what was stroked, because "which room is
/// working" is a question the map answers with paint and nothing else.
class _Paints implements ui.Canvas {
  final List<Color> filled = [];
  final List<(Color, double)> stroked = [];

  @override
  void drawPath(ui.Path path, ui.Paint paint) {
    if (paint.style == PaintingStyle.stroke) {
      stroked.add((paint.color, paint.strokeWidth));
    } else {
      filled.add(paint.color);
    }
  }

  @override
  dynamic noSuchMethod(Invocation inv) => null;
}

Room _room() => Room.fromJson({
      'id': 'factory@c1',
      'name': 'Factory',
      'position': {'x': 0, 'y': 0},
      'size': {'w': 6, 'h': 4},
      'color': '#445566',
    });

AgentState _agent({required bool busy}) => AgentState(
      id: 'forge@c1',
      name: 'Forge',
      roomId: 'factory@c1',
      x: 2,
      y: 2,
      color: 0xFF98C1D9,
      busy: busy,
    );

_Paints _paint({required bool busy, double tick = 0}) {
  final canvas = _Paints();
  WorldPainter(
    rooms: [_room()],
    agents: [_agent(busy: busy)],
    camera: Offset.zero,
    zoom: 1.0,
    iso: const Iso(),
    tick: tick,
  ).paint(canvas, const Size(2000, 2000));
  return canvas;
}

/// A near-white fill with some transparency: the wash, not the floor.
bool _wash(Color c) =>
    c.a < 0.95 && c.r > 0.9 && c.g > 0.9 && c.b > 0.9;

double _widest(List<(Color, double)> l) =>
    l.map((s) => s.$2).reduce((a, b) => a > b ? a : b);

void main() {
  test('a room with someone working in it is washed, an idle one is not', () {
    // The sprite animation says WHO is working. At the middle zoom there are
    // no readable sprites and no labels, so without this the map cannot say
    // WHERE the work is at the one distance you would survey a castle from.
    expect(_paint(busy: true, tick: 0.4).filled.any(_wash), isTrue,
        reason: 'a working room was drawn exactly like an idle one');
    expect(_paint(busy: false, tick: 0.4).filled.any(_wash), isFalse);
  });

  test('it breathes rather than blinking', () {
    // A strobe on a map that repaints every frame and is looked at for long
    // stretches is an irritation you end up avoiding. The value has to MOVE,
    // and move smoothly.
    final seen = <double>[];
    for (final t in [0.0, 0.35, 0.7, 1.05, 1.4]) {
      final wash = _paint(busy: true, tick: t).filled.firstWhere(_wash);
      seen.add(wash.a);
    }
    expect(seen.toSet().length, greaterThan(3),
        reason: 'the wash never changed — it is not animating');
    // Never fully opaque and never fully gone: the room stays itself, and the
    // signal never disappears between beats the way a blink does.
    for (final a in seen) {
      expect(a, greaterThan(0.0));
      expect(a, lessThan(0.5));
    }
  });

  test('the outline glows with it', () {
    // The wash alone is easy to miss on a pale floor.
    final busy = _paint(busy: true, tick: 0.8).stroked;
    final idle = _paint(busy: false, tick: 0.8).stroked;
    expect(_widest(busy), greaterThan(_widest(idle)),
        reason: 'a working room had no heavier edge than an idle one');
  });

  test('a room whose only worker is idle is not marked', () {
    // Every room has a permanent worker standing in it, so "has an agent" is
    // not the question — "has a BUSY one" is.
    expect(_paint(busy: false, tick: 0.2).filled.any(_wash), isFalse);
  });
}
