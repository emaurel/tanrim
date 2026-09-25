import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:tanrim/model/castle.dart';
import 'package:tanrim/model/record.dart';
import 'package:tanrim/model/world.dart';
import 'package:tanrim/ui/castle_dialogs.dart';
import 'package:tanrim/ui/castle_panel.dart';

Room _room(String id, String castle) => Room.fromJson({
      'id': '$id@$castle',
      'base_id': id,
      'castle_id': castle,
      'name': id,
      'position': {'x': 0, 'y': 0},
      'size': {'w': 4, 'h': 4},
      'color': '#445566',
    });

Castle _castle({int records = 7, bool installed = true}) => Castle(
      id: 'c1',
      pluginId: 'web_agency',
      pluginName: 'Web agency',
      name: 'Web agency 1',
      ring: 1,
      slot: 0,
      centre: (0, -78),
      span: 52,
      records: records,
      installed: installed,
      rooms: const [],
    );

Future<void> _panel(
  WidgetTester t, {
  Castle? castle,
  List<Room>? rooms,
  Future<void> Function()? onRaze,
  void Function(Room)? onOpenRoom,
  List<WorkRecord> records = const [],
  List<AgentState> agents = const [],
}) async {
  t.view
    ..physicalSize = const Size(520, 900)
    ..devicePixelRatio = 1.0;
  addTearDown(t.view.reset);

  await t.pumpWidget(MaterialApp(
    home: Scaffold(
      body: CastlePanel(
        castle: castle ?? _castle(),
        rooms: rooms ?? [_room('assay', 'c1'), _room('factory', 'c1')],
        badges: const {'assay@c1': 3},
        onRaze: onRaze ?? () async {},
        onOpenRoom: onOpenRoom ?? (_) {},
        records: records,
        agents: agents,
        stages: const ['sourced', 'qualified'],
        deadStages: const ['lost'],
        onTapRecord: (_) {},
      ),
    ),
  ));
  await t.pumpAndSettle();
}

