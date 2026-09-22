@Tags(['live'])
library;

import 'package:flutter_test/flutter_test.dart';

import 'package:tanrim/api/client.dart';
import 'package:tanrim/api/live.dart';
import 'package:tanrim/model/world.dart';

/// Against a REAL running environment.
///
/// Skipped when nothing is listening, so the suite stays green on a machine
/// with no server — but when one IS up this is the only thing that proves the
/// client speaks what the environment actually serves rather than what the
/// models hope it does.
void main() {
  const base = 'http://127.0.0.1:8765';

  test('rooms parse from the live server', () async {
    final api = Api(base);
    late final List<dynamic> raw;
    try {
      raw = await api.get('/rooms') as List;
    } catch (_) {
      markTestSkipped('no environment on $base');
      api.close();
      return;
    }

    final rooms =
        raw.map((r) => Room.fromJson(r as Map<String, dynamic>)).toList();
    expect(rooms, isNotEmpty);
    for (final r in rooms) {
      expect(r.id, isNotEmpty);
      expect(r.size.x, greaterThan(0));
      expect(r.size.y, greaterThan(0));
      // Every room the environment serves must be drawable: a colour that
      // will not parse is the one thing that would make it invisible.
      expect(r.color, isNot(0));
    }
    api.close();
  }, timeout: const Timeout(Duration(seconds: 20)));

  test('the live socket delivers a snapshot', () async {
    final api = Api(base);
    try {
      await api.get('/rooms');
    } catch (_) {
      markTestSkipped('no environment on $base');
      api.close();
      return;
    }

    final live = Live(api.socket);
    final seen = <LiveKind>[];
    final sub = live.events.listen(seen.add);
    await live.connect();
    // The environment sends a snapshot on connect; give it a moment.
    await Future<void>.delayed(const Duration(seconds: 3));
    await sub.cancel();
    await live.close();
    api.close();

    expect(seen, contains(LiveKind.connected));
    expect(live.agents, isNotEmpty,
        reason: 'the snapshot should have carried the crew');
  }, timeout: const Timeout(Duration(seconds: 30)));
}
