import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:tanrim/api/client.dart';
import 'package:tanrim/model/record.dart';
import 'package:tanrim/ui/board.dart';
import 'package:tanrim/ui/record_window.dart';

/// Records what was POSTed, so a test can assert the wire rather than the UI's
/// intention. Every one of these routes existed for months with nothing
/// reaching them; what matters is that the right path is called.
class _FakeApi extends Api {
  _FakeApi(this.view, {this.fail}) : super('http://127.0.0.1:1');

  final Map<String, dynamic> view;

  /// path -> the error to throw instead of answering.
  final Map<String, ApiError>? fail;
  final List<(String, Object?)> posted = [];
  Map<String, dynamic> Function(String)? reply;

  @override
  Future<dynamic> get(String path) async => view;

  @override
  Future<dynamic> post(String path, [Object? payload]) async {
    posted.add((path, payload));
    final boom = fail?[path];
    if (boom != null && posted.where((p) => p.$1 == path).length == 1) {
      throw boom;
    }
    return reply?.call(path) ?? {'ok': true};
  }
}

Map<String, dynamic> _view({String stage = 'built'}) => {
      'id': 'r1',
      'name': "Table des Ormes",
      'kind': 'prospect',
      'stage': stage,
      'blocks': const [],
    };

Future<_FakeApi> _open(WidgetTester t,
    {bool working = false,
    List<String> stages = const ['sourced', 'built', 'published', 'lost'],
    Map<String, ApiError>? fail}) async {
  t.view
    ..physicalSize = const Size(620, 900)
    ..devicePixelRatio = 1.0;
  addTearDown(t.view.reset);

  final api = _FakeApi(_view(), fail: fail);
  await t.pumpWidget(MaterialApp(
    home: Scaffold(
      body: RecordWindow(
          api: api, recordId: 'r1', working: working, stages: stages),
    ),
  ));
  await t.pumpAndSettle();
  return api;
}

