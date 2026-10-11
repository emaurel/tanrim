import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:tanrim/main.dart';
import 'package:tanrim/server/prefs.dart';

/// Enough of an environment for the app to call itself connected.
///
/// Only `/rooms` has to answer — it IS the map, and `_loadAll` stops for
/// nothing else — but the rest are served so the connect does not spend the
/// test's time retrying five loads it does not care about.
Future<HttpServer> _fakeEnvironment() async {
  final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
  unawaited(() async {
    await for (final req in server) {
      final body = switch (req.uri.path) {
        '/rooms' => '[]',
        '/plugins' => '{"plugins": []}',
        '/plugins/catalog' => '{"plugins": []}',
        '/castles' => '{"castles": [], "ring_spacing": 1.6, "plot": 20}',
        '/approvals' => '{"approvals": []}',
        '/leads' => '{"leads": [], "stages": [], "kinds": []}',
        _ => '{}',
      };
      req.response.headers.contentType = ContentType.json;
      req.response.write(body);
      await req.response.close();
    }
  }());
  return server;
}

void main() {
  late Directory dir;
  setUp(() {
    dir = Directory.systemTemp.createTempSync('tanrim_prefs');
    // The test binding installs an HttpOverrides that answers 400 to
    // everything, so an app connecting to a server on loopback would be told
    // the connection failed and never reach the line under test.
    HttpOverrides.global = null;
  });
  tearDown(() => dir.deleteSync(recursive: true));

  File named(String name) => File('${dir.path}/$name');

  test('what is set comes back on the next launch', () {
    Prefs.at(named('a.json')).set('server', 'http://100.82.174.9:8765');
    expect(Prefs.at(named('a.json')).string('server'),
        'http://100.82.174.9:8765');
  });

  test('a corrupt file leaves the app working on its defaults', () {
    named('b.json').writeAsStringSync('{not json at all');
    expect(Prefs.at(named('b.json')).string('server', 'fallback'), 'fallback');
  });

  test('a file in a directory that does not exist yet is still written', () {
    final deep = File('${dir.path}/one/two/app.json');
    Prefs.at(deep).set('server', 'http://x:1');
    expect(jsonDecode(deep.readAsStringSync())['server'], 'http://x:1');
  });

  testWidgets('a connect WRITES the address down', (tester) async {
    // The call-site test, and the whole point of the fix. Prefs round-tripped
    // correctly for as long as it has existed; the address was simply never
    // handed to it on this path. Only `_startServer` saved, which is the
    // local case — an operator pointing the app at a machine across a tailnet
    // never goes through it, so the field was back to the default on every
    // launch.
    //
    // The file is DELETED after seeding, so only a save during the connect
    // can put it back. An earlier version of this test left the seeded file
    // in place and asserted its contents, which passed with the fix removed.
    //
    // Everything touching the socket runs inside `runAsync`: a widget test
    // drives a FAKE clock, and an HttpServer bound under it gets fake timers
    // and never answers.
    late HttpServer server;
    await tester.runAsync(() async => server = await _fakeEnvironment());
    addTearDown(() => server.close(force: true));
    final where = 'http://127.0.0.1:${server.port}';

    final file = named('app.json');
    final prefs = Prefs.at(file)..set('server', where);
    file.deleteSync();

    // The app is STARTED inside `runAsync` as well. Its connect begins in
    // initState, and a request begun under the test's fake clock never
    // completes, however long the test waits afterwards.
    await tester.runAsync(() async {
      await tester.pumpWidget(TanrimApp(prefs: prefs));
      await Future.delayed(const Duration(milliseconds: 800));
    });
    await tester.pump();

    expect(file.existsSync(), isTrue,
        reason: 'the connect never wrote the address down');
    expect(jsonDecode(file.readAsStringSync())['server'], where);
  });

  testWidgets('a connect that never lands keeps the address that works',
      (tester) async {
    // One mistyped octet must not cost you the address that works — which is
    // what saving on every attempt rather than on success would do.
    final file = named('app.json');
    // Port 1 on loopback: nothing is listening, and nothing will be.
    final prefs = Prefs.at(file)..set('server', 'http://127.0.0.1:1');
    file.deleteSync();

    await tester.runAsync(() async {
      await tester.pumpWidget(TanrimApp(prefs: prefs));
      await Future.delayed(const Duration(milliseconds: 400));
    });
    await tester.pump();

    expect(file.existsSync(), isFalse,
        reason: 'an address that never answered was written down anyway');
  });
}
