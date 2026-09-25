
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:tanrim/model/record.dart';
import 'package:tanrim/ui/board.dart';

Map<String, dynamic> _row(String id, String stage,
        {String name = 'X', double updated = 0}) =>
    {'id': id, 'stage': stage, 'name': name, 'updated_ts': updated};

Future<void> _pump(WidgetTester t, Widget child) => t.pumpWidget(
    MaterialApp(home: Scaffold(body: SizedBox(width: 400, child: child))));

void main() {
  _runAllTests();
  testWidgets('stages come from the server, in its order', (t) async {
    // The environment is plugin-driven: a second plugin adds stages this build
    // has never heard of, so nothing in the app may name one.
    await _pump(
      t,
      Board(
        records: [
          WorkRecord(_row('1', 'zebra')),
          WorkRecord(_row('2', 'alpha')),
        ],
        stages: const ['zebra', 'alpha'],
        deadStages: const [],
        counts: const {},
        onTapRecord: (_) {},
      ),
    );
    final headings = t
        .widgetList<Text>(find.byType(Text))
        .map((w) => w.data)
        .where((s) => s == 'zebra' || s == 'alpha')
        .toList();
    expect(headings, ['zebra', 'alpha'], reason: 'server order, not alphabetical');
  });

  testWidgets('a record at a stage the server did not list still shows',
      (t) async {
    // Dropping it silently is how a record becomes invisible after a plugin
    // changes its pipeline — the operator would simply never see it again.
    await _pump(
      t,
      Board(
        records: [WorkRecord(_row('1', 'surprise', name: 'Orphan'))],
        stages: const ['known'],
        deadStages: const [],
        counts: const {},
        onTapRecord: (_) {},
      ),
    );
    expect(find.text('surprise'), findsOneWidget);
    await t.tap(find.text('surprise'));
    await t.pumpAndSettle();
    expect(find.text('Orphan'), findsOneWidget);
  });

  testWidgets('endings come last, and every stage starts collapsed',
      (t) async {
    // A board that sorts `lost` next to `sourced` buries the work still worth
    // doing, and 47 disqualified records would push everything off screen.
    //
    // All of them start closed now, endings included: a castle's work is a
    // dozen stages and seventy records, and the SHAPE of the pipeline — where
    // the work has piled up — is what you came to see.
    await _pump(
      t,
      Board(
        records: [
          WorkRecord(_row('1', 'live', name: 'Working')),
          WorkRecord(_row('2', 'lost', name: 'Gone')),
        ],
        stages: const ['live'],
        deadStages: const ['lost'],
        counts: const {},
        onTapRecord: (_) {},
      ),
    );
    expect(find.text('Working'), findsNothing, reason: 'stages start closed');
    expect(find.text('Gone'), findsNothing);

    // `live` above `lost`, and each opens on its own.
    expect(t.getTopLeft(find.text('live')).dy,
        lessThan(t.getTopLeft(find.text('lost')).dy));

    await t.tap(find.text('lost'));
    await t.pumpAndSettle();
    expect(find.text('Gone'), findsOneWidget);
    expect(find.text('Working'), findsNothing);
  });

  testWidgets('newest first within a stage', (t) async {
    await _pump(
      t,
      Board(
        records: [
          WorkRecord(_row('1', 's', name: 'Older', updated: 100)),
          WorkRecord(_row('2', 's', name: 'Newer', updated: 200)),
        ],
        stages: const ['s'],
        deadStages: const [],
        counts: const {},
        onTapRecord: (_) {},
      ),
    );
    await t.tap(find.text('s'));
    await t.pumpAndSettle();
    final older = t.getTopLeft(find.text('Older'));
    final newer = t.getTopLeft(find.text('Newer'));
    expect(newer.dy, lessThan(older.dy));
  });

  testWidgets('an empty pipeline says so rather than showing nothing',
      (t) async {
    await _pump(
      t,
      Board(
        records: const [],
        stages: const ['a'],
        deadStages: const [],
        counts: const {},
        onTapRecord: (_) {},
      ),
    );
    expect(find.text('nothing in the pipeline'), findsOneWidget);
  });

  test('a row shows what a plugin declared, without knowing what it means',
      () {
    final r = WorkRecord(const {
      'id': 'x', 'stage': 's', 'name': 'Chez Test',
      'city': 'Nîmes', 'email': 'a@b.fr',
      // structure and bookkeeping are not row content
      'profile': {'big': 'dossier'}, 'history_len': 4, 'ts': 1.0,
    });
    expect(r.summary, {'city': 'Nîmes', 'email': 'a@b.fr'});
  });

  test('the last history entry is what a row is really for', () {
    final r = WorkRecord(const {
      'id': 'x', 'stage': 's', 'name': 'n',
      'last': {'agent': 'probe', 'note': 'Google says CLOSED_PERMANENTLY'},
    });
    expect(r.lastNote, 'probe · Google says CLOSED_PERMANENTLY');
  });
}

