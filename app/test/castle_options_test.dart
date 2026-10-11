import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:tanrim/api/client.dart';
import 'package:tanrim/model/castle.dart';
import 'package:tanrim/ui/castle_options.dart';

/// What makes one castle of a plugin different from another of the same one.
///
/// The panel ships no fields of its own: the plugin declares them and this
/// draws whatever came back. So the thing worth testing is that every kind in
/// the vocabulary survives the round trip — drawn from the declaration, typed
/// into, and SENT in the shape the plugin said it would be.
class _OptionsApi extends Api {
  _OptionsApi({this.declared, this.stored = const {}, this.refuse = ''})
      : super('http://127.0.0.1:1');

  final List<Map<String, dynamic>>? declared;
  final Map<String, dynamic> stored;
  final String refuse;
  final List<(String, String, Object?)> sent = [];

  @override
  Future<dynamic> get(String path) async => {
        'castle': 'c1',
        'plugin': 'contributor',
        'declared': declared ??
            [
              {'id': 'upstream', 'label': 'Upstream', 'kind': 'text',
               'required': true, 'hint': 'owner/name'},
              {'id': 'slots', 'label': 'Slots', 'kind': 'number',
               'default': 2},
              {'id': 'draft', 'label': 'Draft PRs', 'kind': 'toggle',
               'default': true},
              {'id': 'runner', 'label': 'Runner', 'kind': 'choice',
               'options': ['native', 'wsl']},
              {'id': 'rules', 'label': 'Rules', 'kind': 'list',
               'options': <String>[]},
              {'id': 'platforms', 'label': 'Platforms', 'kind': 'json'},
            ],
        'options': stored,
      };

  @override
  Future<dynamic> send(String method, String path, [Object? payload]) async {
    sent.add((method, path, payload));
    if (refuse.isNotEmpty) return {'ok': false, 'error': refuse};
    return {'ok': true, 'options': (payload as Map)['options']};
  }
}

Castle _castle({int declared = 6}) => Castle(
      id: 'c1',
      pluginId: 'contributor',
      pluginName: 'Contributor',
      name: 'AnyPS5',
      ring: 1,
      slot: 0,
      centre: (0, 0),
      span: 52,
      records: 0,
      installed: true,
      optionsDeclared: declared,
    );

Future<void> _open(WidgetTester tester, Api api) async {
  // A tall surface, because the panel is a ListView: a control below the fold
  // is not BUILT, so a finder for it reports "no such widget" and the test
  // reads as a missing field rather than as an off-screen one.
  tester.view.physicalSize = const Size(1200, 2600);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
      home: Scaffold(body: CastleOptions(api: api, castle: _castle()))));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('the form is the plugin\'s, not this binary\'s', (tester) async {
    await _open(tester, _OptionsApi());
    for (final id in ['upstream', 'slots', 'draft', 'rules', 'platforms']) {
      expect(find.byKey(ValueKey('option-$id')), findsOneWidget,
          reason: '$id was declared and not drawn');
    }
    // A `choice` is chips rather than one keyed control, and a `list` with no
    // options is a text box — the two degradations worth seeing drawn.
    expect(find.widgetWithText(ChoiceChip, 'native'), findsOneWidget);
    expect(find.widgetWithText(ChoiceChip, 'wsl'), findsOneWidget);
  });

  testWidgets('what is already stored is what you edit', (tester) async {
    await _open(
        tester,
        _OptionsApi(stored: {
          'upstream': 'emaurel/thing',
          'draft': false,
          'platforms': [
            {'name': 'windows'}
          ],
        }));
    expect(find.text('emaurel/thing'), findsOneWidget);
    expect(tester.widget<Switch>(find.byKey(const ValueKey('option-draft')))
        .value, isFalse);
    // A `json` option is stored as the value it describes, so it has to be
    // printed back out to be edited — and indented, because a platform list
    // on one line is not something anyone edits twice.
    expect(find.textContaining('"name": "windows"'), findsOneWidget);
  });

  testWidgets('Save PUTs what was typed, in the declared shapes',
      (tester) async {
    // The call-site test. Every kind in the vocabulary at once, because the
    // shapes are where this goes wrong: a free `list` is typed
    // comma-separated and must still ARRIVE as a list, and a `json` field is
    // typed as text the server parses.
    final api = _OptionsApi();
    await _open(tester, api);

    await tester.enterText(
        find.byKey(const ValueKey('option-upstream')), 'emaurel/thing');
    await tester.enterText(find.byKey(const ValueKey('option-slots')), '4');
    await tester.tap(find.byKey(const ValueKey('option-draft')));
    await tester.tap(find.widgetWithText(ChoiceChip, 'wsl'));
    await tester.enterText(
        find.byKey(const ValueKey('option-rules')), 'CONTRIBUTING.md, STYLE.md');
    await tester.enterText(
        find.byKey(const ValueKey('option-platforms')), '[{"name": "win"}]');
    await tester.pumpAndSettle();

    await tester.tap(find.widgetWithText(FilledButton, 'Save'));
    await tester.pumpAndSettle();

    expect(api.sent, hasLength(1));
    final (method, path, payload) = api.sent.single;
    expect(method, 'PUT');
    expect(path, '/castles/c1/options');
    final options = (payload as Map)['options'] as Map;
    expect(options['upstream'], 'emaurel/thing');
    expect(options['slots'], '4');
    expect(options['draft'], false);
    expect(options['runner'], 'wsl');
    expect(options['rules'], ['CONTRIBUTING.md', 'STYLE.md']);
    expect(options['platforms'], '[{"name": "win"}]');
    expect(find.text('saved'), findsOneWidget);
  });

  testWidgets('a field left blank is not sent at all', (tester) async {
    // Blank and unanswered are the same thing — both fall back to the
    // plugin's default — so only one of them is ever on the wire.
    final api = _OptionsApi(stored: {'upstream': 'a/b'});
    await _open(tester, api);
    await tester.enterText(find.byKey(const ValueKey('option-upstream')), '');
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, 'Save'));
    await tester.pumpAndSettle();
    expect(((api.sent.single.$3 as Map)['options'] as Map)
        .containsKey('upstream'), isFalse);
  });

  testWidgets('a refusal is shown, not swallowed', (tester) async {
    // Half the refusals in this environment are deliberate, and every one of
    // them was invisible until a panel read it.
    await _open(tester, _OptionsApi(refuse: "'upstream' is required"));
    await tester.tap(find.widgetWithText(FilledButton, 'Save'));
    await tester.pumpAndSettle();
    expect(find.textContaining('is required'), findsOneWidget);
    expect(find.text('saved'), findsNothing);
  });

  testWidgets('a plugin with nothing to set says so plainly', (tester) async {
    await _open(tester, _OptionsApi(declared: const []));
    expect(find.textContaining('nothing that differs'), findsOneWidget);
  });
}
