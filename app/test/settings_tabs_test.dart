import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:tanrim/api/client.dart';
import 'package:tanrim/model/castle.dart';
import 'package:tanrim/model/world.dart';
import 'package:tanrim/ui/castle_gates.dart';
import 'package:tanrim/ui/castle_settings.dart';
import 'package:tanrim/ui/room_settings.dart';
import 'package:tanrim/ui/settings_tab.dart';

/// Records what was sent. Every one of these writes to `state/` on the server
/// and none of them touches a plugin's source, so the wire is what matters.
class _FakeApi extends Api {
  _FakeApi([this.reply]) : super('http://127.0.0.1:1');
  final Map<String, dynamic>? reply;
  final List<(String, String, Object?)> sent = [];

  @override
  Future<dynamic> get(String path) async =>
      reply ?? {'tools': ['site_inspect', 'domain_check'], 'skills': []};

  @override
  Future<dynamic> post(String path, [Object? payload]) async {
    sent.add(('POST', path, payload));
    return {'ok': true, 'tools': ['site_inspect'], 'skills': []};
  }

  @override
  Future<dynamic> send(String method, String path, [Object? payload]) async {
    sent.add((method, path, payload));
    return {'ok': true};
  }
}

Castle _castle() => Castle(
      id: 'c1', pluginId: 'web_agency', pluginName: 'Web agency',
      name: 'Web agency 1', ring: 1, slot: 0, centre: (0, -78), span: 52,
      records: 7, installed: true, rooms: const [],
    );

Room _room() => Room.fromJson({
      'id': 'factory@c1',
      'base_id': 'factory',
      'castle_id': 'c1',
      'name': 'Factory',
      'purpose': 'builds the site',
      'position': {'x': 0, 'y': 0},
      'size': {'w': 12, 'h': 8},
      'color': '#445566',
      'tools': ['site_inspect'],
      'skills': ['impeccable'],
      'agents': [
        {'id': 'forge@c1', 'name': 'Forge', 'role': 'builds',
         'color': '#98c1d9'},
      ],
      'workbenches': [
        {'id': 'floor', 'name': 'Build Floor', 'job': 'build',
         'stages': ['visualised', 'qa_failed']},
      ],
    });

Future<void> _pump(WidgetTester t, Widget child) async {
  t.view
    ..physicalSize = const Size(560, 1000)
    ..devicePixelRatio = 1.0;
  addTearDown(t.view.reset);
  await t.pumpWidget(MaterialApp(home: Scaffold(body: child)));
  await t.pumpAndSettle();
}