void main() {
  testWidgets('it shows what the castle is and what is in it', (t) async {
    await _panel(t);
    // The NAME is the window's title bar's job now — this is the body.
    expect(find.text('Web agency'), findsOneWidget);       // the plugin
    expect(find.text('ring 1, plot 0'), findsOneWidget);
    expect(find.text('2 rooms'), findsOneWidget);
    expect(find.text('7 records'), findsOneWidget);
    // Its rooms, with whatever is waiting in them.
    expect(find.text('assay'), findsOneWidget);
    expect(find.text('factory'), findsOneWidget);
    expect(find.text('3'), findsOneWidget);
  });




  testWidgets('a castle whose plugin is gone explains itself', (t) async {
    await _panel(t, castle: _castle(installed: false), rooms: const []);
    expect(find.text('plugin not installed'), findsOneWidget);
    expect(find.textContaining('Install or enable it'), findsOneWidget);
  });

  testWidgets('a room opens from the list', (t) async {
    final opened = <String>[];
    await _panel(t, onOpenRoom: (r) => opened.add(r.id));
    await t.tap(find.text('factory'));
    await t.pumpAndSettle();
    expect(opened, ['factory@c1']);
  });

  testWidgets('razing says the records survive', (t) async {
    await _panel(t);
    expect(find.textContaining('7 record(s) would be kept'), findsOneWidget);
  });

  group('the dialogs still build', () {
    // `AlertDialog.actions` lay out in an `OverflowBar`, not a `Row`, so a
    // `Spacer` in them throws `_OverflowBarParentData is not a subtype of
    // FlexParentData` — which is what clicking a castle used to do.
    testWidgets('the raze confirmation', (t) async {
      await t.pumpWidget(MaterialApp(
        home: Builder(
          builder: (ctx) => Scaffold(
            body: ElevatedButton(
              onPressed: () => confirmRaze(ctx, _castle()),
              child: const Text('go'),
            ),
          ),
        ),
      ));
      await t.tap(find.text('go'));
      await t.pumpAndSettle();
      expect(caughtException(), isNull);
      expect(find.text('Raze Web agency 1?'), findsOneWidget);
    });

    testWidgets('the build chooser', (t) async {
      await t.pumpWidget(MaterialApp(
        home: Builder(
          builder: (ctx) => Scaffold(
            body: ElevatedButton(
              onPressed: () => askWhatToBuild(ctx,
                  buildable: const [
                    {'id': 'web_agency', 'name': 'Web agency', 'built': 1},
                  ],
                  ring: 1,
                  slot: 2),
              child: const Text('go'),
            ),
          ),
        ),
      ));
      await t.tap(find.text('go'));
      await t.pumpAndSettle();
      expect(caughtException(), isNull);
      expect(find.text('Build here'), findsOneWidget);
      expect(find.text('Ring 1, plot 2'), findsOneWidget);
    });
  });

  testWidgets('the work comes before the rooms', (t) async {
    // A castle is a place that DOES something; its rooms are how. You open one
    // to look at what is in it rather than at the building.
    await _panel(t, records: [
      WorkRecord(const {
        'id': 'r1', 'name': 'Table des Ormes', 'stage': 'sourced',
        'kind': 'prospect', 'castle_id': 'c1', 'updated_ts': 0,
      }),
    ]);

    expect(find.text('prospect (1)'), findsOneWidget);
    expect(find.text('Rooms (2)'), findsOneWidget);
    expect(t.getTopLeft(find.text('prospect (1)')).dx,
        lessThan(t.getTopLeft(find.text('Rooms (2)')).dx));
  });

  group('the working tab', () {
    AgentState busyOn(String recordId,
            {String room = 'factory@c1',
            String name = 'Forge',
            String? bench = 'floor',
            String? say,
            double? since}) =>
        AgentState(
          id: 'w-$recordId', name: name, roomId: room, x: 0, y: 0,
          color: 0xFF9AD1B0, busy: true, recordId: recordId,
          workbench: bench, say: say,
          busySince: since ??
              DateTime.now().millisecondsSinceEpoch / 1000 - 240,
        );

    final record = WorkRecord(const {
      'id': 'r1', 'name': 'Table des Ormes', 'stage': 'sourced',
      'kind': 'prospect', 'castle_id': 'c1', 'updated_ts': 0,
    });

    testWidgets('it comes first, and says what is running where', (t) async {
      await _panel(t, records: [record], agents: [busyOn('r1')]);

      expect(find.text('Working (1)'), findsOneWidget);
      // First, so the thing happening right now is what the window opens on.
      expect(t.getTopLeft(find.text('Working (1)')).dx,
          lessThan(t.getTopLeft(find.text('prospect (1)')).dx));

      expect(find.text('Table des Ormes'), findsOneWidget);
      expect(find.text('Forge'), findsOneWidget);   // who
      expect(find.text('factory'), findsOneWidget); // where
      expect(find.text('sourced'), findsWidgets);   // what state
      expect(find.text('4m'), findsOneWidget);      // how long
    });

    testWidgets('no tab at all when nothing is running', (t) async {
      // A tab that is empty most of the time trains you to skip it, and the
      // whole value of this one is that its presence means something is up.
      await _panel(t, records: [record]);
      expect(find.textContaining('Working'), findsNothing);
      expect(find.text('prospect (1)'), findsOneWidget);
    });

    testWidgets('another castle\'s worker is not this castle\'s work',
        (t) async {
      await _panel(t,
          records: [record], agents: [busyOn('r1', room: 'factory@OTHER')]);
      expect(find.textContaining('Working'), findsNothing);
    });

    testWidgets('an idle worker is not working', (t) async {
      await _panel(t, records: [record], agents: [
        AgentState(
            id: 'w', name: 'Forge', roomId: 'factory@c1', x: 0, y: 0,
            color: 0xFF9AD1B0, busy: false, recordId: 'r1'),
      ]);
      expect(find.textContaining('Working'), findsNothing);
    });

    testWidgets('a worker on no record still shows, because it is still work',
        (t) async {
      // Sourcing takes a place, not a record. A blank line would be worse
      // than saying which agent is doing it.
      await _panel(t, agents: [busyOn('', room: 'assay@c1', name: 'Nova')]);
      expect(find.text('Working (1)'), findsOneWidget);
      expect(find.textContaining('no record'), findsOneWidget);
    });

    testWidgets('what the agent is saying is shown', (t) async {
      await _panel(t,
          records: [record], agents: [busyOn('r1', say: 'reading the menu…')]);
      expect(find.text('reading the menu…'), findsOneWidget);
    });

    testWidgets('the longest-running is first', (t) async {
      final now = DateTime.now().millisecondsSinceEpoch / 1000;
      final second = WorkRecord(const {
        'id': 'r2', 'name': 'Comptoir des Lices', 'stage': 'sourced',
        'kind': 'prospect', 'castle_id': 'c1', 'updated_ts': 0,
      });
      await _panel(t, records: [record, second], agents: [
        busyOn('r1', since: now - 60),
        busyOn('r2', room: 'assay@c1', since: now - 3600),
      ]);
      expect(t.getTopLeft(find.text('Comptoir des Lices')).dy,
          lessThan(t.getTopLeft(find.text('Table des Ormes')).dy));
      expect(find.text('1h 0m'), findsOneWidget);
    });
  });

  testWidgets('a castle with no work at all still lands somewhere', (t) async {
    // Which kinds a castle has depends on what is installed, so the first tab
    // is resolved rather than fixed.
    await _panel(t);
    expect(find.text('Rooms (2)'), findsOneWidget);
    expect(find.text('assay'), findsOneWidget);
  });
}

/// Whatever the framework caught while building, if anything.
Object? caughtException() =>
    TestWidgetsFlutterBinding.instance.takeException();
