import 'package:flutter_test/flutter_test.dart';

import 'package:tanrim/api/steps.dart';

void main() {
  test('one failing step does not cancel the ones after it', () async {
    // The bug, exactly: six loads in one try, records loaded last, so almost
    // any hiccup left the board empty with nothing on screen to say why.
    final ran = <String>[];
    final failed = await runSteps({
      'plugins': () async => throw StateError('boom'),
      'castles': () async => ran.add('castles'),
      'records': () async => ran.add('records'),
    });

    expect(ran, ['castles', 'records']);
    expect(failed, {'plugins'});
  });

  test('what did not load is NAMED, not shown as empty', () async {
    // An empty list and a list that failed to load render identically, and
    // "no plugins" is a legitimate state here — the documented empty install.
    final failed = await runSteps({
      'plugins': () async => throw StateError('x'),
      'records': () async => throw StateError('y'),
      'castles': () async {},
    });
    expect(failed, {'plugins', 'records'});
  });

  test('everything working reports nothing', () async {
    expect(await runSteps({'a': () async {}, 'b': () async {}}), isEmpty);
  });

  test('a step is retried before it is given up on', () async {
    // The common case is connecting the instant the port answers, while the
    // server is still settling.
    var tries = 0;
    final failed = await runSteps(
      {'records': () async {
        tries++;
        if (tries < 2) throw StateError('not yet');
      }},
      attempts: 2,
      wait: (_) async {},
    );
    expect(tries, 2);
    expect(failed, isEmpty);
  });

  test('a retry that also fails is still reported', () async {
    var tries = 0;
    final failed = await runSteps(
      {'records': () async {
        tries++;
        throw StateError('still no');
      }},
      attempts: 2,
      wait: (_) async {},
    );
    expect(tries, 2);
    expect(failed, {'records'});
  });

  test('the error is handed over, so it can be logged rather than lost',
      () async {
    Object? got;
    await runSteps(
      {'records': () async => throw StateError('the reason')},
      onError: (_, e) => got = e,
    );
    expect('$got', contains('the reason'));
  });
}
