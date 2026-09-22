import 'dart:convert';
import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:tanrim/model/world.dart';
import 'package:tanrim/world/iso.dart';
import 'package:tanrim/world/painter.dart';

/// Paints the REAL room layout to a PNG.
///
/// Not an assertion — a way to look at the thing. An isometric scene either
/// reads as rooms you can walk into or it does not, and no unit test can tell
/// you which.
void main() {
  setUpAll(() async {
    // `flutter test` ships a placeholder font that draws every glyph as a
    // filled box, so without this the render is unreadable and it looks like
    // a bug in the painter. Load a real one.
    final path = File('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf');
    if (path.existsSync()) {
      final loader = FontLoader('Review')
        ..addFont(Future.value(
            ByteData.sublistView(path.readAsBytesSync())));
      await loader.load();
    }
  });

  test('render the real map', () async {
    final rooms = (jsonDecode(File('test/rooms_fixture.json').readAsStringSync())
            as List)
        .map((r) => Room.fromJson(r as Map<String, dynamic>))
        .toList();

    // One worker per room, standing where a bench is, so the sprites and the
    // depth sort are exercised rather than just the floors.
    final agents = <AgentState>[];
    for (final r in rooms) {
      if (r.agents.isEmpty) continue;
      final a = r.agents.first;
      final b = r.workbenches.where((w) => w.position != null).firstOrNull;
      agents.add(AgentState(
        id: a.id,
        name: a.name,
        roomId: r.id,
        x: r.position.x + (b?.position?.x ?? r.size.x / 2) + 0.5,
        y: r.position.y + (b?.position?.y ?? r.size.y / 2) + 1.4,
        color: a.color,
        busy: a.id.hashCode.isEven,
        status: 'working…',
        workbench: b?.id,
      ));
    }

    const size = Size(2000, 1250);
    const iso = Iso(tileW: 56);

    final recorder = ui.PictureRecorder();
    final canvas = Canvas(recorder);
    canvas.drawRect(
        Rect.fromLTWH(0, 0, size.width, size.height),
        Paint()..color = const Color(0xFF12141A));

    WorldPainter(
      rooms: rooms,
      agents: agents,
      // Centred by hand for the fixture; the app frames itself on first paint.
      camera: const Offset(0, -130),
      zoom: 0.62,
      iso: iso,
      tick: 0.4,
      badges: const {'comms': 2, 'publish': 1},
      selectedRoom: 'gallery',
      fontFamily: 'Review',
    ).paint(canvas, size);

    final img = await recorder
        .endRecording()
        .toImage(size.width.toInt(), size.height.toInt());
    final png = await img.toByteData(format: ui.ImageByteFormat.png);
    File('build/map.png').writeAsBytesSync(png!.buffer.asUint8List());
    expect(File('build/map.png').lengthSync(), greaterThan(1000));
  });
}
