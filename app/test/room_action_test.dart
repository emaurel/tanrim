import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:tanrim/api/client.dart';
import 'package:tanrim/model/world.dart';
import 'package:tanrim/ui/room_panel.dart';

/// Records what the panel asks for, and answers with canned state.
///
/// A fake rather than a stub HTTP server: a widget test drives a fake clock,
/// so real socket I/O never completes inside `pumpAndSettle` and the whole
/// thing simply hangs. What is under test here is the BODY the panel builds,
/// which a fake pins exactly as well.
class _FakeApi extends Api {
  _FakeApi(this.state) : super('http://127.0.0.1:1');

  Map<String, dynamic> state;
  final List<(String, Object?)> posts = [];

  @override
  Future<dynamic> get(String path) async => state;

  @override
  Future<dynamic> post(String path, [Object? payload]) async {
    posts.add((path, payload));
    // What the server really answers for a well-formed action: starting the
    // task succeeded, whatever the task then decides.
    return {'ok': true, 'started': true};
  }
}

Map<String, dynamic> _state({
  Map<String, dynamic>? lastResult,
  List<Map<String, dynamic>> inFlight = const [],
}) => {
      'agent_id': 'forge',
      'action_name': 'run_build',
      'accepts_stages': ['visualised'],
      'running': false,
      'at_capacity': false,
      'worker_limit': 10,
      'workers_busy': 0,
      'in_flight': inFlight,
      'queue': [
        {
          'id': 'rec-1',
          'name': 'Piscines Bellerive',
          'stage': 'visualised',
          'kind': 'port',
          'updated_ts': 0,
        }
      ],
      'recent': [],
      'last_result': ?lastResult,
    };

Future<_FakeApi> _panel(WidgetTester t,
    {Map<String, dynamic>? lastResult,
    List<Map<String, dynamic>> inFlight = const []}) async {
  t.view
    ..physicalSize = const Size(520, 900)
    ..devicePixelRatio = 1.0;
  addTearDown(t.view.reset);

  final api = _FakeApi(_state(lastResult: lastResult, inFlight: inFlight));
  await t.pumpWidget(MaterialApp(
    home: Scaffold(
      body: RoomPanel(
        api: api,
        room: Room.fromJson({
          'id': 'factory@c1',
          'base_id': 'factory',
          'castle_id': 'c1',
          'name': 'Factory',
          'position': {'x': 0, 'y': 0},
          'size': {'w': 12, 'h': 8},
          'color': '#3d5a80',
        }),
        here: const [],
        onChanged: () {},
      ),
    ),
  ));
  // Pumped, not settled. A run in flight draws a `CircularProgressIndicator`,
  // which animates for ever — `pumpAndSettle` waits for the frames to stop and
  // so never returns.
  await t.pump();
  await t.pump(const Duration(milliseconds: 50));
  return api;
}

void main() {
  testWidgets('Run sends the record id where the server reads it', (t) async {
    // The bug this exists for: the panel sent `{name, lead_id}` while the
    // server reads an action's arguments from `body.payload`. Sent flat they
    // were dropped, the endpoint still answered `ok: true, started: true` —
    // because starting the task genuinely did succeed — and the task then
    // refused itself with "lead_id required" where nothing was looking.
    // Clicking Run did nothing, and said nothing either.
    final api = await _panel(t);
    expect(find.text('Piscines Bellerive'), findsOneWidget);

    await t.tap(find.byIcon(Icons.play_arrow).first);
    await t.pump();
    await t.pump(const Duration(milliseconds: 50));

    expect(api.posts, hasLength(1));
    final (path, body) = api.posts.single;
    expect(path, '/rooms/factory@c1/action');

    final sent = (body! as Map).cast<String, dynamic>();
    expect(sent['name'], 'run_build');
    expect(sent['payload'], isA<Map>());
    expect((sent['payload'] as Map)['lead_id'], 'rec-1');
    expect(sent.containsKey('lead_id'), isFalse,
        reason: 'the server does not read a top-level lead_id');
  });

  testWidgets('a run refused after it started is shown', (t) async {
    // The worse half of the bug. A refusal inside the task lands in
    // `last_result`, not `last_error`, and the POST has already answered
    // `ok: true` — so every deliberate refusal in this system was silent.
    await _panel(t, lastResult: const {
      'ok': false,
      'error': 'forge works records at [visualised]',
    });
    expect(find.textContaining('forge works records at'), findsOneWidget);
  });

  testWidgets('a run that succeeded shows nothing', (t) async {
    await _panel(t, lastResult: const {'ok': true});
    expect(find.textContaining('works records at'), findsNothing);
  });

  testWidgets('the queue says which pipeline a record is on', (t) async {
    // The Assay Room takes prospects and ports, which are different kinds of
    // work, and the queue gave no way to tell one row from another.
    await _panel(t);
    expect(find.text('port'), findsOneWidget);
  });

  testWidgets('a record being worked is not listed as waiting', (t) async {
    // A record stays at its stage until the run finishes, so a build in its
    // tenth minute sat in a list headed "Waiting" with a Run button beside it.
    await _panel(t, inFlight: [
      {
        'worker_id': 'forge@c1',
        'role': 'forge@c1',
        'lead_id': 'rec-1',
        'summary': 'building site: Piscines Bellerive',
        'workbench': 'site',
        'started_ts':
            DateTime.now().millisecondsSinceEpoch / 1000 - 90,
      }
    ]);

    expect(find.textContaining('WAITING'), findsNothing);
    expect(find.textContaining('1 RUNNING'), findsOneWidget);
    // What it is doing, and for how long, instead of a stage and a Run button.
    expect(find.textContaining('building site: Piscines Bellerive'), findsOneWidget);
    expect(find.textContaining('1m'), findsOneWidget);
    expect(find.byIcon(Icons.play_arrow), findsNothing);
    expect(find.byType(CircularProgressIndicator), findsOneWidget);
  });

  testWidgets('a record nobody is working still offers Run', (t) async {
    await _panel(t);
    expect(find.textContaining('WAITING'), findsOneWidget);
    expect(find.byIcon(Icons.play_arrow), findsOneWidget);
  });
}