void main() {
  _gatesTests();
  group('castle settings', () {
    testWidgets('it renames through PATCH', (t) async {
      final api = _FakeApi();
      await _pump(t, CastleSettings(
          api: api, castle: _castle(), onChanged: () {}, onRaze: () async {}));

      await t.enterText(find.widgetWithText(TextField, 'Web agency 1'), 'Nimes');
      await t.pumpAndSettle();
      await t.tap(find.text('Save').first);
      await t.pumpAndSettle();

      expect(api.sent.first.$1, 'PATCH');
      expect(api.sent.first.$2, '/castles/c1');
      expect(api.sent.first.$3, {'name': 'Nimes'});
    });

    testWidgets('raze says the records survive, because they do', (t) async {
      var razed = false;
      await _pump(t, CastleSettings(
          api: _FakeApi(), castle: _castle(), onChanged: () {},
          onRaze: () async => razed = true));

      await t.tap(find.text('Raze this castle'));
      await t.pumpAndSettle();
      expect(find.textContaining('7 record(s) survive'), findsOneWidget);

      await t.tap(find.widgetWithText(FilledButton, 'Raze this castle'));
      await t.pumpAndSettle();
      expect(razed, isTrue);
    });

    testWidgets('cancelling razes nothing', (t) async {
      var razed = false;
      await _pump(t, CastleSettings(
          api: _FakeApi(), castle: _castle(), onChanged: () {},
          onRaze: () async => razed = true));
      await t.tap(find.text('Raze this castle'));
      await t.pumpAndSettle();
      await t.tap(find.text('Cancel'));
      await t.pumpAndSettle();
      expect(razed, isFalse);
    });
  });

  group('room settings', () {
    testWidgets('granting a tool sends the whole list, with the flag',
        (t) async {
      // `skills: []` and "did not send skills" are different requests, and
      // over JSON a null is indistinguishable from an omission — so the flag
      // is what says which field this call is about.
      final api = _FakeApi();
      await _pump(t, RoomSettings(
          api: api, room: _room(), workerLimit: 3, onChanged: () {}));

      await t.tap(find.widgetWithText(FilterChip, 'domain_check'));
      await t.pumpAndSettle();

      final (_, path, body) = api.sent.last;
      expect(path, '/rooms/factory@c1/grants');
      expect((body as Map)['set_tools'], isTrue);
      expect(body.containsKey('set_skills'), isFalse,
          reason: 'granting a tool also rewrote the skills');
      expect({...(body['tools'] as List)}, {'site_inspect', 'domain_check'});
    });

    testWidgets('unticking every skill sends an empty list, not nothing',
        (t) async {
      final api = _FakeApi();
      await _pump(t, RoomSettings(
          api: api, room: _room(), workerLimit: 3, onChanged: () {}));

      await t.tap(find.widgetWithText(FilterChip, 'impeccable'));
      await t.pumpAndSettle();

      final body = api.sent.last.$3 as Map;
      expect(body['set_skills'], isTrue);
      expect(body['skills'], isEmpty);
    });

    testWidgets('the colour swatch IS the colour, and opens a wheel',
        (t) async {
      // A hex field was the first version and it is the wrong instrument:
      // nobody reads `#98c1d9` and pictures a colour, and one character wrong
      // gives a plausible different colour rather than an error.
      final api = _FakeApi();
      await _pump(t, RoomSettings(
          api: api, room: _room(), workerLimit: 3, onChanged: () {}));

      final swatch = find.descendant(
          of: find.byType(ColorField), matching: find.byType(Container));
      final box = t.widget<Container>(swatch.first).decoration as BoxDecoration;
      expect(box.color, const Color(0xff98c1d9),
          reason: 'the swatch showed something other than the agent colour');

      await t.tap(find.descendant(
          of: find.byType(ColorField), matching: find.byType(InkWell)));
      await t.pumpAndSettle();
      expect(find.byType(AlertDialog), findsOneWidget);
    });

    testWidgets('picking a colour sends #rrggbb, which is what is stored',
        (t) async {
      final api = _FakeApi();
      await _pump(t, RoomSettings(
          api: api, room: _room(), workerLimit: 3, onChanged: () {}));

      await t.tap(find.descendant(
          of: find.byType(ColorField), matching: find.byType(InkWell)));
      await t.pumpAndSettle();
      // Accepting without moving anything: the round trip through HSV and back
      // must not shift the colour, or opening the picker would change it.
      await t.tap(find.widgetWithText(FilledButton, 'Use this'));
      await t.pumpAndSettle();

      final (_, path, body) = api.sent.last;
      expect(path, '/agents/forge@c1/identity');
      expect((body as Map)['color'], '#98c1d9');
    });

    testWidgets('cancelling sends nothing at all', (t) async {
      final api = _FakeApi();
      await _pump(t, RoomSettings(
          api: api, room: _room(), workerLimit: 3, onChanged: () {}));

      await t.tap(find.descendant(
          of: find.byType(ColorField), matching: find.byType(InkWell)));
      await t.pumpAndSettle();
      await t.tap(find.widgetWithText(TextButton, 'Cancel'));
      await t.pumpAndSettle();

      expect(api.sent, isEmpty);
    });

    testWidgets('an agent is renamed per castle, not per role', (t) async {
      final api = _FakeApi();
      await _pump(t, RoomSettings(
          api: api, room: _room(), workerLimit: 3, onChanged: () {}));

      await t.enterText(find.widgetWithText(TextField, 'Forge'), 'Builder');
      await t.pumpAndSettle();
      // The ENABLED Save: there is one per field and only the edited field's
      // is live, so `.first` would tap the crew row's dead button.
      await t.tap(find.byWidgetPredicate((w) =>
          w is TextButton && w.onPressed != null &&
          w.child is Text && (w.child as Text).data == 'Save'));
      await t.pumpAndSettle();

      final (_, path, body) = api.sent.last;
      expect(path, '/agents/forge@c1/identity',
          reason: 'renamed the role rather than this castle\'s worker');
      expect(body, {'name': 'Builder'});
    });

    testWidgets('benches are shown and not offered', (t) async {
      // A bench declares which stages are worked here, and that IS the
      // routing table — editing it from a settings pane edits the pipeline.
      await _pump(t, RoomSettings(
          api: _FakeApi(), room: _room(), workerLimit: 3, onChanged: () {}));

      expect(find.text('Build Floor'), findsOneWidget);
      expect(find.text('visualised, qa_failed'), findsOneWidget);
      expect(find.widgetWithText(TextField, 'Build Floor'), findsNothing);
    });
  });

  group('the shared pieces', () {
    testWidgets('a field does not write until you commit it', (t) async {
      // A name that PATCHed per keystroke would send eleven requests to type
      // "Web agency".
      var writes = 0;
      await _pump(t, EditableField(
        label: 'Name',
        value: 'before',
        onSubmit: (v) async {
          writes++;
          return '';
        },
      ));
      await t.enterText(find.byType(TextField), 'after');
      await t.pumpAndSettle();
      expect(writes, 0);

      await t.tap(find.text('Save'));
      await t.pumpAndSettle();
      expect(writes, 1);
    });

    testWidgets('an unchanged field cannot be saved', (t) async {
      await _pump(t, EditableField(
          label: 'Name', value: 'same', onSubmit: (v) async => ''));
      expect(t.widget<TextButton>(find.widgetWithText(TextButton, 'Save'))
          .onPressed, isNull);
    });

    testWidgets('a refusal is shown next to the field that caused it',
        (t) async {
      await _pump(t, EditableField(
        label: 'Ring',
        value: '1',
        onSubmit: (v) async => 'that plot is taken',
      ));
      await t.enterText(find.byType(TextField), '9');
      await t.pumpAndSettle();
      await t.tap(find.text('Save'));
      await t.pumpAndSettle();
      expect(find.text('that plot is taken'), findsOneWidget);
    });
  });
}

