import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:tanrim/model/record.dart';
import 'package:tanrim/model/world.dart';
import 'package:tanrim/ui/board.dart';
import 'package:tanrim/ui/map_view.dart';

/// The whole UI, painted to a PNG with the REAL payloads.
///
/// Not an assertion. A console is either readable at a glance or it is not,
/// and that is not a thing a unit test can tell you.
void main() {
  setUpAll(() async {
    final f = File('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf');
    if (!f.existsSync()) return;
    final bold = File('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf');
    for (final family in ['Roboto', 'packages/tanrim/Roboto']) {
      final loader = FontLoader(family)
        ..addFont(Future.value(ByteData.sublistView(f.readAsBytesSync())));
      if (bold.existsSync()) {
        loader.addFont(
            Future.value(ByteData.sublistView(bold.readAsBytesSync())));
      }
      await loader.load();
    }
  });

  testWidgets('the console, with real data', (tester) async {
    tester.view
      ..physicalSize = const Size(1800, 1050)
      ..devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final rooms = (jsonDecode(File('test/rooms_fixture.json').readAsStringSync())
            as List)
        .map((r) => Room.fromJson(r as Map<String, dynamic>))
        .toList();
    final board =
        jsonDecode(File('test/board_fixture.json').readAsStringSync())
            as Map<String, dynamic>;
    final records = ((board['leads'] ?? []) as List)
        .map((r) => WorkRecord(r as Map<String, dynamic>))
        .toList();

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

    await tester.pumpWidget(MaterialApp(
      theme: ThemeData(
        useMaterial3: true,
        brightness: Brightness.dark,
        fontFamily: 'Roboto',
        colorScheme: ColorScheme.fromSeed(
            seedColor: const Color(0xFF7AD7D7), brightness: Brightness.dark),
        scaffoldBackgroundColor: const Color(0xFF12141A),
      ),
      home: Scaffold(
        body: Column(children: [
          Container(
            padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
            color: const Color(0xFF191C24),
            child: Row(children: const [
              Text('Tanrim',
                  style: TextStyle(fontWeight: FontWeight.w700, fontSize: 16)),
              SizedBox(width: 16),
              Text('http://127.0.0.1:8765',
                  style: TextStyle(fontSize: 13, color: Colors.white54)),
              SizedBox(width: 16),
              Icon(Icons.circle, size: 10, color: Color(0xFF63C77B)),
              SizedBox(width: 6),
              Text('12 rooms · live',
                  style: TextStyle(color: Colors.white60)),
            ]),
          ),
          Expanded(
            child: Row(children: [
              Expanded(
                child: MapView(
                  rooms: rooms,
                  agents: agents,
                  badges: const {'comms': 2, 'publish': 1},
                  selectedRoom: 'assay',
                  onRoomTapped: (_) {},
                ),
              ),
              SizedBox(
                width: 380,
                child: Container(
                  color: const Color(0xFF161922),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Padding(
                        padding: const EdgeInsets.fromLTRB(16, 14, 16, 2),
                        child: Row(children: [
                          const Text('Board',
                              style: TextStyle(
                                  fontSize: 18, fontWeight: FontWeight.w700)),
                          const SizedBox(width: 8),
                          Text('${records.length}',
                              style:
                                  const TextStyle(color: Colors.white38)),
                        ]),
                      ),
                      Expanded(
                        child: Board(
                          records: records,
                          stages:
                              ((board['stages'] ?? []) as List).cast<String>(),
                          deadStages: ((board['dead_stages'] ?? []) as List)
                              .cast<String>(),
                          counts: const {},
                          onTapRecord: (_) {},
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ]),
          ),
        ]),
      ),
    ));

    await tester.pumpAndSettle(const Duration(milliseconds: 300));
    // The map frames itself on first layout; give it a frame to settle.
    await tester.pump(const Duration(milliseconds: 100));

    // A golden, written with `--update-goldens`, and never asserted in an
    // ordinary run — the rows show relative times ("56m"), so the image
    // legitimately differs every time. It exists to be LOOKED at:
    //
    //     flutter test --tags render --update-goldens
    //     # then open build/console.png
    //
    // `dart_test.yaml` excludes the tag from the ordinary suite.
    await expectLater(
      find.byType(MaterialApp),
      matchesGoldenFile('../build/console.png'),
    );
  });
}
