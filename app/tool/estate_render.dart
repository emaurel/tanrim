import 'dart:convert';
import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:tanrim/model/castle.dart';
import 'package:tanrim/model/world.dart';
import 'package:tanrim/world/iso.dart';
import 'package:tanrim/world/painter.dart';

/// The map seen from far off, with the real rooms grouped into castles —
/// and, beside it, an invented second plugin, so the thing this view exists
/// for is actually visible. Today there is one castle.
void main() {
  setUpAll(() async {
    final f = File('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf');
    final b = File('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf');
    if (!f.existsSync()) return;
    final loader = FontLoader('Review')
      ..addFont(Future.value(ByteData.sublistView(f.readAsBytesSync())));
    if (b.existsSync()) {
      loader.addFont(Future.value(ByteData.sublistView(b.readAsBytesSync())));
    }
    await loader.load();
  });

  test('the estate', () async {
    final rooms = (jsonDecode(File('test/rooms_fixture.json').readAsStringSync())
            as List)
        .map((r) => Room.fromJson(r as Map<String, dynamic>))
        .toList();

    // A second and third castle, placed on the same grid, to show what this
    // view is for. They are not real: no second plugin declares rooms yet.
    final imagined = <Room>[
      for (var i = 0; i < 4; i++)
        Room.fromJson({
          'id': 'shop_$i',
          'name': 'Shop $i',
          'position': {'x': 62 + (i % 2) * 13, 'y': 6 + (i ~/ 2) * 9},
          'size': {'w': 12, 'h': 8},
          'color': '#3b4a6b',
        }),
      for (var i = 0; i < 2; i++)
        Room.fromJson({
          'id': 'mill_$i',
          'name': 'Mill $i',
          'position': {'x': 8 + i * 13, 'y': 44},
          'size': {'w': 12, 'h': 8},
          'color': '#5b3b4a'
        }),
    ];

    final castles = Castle.group(
      [...rooms, ...imagined],
      {
        'web_agency': rooms.map((r) => r.id).toList(),
        'shopfront': [for (var i = 0; i < 4; i++) 'shop_$i'],
        'mill': ['mill_0', 'mill_1'],
      },
      {
        'web_agency': 'Web agency',
        'shopfront': 'Shopfront',
        'mill': 'The Mill',
      },
    );

    const size = Size(1900, 1100);
    final recorder = ui.PictureRecorder();
    final canvas = Canvas(recorder);
    canvas.drawRect(Rect.fromLTWH(0, 0, size.width, size.height),
        Paint()..color = const Color(0xFF12141A));

    WorldPainter(
      rooms: const [],
      agents: const [],
      camera: const Offset(0, -40),
      // Below `farZoom`, which is what puts it in estate mode.
      zoom: 0.20,
      iso: const Iso(tileW: 56),
      tick: 0,
      castles: castles,
      castleBadges: const {'web_agency': 8, 'shopfront': 2},
      hoveredCastle: 'shopfront',
      fontFamily: 'Review',
    ).paint(canvas, size);

    final img = await recorder
        .endRecording()
        .toImage(size.width.toInt(), size.height.toInt());
    final png = await img.toByteData(format: ui.ImageByteFormat.png);
    File('build/estate.png').writeAsBytesSync(png!.buffer.asUint8List());
    expect(File('build/estate.png').lengthSync(), greaterThan(1000));
  });
}