/// Answers `/pipeline` the way the server does: the steps it returns depend on
/// the `kind` it was asked for, and an unfiltered ask returns every plugin's.
class _PipelineApi extends Api {
  _PipelineApi() : super('http://127.0.0.1:1');
  final List<String> asked = [];
  final List<(String, Object?)> posted = [];

  static const _all = [
    {'stage': 'sourced', 'record_kind': 'application', 'role': 'scout',
     'room_name': 'Scouts', 'gated': false, 'waiting': 0},
    {'stage': 'screened', 'record_kind': 'application', 'role': 'reader',
     'room_name': 'Desk', 'gated': true, 'waiting': 0, 'permanent': true,
     'permanent_reason': 'this one sends an email'},
    {'stage': 'answered', 'record_kind': 'prospect', 'role': 'probe',
     'room_name': 'Probe', 'gated': false, 'waiting': 0},
  ];

  @override
  Future<dynamic> get(String path) async {
    asked.add(path);
    final kind = Uri.parse(path).queryParameters['kind'] ?? '';
    return {
      'kinds': ['application'],
      'steps': [
        for (final s in _all)
          if (kind.isEmpty || s['record_kind'] == kind) s,
      ],
    };
  }

  @override
  Future<dynamic> post(String path, [Object? payload]) async {
    posted.add((path, payload));
    return {'ok': true};
  }
}

void _gatesTests() {
  group('castle gates', () {
    testWidgets('a castle shows only the pipelines that run in it', (t) async {
      // Every plugin's pipelines are merged into one table, so an unfiltered
      // answer put a web agency's stages inside a job hunt's castle — and the
      // switch beside one of them wrote a gate no dispatch would ever consult.
      final api = _PipelineApi();
      await _pump(t, CastleGates(api: api, castle: _castle()));
      await t.pumpAndSettle();

      expect(find.text('sourced'), findsOneWidget);
      expect(find.text('answered'), findsNothing,
          reason: 'another plugin\'s stage was listed in this castle');
    });

    testWidgets('the kind is adopted from the server, not from the records',
        (t) async {
      // Taken from the records, a castle with none yet offered nothing and
      // could not be gated until after its first arrived — exactly when you
      // would want to.
      final api = _PipelineApi();
      await _pump(t, CastleGates(api: api, castle: _castle()));
      await t.pumpAndSettle();

      expect(api.asked.first, contains('kind='));
      expect(api.asked.last, contains('kind=application'),
          reason: 'it never re-asked, so the list is still every pipeline');
    });

    testWidgets('toggling sends the castle and the kind it is showing',
        (t) async {
      final api = _PipelineApi();
      await _pump(t, CastleGates(api: api, castle: _castle()));
      await t.pumpAndSettle();

      await t.tap(find.byType(Switch).first);
      await t.pumpAndSettle();

      final (path, body) = api.posted.single;
      expect(path, '/pipeline/gate');
      expect((body as Map)['castle_id'], 'c1');
      expect(body['kind'], 'application');
      expect(body['stage'], 'sourced');
    });

    testWidgets('a permanent gate cannot be moved, and says why', (t) async {
      // Anything irreversible or outward-facing must never depend on a
      // checkbox — so the switch does not move, rather than springing back.
      final api = _PipelineApi();
      await _pump(t, CastleGates(api: api, castle: _castle()));
      await t.pumpAndSettle();

      final switches = t.widgetList<Switch>(find.byType(Switch)).toList();
      final permanent = switches[1];
      expect(permanent.value, isTrue);
      expect(permanent.onChanged, isNull,
          reason: 'a permanent gate offered a switch that would do nothing');
      expect(find.text('this one sends an email'), findsOneWidget);
    });
  });
}
