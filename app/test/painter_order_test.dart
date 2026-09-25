import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:tanrim/model/world.dart';
import 'package:tanrim/world/iso.dart';
import 'package:tanrim/world/painter.dart';

/// A canvas that records the ORDER of what it was asked to draw.
///
/// The painter's bug was never wrong output from any one call — every room,
/// wall and label was drawn correctly. It was the sequence: a label drawn with
/// its room went into the depth sort with it, so the next room's wall and
/// anyone standing in front of it painted over the name. Only the order can
/// show that, so only the order is recorded.
class _Recorder implements ui.Canvas {
  final List<Symbol> calls = [];

  @override
  dynamic noSuchMethod(Invocation inv) {
    if (inv.isMethod) calls.add(inv.memberName);
    return null;
  }
}

Room _room(String id, double x, double y) => Room.fromJson({
      'id': id,
      'name': 'Room $id',
      'position': {'x': x, 'y': y},
      'size': {'w': 12, 'h': 8},
      'color': '#445566',
    });

List<Symbol> _draw({required List<Room> rooms, List<AgentState> agents = const []}) {
  final canvas = _Recorder();
  WorldPainter(
    rooms: rooms,
    agents: agents,
    camera: Offset.zero,
    zoom: 1.0,
    iso: const Iso(),
    tick: 0,
    badges: const {},
  ).paint(canvas, const Size(2000, 2000));
  return canvas.calls;
}

void main() {
  test('a room name is drawn over every room and every sprite', () {
    // Two rooms deep in each other's way, and someone standing in the near
    // one — which is exactly the arrangement that hid the far room's name.
    final calls = _draw(
      rooms: [_room('a', 0, 0), _room('b', 0, 10)],
      agents: [
        AgentState(
          id: 'w', name: 'Worker', roomId: 'b',
          x: 4, y: 12, color: 0xFF9AD1B0,
        ),
      ],
    );

    // Text goes through `drawParagraph`; floors, walls and benches are paths,
    // and a sprite is a recorded `Picture`.
    final firstText = calls.indexOf(#drawParagraph);
    final lastScene = [#drawPath, #drawLine, #drawPicture, #drawRect]
        .map((s) => calls.lastIndexOf(s))
        .reduce((a, b) => a > b ? a : b);

    expect(firstText, greaterThanOrEqualTo(0), reason: 'no labels drawn at all');
    expect(lastScene, greaterThanOrEqualTo(0), reason: 'nothing drawn at all');
    expect(firstText, greaterThan(lastScene),
        reason: 'a room name was drawn before the scene finished, so something '
            'painted over it');
  });

  test('every visible room still gets its name', () {
    // The labels moved out of `_room` into a pass of their own; a pass that
    // forgot a room would lose its name silently, and the map would simply
    // look emptier.
    final calls = _draw(rooms: [_room('a', 0, 0), _room('b', 0, 10),
                                _room('c', 14, 0)]);
    // One outlined draw and one filled draw per label.
    expect(calls.where((s) => s == #drawParagraph).length,
        greaterThanOrEqualTo(3));
  });
}