void _runAllTests() {
  group('run all', () {
    List<WorkRecord> rows(String stage, int n) => [
          for (var i = 0; i < n; i++)
            WorkRecord({'id': 'r$i', 'name': 'Rec $i', 'stage': stage,
                        'kind': 'application'}),
        ];

    Widget board({
      Map<String, Map<String, dynamic>> drains = const {},
      void Function(String)? onRunAll,
      void Function(String)? onStopAll,
    }) =>
        MaterialApp(
          theme: ThemeData.dark(),
          home: Scaffold(
            body: Board(
              records: rows('spotted', 4),
              stages: const ['spotted'],
              deadStages: const ['lost'],
              counts: const {'spotted': 4},
              onTapRecord: (_) {},
              drains: drains,
              onRunAll: onRunAll,
              onStopAll: onStopAll,
            ),
          ),
        );

    testWidgets('a stage with work offers to run all of it', (t) async {
      String? asked;
      await t.pumpWidget(board(onRunAll: (s) => asked = s));
      await t.pumpAndSettle();

      await t.tap(find.byIcon(Icons.playlist_play_rounded));
      await t.pumpAndSettle();
      expect(asked, 'spotted');
    });

    testWidgets('no button without a server to ask', (t) async {
      // The board is drawn in places with no api — offering a control that
      // cannot be wired is offering something that does nothing.
      await t.pumpWidget(board());
      await t.pumpAndSettle();
      expect(find.byIcon(Icons.playlist_play_rounded), findsNothing);
    });

    testWidgets('while it runs, the count left is shown and it can be stopped',
        (t) async {
      // A drain of seventy is minutes long and the only question worth
      // answering on the header is whether it is still moving.
      String? stopped;
      await t.pumpWidget(board(
        onRunAll: (_) {},
        onStopAll: (s) => stopped = s,
        drains: const {
          'spotted': {'stage': 'spotted', 'total': 70, 'done': 9,
                      'refused': 3, 'running': true, 'stopping': false},
        },
      ));
      await t.pumpAndSettle();

      expect(find.text('12/70'), findsOneWidget);
      expect(find.byIcon(Icons.playlist_play_rounded), findsNothing);
      await t.tap(find.byIcon(Icons.stop_rounded));
      await t.pumpAndSettle();
      expect(stopped, 'spotted');
    });

    testWidgets('a drain already stopping cannot be stopped twice', (t) async {
      await t.pumpWidget(board(
        onRunAll: (_) {},
        onStopAll: (_) {},
        drains: const {
          'spotted': {'stage': 'spotted', 'total': 70, 'done': 60,
                      'refused': 0, 'running': true, 'stopping': true},
        },
      ));
      await t.pumpAndSettle();
      final btn = t.widget<IconButton>(
          find.ancestor(of: find.byIcon(Icons.stop_rounded),
                        matching: find.byType(IconButton)));
      expect(btn.onPressed, isNull);
    });
  });
}
