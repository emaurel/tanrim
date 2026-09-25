@Tags(['live'])
library;

import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

import 'package:tanrim/api/client.dart';
import 'package:tanrim/server/process.dart';

/// Starting and stopping a REAL server.
///
/// Tagged `live` and skipped when the checkout has no virtualenv, because the
/// alternative is a unit test of `Process.start` that proves nothing: the
/// failures worth catching here are a wrong PYTHONPATH, a wrong interpreter
/// and a server that boots but never answers, and none of them are visible
/// without actually running one.
void main() {
  // Not 8765: the operator's own server usually has that, and a test that
  // fights it would either fail or, worse, adopt it and kill it.
  const port = 8791;

  test('it starts a server, connects, and stops it again', () async {
    final repo = ServerProcess.guess();
    final p = ServerProcess(repo: repo, port: port);
    if (p.checkRepo() != null) {
      markTestSkipped('no usable checkout: ${p.checkRepo()}');
      return;
    }
    if (await p.reachable()) {
      markTestSkipped('something is already on $port');
      return;
    }

    final problem = await p.start();
    expect(problem, isNull, reason: p.log.join('\n'));
    expect(p.running, isTrue);
    expect(p.adopted, isFalse, reason: 'it started this one itself');

    // It is a real environment, not just an open port.
    final api = Api(p.base);
    final rooms = await api.get('/rooms') as List;
    expect(rooms, isNotEmpty);
    api.close();

    expect(await p.stop(), isNull);
    expect(p.running, isFalse);
    expect(await p.reachable(), isFalse, reason: 'the port should be free');
  }, timeout: const Timeout(Duration(seconds: 90)));

  test('a checkout with no virtualenv says so instead of failing oddly',
      () async {
    final p = ServerProcess(repo: Directory.systemTemp.path, port: port);
    final problem = p.checkRepo();
    expect(problem, isNotNull);
    expect(problem, contains('backend/tanrim'));
    // And starting is refused with the same sentence rather than an exception.
    expect(await p.start(), problem);
  });

  test('it will not stop a server it did not start', () async {
    final p = ServerProcess(repo: ServerProcess.guess(), port: port);
    expect(p.running, isFalse);
    expect(await p.stop(), contains('not running'));
  });
}
