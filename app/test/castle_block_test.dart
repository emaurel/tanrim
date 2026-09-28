import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:tanrim/model/castle.dart';
import 'package:tanrim/world/iso.dart';
import 'package:tanrim/world/painter.dart';

/// Records the COLOURS filled, because that is the whole message at this
/// distance. Zoomed all the way out the block IS the castle — there are no
/// rooms, no sprites and no readable labels — so its colour is the only thing
/// that can say anything without being read.
class _Colours implements ui.Canvas {
  final List<Color> filled = [];

  @override
  void drawPath(ui.Path path, ui.Paint paint) {
    if (paint.style == PaintingStyle.fill) filled.add(paint.color);
  }

  @override
  dynamic noSuchMethod(Invocation inv) => null;
}

Castle _castle({required bool installed, bool working = false}) => Castle(
      id: 'c1',
      pluginId: 'web_agency',
      pluginName: 'Web agency',
      name: 'Agency',
      ring: 1,
      slot: 0,
      centre: (0, 0),
      span: 52,
      records: 7,
      installed: installed,
      rooms: const [],
      status: working ? 'working' : 'idle',
    );

List<Color> _paint(Castle c) {
  final canvas = _Colours();
  WorldPainter(
    rooms: const [],
    agents: const [],
    camera: Offset.zero,
    // Below `farZoom`, which is what swaps the rooms for one block per castle.
    zoom: WorldPainter.farZoom / 2,
    iso: const Iso(),
    tick: 0,
    castles: [c],
  ).paint(canvas, const Size(2000, 2000));
  return canvas.filled;
}

bool _reddish(Color c) => c.r > c.g + 0.1 && c.r > c.b + 0.1;
bool _greenish(Color c) => c.g > c.r + 0.1 && c.g > c.b + 0.1;

void main() {
  test('a castle whose plugin is gone is drawn red', () {
    // It has no rooms, no agents and nothing that can run. Worth seeing from
    // across the estate rather than only after opening it and finding out.
    final filled = _paint(_castle(installed: false));
    expect(filled.any(_reddish), isTrue,
        reason: 'nothing red was drawn for an uninstalled castle');
  });

  test('an ordinary castle is not red', () {
    final filled = _paint(_castle(installed: true));
    expect(filled.any(_reddish), isFalse);
  });

  test('a working castle is green', () {
    final filled = _paint(_castle(installed: true, working: true));
    expect(filled.any(_greenish), isTrue);
  });

  test('red beats green: a castle that cannot work is not working', () {
    // `working` can still be set from a run that was in flight when the plugin
    // was removed. Drawing it green would say the place is fine.
    final filled = _paint(_castle(installed: false, working: true));
    expect(filled.any(_reddish), isTrue);
    expect(filled.any(_greenish), isFalse,
        reason: 'an uninstalled castle was drawn as if it were working');
  });
}
