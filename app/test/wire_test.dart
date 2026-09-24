import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';

import 'package:tanrim/api/live.dart';

/// The wire format between this app and the environment.
///
/// Every one of these is a key that can be misspelt on one side while both
/// sides keep working well enough that nobody notices.
void main() {
  Live live() => Live(Uri.parse('ws://127.0.0.1:1/ws'));

  String frame(Map<String, dynamic> m) => jsonEncode(m);

  Map<String, dynamic> agent(String id) => {
        'id': id,
        'name': id,
        'color': '#ffffff',
        'home_room': 'r',
        'room_id': 'r',
        'x': 0.0,
        'y': 0.0,
        'target_x': 0.0,
        'target_y': 0.0,
        'status': 'idle',
      };

  test('a retired worker actually leaves the map', () {
    // The server sends `agent_id`; this read `id`, so it removed nothing.
    // The sweep retires an ephemeral worker after every finished record, so
    // the room slowly filled with sprites of agents that were not there —
    // and nothing errored, which is why it survived.
    final l = live();
    l.deliver(frame({'type': 'agent_update', 'agent': agent('forge-2')}));
    expect(l.agents.keys, contains('forge-2'));

    l.deliver(frame({'type': 'agent_removed', 'agent_id': 'forge-2'}));
    expect(l.agents.keys, isNot(contains('forge-2')));
  });

  test('a reload tells every client, not just the one that asked', () async {
    final l = live();
    final seen = <LiveKind>[];
    l.events.listen((e) => seen.add(e.kind));

    l.deliver(frame({'type': 'rooms_changed'}));
    l.deliver(frame({'type': 'plugins_changed'}));
    await Future<void>.delayed(Duration.zero);

    expect(seen, [LiveKind.worldChanged, LiveKind.worldChanged]);
  });

  test('a frame this build has never seen is ignored, not fatal', () async {
    // The environment gains behaviour by gaining plugins, so an unknown type
    // is expected. Throwing here would kill the socket for everything else.
    final l = live();
    final seen = <LiveKind>[];
    l.events.listen((e) => seen.add(e.kind));

    l.deliver(frame({'type': 'something_a_plugin_invented', 'x': 1}));
    l.deliver('not json at all');
    await Future<void>.delayed(Duration.zero);

    expect(seen, isEmpty);
  });
}
