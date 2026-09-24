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
  Future<String> Function(String)? onRename,
  Future<void> Function()? onRaze,
  void Function(Room)? onOpenRoom,
  List<WorkRecord> records = const [],
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
        onClose: () {},
        onRename: onRename ?? (_) async => '',
        onRaze: onRaze ?? () async {},
        onOpenRoom: onOpenRoom ?? (_) {},
        records: records,
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
    expect(find.text('Web agency 1'), findsOneWidget);
    expect(find.text('Web agency'), findsOneWidget);       // the plugin
    expect(find.text('ring 1, plot 0'), findsOneWidget);
    expect(find.text('2 rooms'), findsOneWidget);
    expect(find.text('7 records'), findsOneWidget);
    // Its rooms, with whatever is waiting in them.
    expect(find.text('assay'), findsOneWidget);
    expect(find.text('factory'), findsOneWidget);
    expect(find.text('3'), findsOneWidget);
  });

  testWidgets('the name is edited in place', (t) async {
    // In place because the default is `[PLUGIN NAME] [N]` — it says what a
    // castle IS and nothing about what it is for, so renaming is the first
    // thing you do and should not be two clicks and a modal away.
    final asked = <String>[];
    await _panel(t, onRename: (n) async {
      asked.add(n);
      return '';
    });

    expect(find.byType(TextField), findsNothing);
    await t.tap(find.text('Web agency 1'));
    await t.pumpAndSettle();
    expect(find.byType(TextField), findsOneWidget);

    await t.enterText(find.byType(TextField), 'Nimes office');
    await t.testTextInput.receiveAction(TextInputAction.done);
    await t.pumpAndSettle();

    expect(asked, ['Nimes office']);
  });

  testWidgets('a refused rename says so and puts the old name back',
      (t) async {
    await _panel(t, onRename: (_) async => 'that name is taken');

    await t.tap(find.text('Web agency 1'));
    await t.pumpAndSettle();
    await t.enterText(find.byType(TextField), 'Something else');
    await t.testTextInput.receiveAction(TextInputAction.done);
    await t.pumpAndSettle();

    expect(find.text('that name is taken'), findsOneWidget);
    expect(find.text('Web agency 1'), findsOneWidget);
  });

  testWidgets('an empty name is not a rename', (t) async {
    var called = 0;
    await _panel(t, onRename: (_) async {
      called++;
      return '';
    });

    await t.tap(find.text('Web agency 1'));
    await t.pumpAndSettle();
    await t.enterText(find.byType(TextField), '   ');
    await t.testTextInput.receiveAction(TextInputAction.done);
    await t.pumpAndSettle();

    expect(called, 0);
    expect(find.text('Web agency 1'), findsOneWidget);
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
}

/// Whatever the framework caught while building, if anything.
Object? caughtException() =>
    TestWidgetsFlutterBinding.instance.takeException();