void main() {
  group('the working dot', () {
    testWidgets('a record with somebody on it says so', (t) async {
      await _open(t, working: true);
      expect(find.text('working'), findsOneWidget);
    });

    testWidgets('and a record with nobody on it does not', (t) async {
      await _open(t);
      expect(find.text('working'), findsNothing);
    });

    testWidgets('a board row is marked, and its folded stage too', (t) async {
      // The dot has to survive on a CLOSED group, or "something is running" is
      // a thing you can only learn by opening every stage in turn.
      t.view
        ..physicalSize = const Size(500, 700)
        ..devicePixelRatio = 1.0;
      addTearDown(t.view.reset);

      final rows = [
        WorkRecord(const {
          'id': 'a', 'name': 'Running', 'stage': 'built',
          'kind': 'prospect', 'castle_id': 'c1', 'updated_ts': 0,
        }),
        WorkRecord(const {
          'id': 'b', 'name': 'Idle', 'stage': 'built',
          'kind': 'prospect', 'castle_id': 'c1', 'updated_ts': 0,
        }),
      ];
      await t.pumpWidget(MaterialApp(
        home: Scaffold(
          body: Board(
            records: rows,
            stages: const ['built'],
            deadStages: const [],
            counts: const {'built': 2},
            working: const {'a'},
            onTapRecord: (_) {},
          ),
        ),
      ));
      await t.pumpAndSettle();

      // Closed: one dot for the group.
      expect(find.byTooltip('an agent is working on this'), findsOneWidget);

      await t.tap(find.text('built'));
      await t.pumpAndSettle();
      // Open: the group's dot plus the one row that is running. The idle row
      // has none, which is the assertion that matters.
      expect(find.byTooltip('an agent is working on this'), findsNWidgets(2));
    });
  });

  group('running from the board', () {
    testWidgets('a row offers run, and a working row offers stop', (t) async {
      // Two clicks and a window to press Run is the wrong shape for something
      // you decide FROM the board — the board is where you can see which
      // stage everything is sitting at, which is what tells you what to run.
      t.view
        ..physicalSize = const Size(560, 700)
        ..devicePixelRatio = 1.0;
      addTearDown(t.view.reset);

      final started = <String>[];
      final stopped = <String>[];
      final rows = [
        WorkRecord(const {
          'id': 'a', 'name': 'Running', 'stage': 'built',
          'kind': 'prospect', 'castle_id': 'c1', 'updated_ts': 0,
        }),
        WorkRecord(const {
          'id': 'b', 'name': 'Idle', 'stage': 'built',
          'kind': 'prospect', 'castle_id': 'c1', 'updated_ts': 0,
        }),
      ];
      await t.pumpWidget(MaterialApp(
        home: Scaffold(
          body: Board(
            records: rows,
            stages: const ['built'],
            deadStages: const [],
            counts: const {'built': 2},
            working: const {'a'},
            onTapRecord: (_) {},
            onRun: (r) => started.add(r.id),
            onStop: (r) => stopped.add(r.id),
          ),
        ),
      ));
      await t.pumpAndSettle();
      await t.tap(find.text('built'));
      await t.pumpAndSettle();

      // The idle row gets run; the working one gets stop, not both.
      expect(find.byTooltip('run the next step'), findsOneWidget);
      expect(find.byTooltip('stop what is working this'), findsOneWidget);

      await t.tap(find.byTooltip('run the next step'));
      await t.pumpAndSettle();
      expect(started, ['b'], reason: 'run was offered on the wrong row');

      await t.tap(find.byTooltip('stop what is working this'));
      await t.pumpAndSettle();
      expect(stopped, ['a']);
    });

    testWidgets('a board with no server offers neither', (t) async {
      // `Board` is drawn in places with nothing to call — the callbacks are
      // optional and the buttons must not appear without them.
      await t.pumpWidget(MaterialApp(
        home: Scaffold(
          body: Board(
            records: [
              WorkRecord(const {
                'id': 'a', 'name': 'Idle', 'stage': 'built',
                'kind': 'prospect', 'castle_id': 'c1', 'updated_ts': 0,
              }),
            ],
            stages: const ['built'],
            deadStages: const [],
            counts: const {'built': 1},
            onTapRecord: (_) {},
          ),
        ),
      ));
      await t.pumpAndSettle();
      await t.tap(find.text('built'));
      await t.pumpAndSettle();
      expect(find.byTooltip('run the next step'), findsNothing);
    });
  });

  group('start and stop', () {
    testWidgets('start asks the server which room, rather than guessing',
        (t) async {
      final api = await _open(t);
      await t.tap(find.text('Start'));
      await t.pumpAndSettle();
      expect(api.posted.first.$1, '/leads/r1/run-next');
    });

    testWidgets('stop is offered instead of start while it runs', (t) async {
      final api = await _open(t, working: true);
      expect(find.text('Start'), findsNothing);
      await t.tap(find.text('Stop'));
      await t.pumpAndSettle();
      expect(api.posted.first.$1, '/leads/r1/stop');
    });

    testWidgets('nothing running is a sentence, not a crash', (t) async {
      final api = await _open(t, working: true);
      api.reply = (_) => {'ok': false, 'error': 'nothing is running on this lead'};
      await t.tap(find.text('Stop'));
      await t.pumpAndSettle();
      expect(find.text('nothing is running on this lead'), findsOneWidget);
    });
  });

  group('moving it by hand', () {
    testWidgets('the reason is required, because the history is the only '
        'record of why', (t) async {
      await _open(t);
      await t.tap(find.text('Move…'));
      await t.pumpAndSettle();

      final move = find.widgetWithText(FilledButton, 'Move');
      expect(t.widget<FilledButton>(move).onPressed, isNull,
          reason: 'a move with no stage and no reason was offered');

      await t.tap(find.byType(DropdownButtonFormField<String>));
      await t.pumpAndSettle();
      await t.tap(find.text('published').last);
      await t.pumpAndSettle();
      expect(t.widget<FilledButton>(move).onPressed, isNull,
          reason: 'a stage with no reason was enough');

      await t.enterText(find.byType(TextField), 'they called, it is live');
      await t.pumpAndSettle();
      expect(t.widget<FilledButton>(move).onPressed, isNotNull);
    });

    testWidgets('it sends the stage and the reason', (t) async {
      final api = await _open(t);
      await t.tap(find.text('Move…'));
      await t.pumpAndSettle();
      await t.tap(find.byType(DropdownButtonFormField<String>));
      await t.pumpAndSettle();
      await t.tap(find.text('lost').last);
      await t.pumpAndSettle();
      await t.enterText(find.byType(TextField), 'they said no');
      await t.pumpAndSettle();          // the button re-enables on setState
      await t.tap(find.widgetWithText(FilledButton, 'Move'));
      await t.pumpAndSettle();

      expect(api.posted.first.$1, '/leads/r1/stage');
      expect(api.posted.first.$2, {'stage': 'lost', 'reason': 'they said no'});
    });

    testWidgets('a 409 is advice, and going on resends with force', (t) async {
      // The rework guard REFUSES with a sentence explaining what the move
      // would mean to a business holding our email. Hiding that behind a
      // generic failure would make the guard useless.
      final api = await _open(t, fail: {
        '/leads/r1/stage': ApiError('/leads/r1/stage', 409,
            '{"detail": "it was emailed to a@b.fr and they have not replied"}'),
      });
      await t.tap(find.text('Move…'));
      await t.pumpAndSettle();
      await t.tap(find.byType(DropdownButtonFormField<String>));
      await t.pumpAndSettle();
      await t.tap(find.text('sourced').last);
      await t.pumpAndSettle();
      await t.enterText(find.byType(TextField), 'start again');
      await t.pumpAndSettle();
      await t.tap(find.widgetWithText(FilledButton, 'Move'));
      // Fixed pumps, not `pumpAndSettle`: `_act` raises a SnackBar and its
      // dismiss timer keeps the scheduler busy, so settling never returns.
      for (var i = 0; i < 6; i++) {
        await t.pump(const Duration(milliseconds: 120));
      }

      expect(find.textContaining('they have not replied'), findsOneWidget);
      await t.tap(find.text('Move anyway'));
      for (var i = 0; i < 6; i++) {
        await t.pump(const Duration(milliseconds: 120));
      }

      expect(api.posted.length, 2);
      expect(api.posted.last.$2,
          {'stage': 'sourced', 'reason': 'start again', 'force': true});
    });
  });
}
