import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:tanrim/api/client.dart';
import 'package:tanrim/ui/start_form.dart';

/// Answers `/starts` with one of each control, so the vocabulary is exercised
/// rather than just the case the three installed plugins happen to use.
class _StartsApi extends Api {
  _StartsApi({this.refuse = false}) : super('http://127.0.0.1:1');
  final bool refuse;
  final List<(String, Object?)> posted = [];

  @override
  Future<dynamic> get(String path) async => {
        'starts': [
          {
            'id': 'sweep',
            'label': 'Sweep the boards',
            'kind': 'application',
            'note': 'reads every configured board',
            'room': 'board@c1',
            'inputs': [
              {'id': 'only', 'label': 'Only these', 'kind': 'list',
               'options': ['remoteok', 'lever'], 'required': false,
               'hint': 'blank sweeps all of them'},
              {'id': 'where', 'label': 'Where', 'kind': 'text',
               'required': true, 'hint': 'a place'},
              {'id': 'deep', 'label': 'Go deep', 'kind': 'toggle',
               'required': false},
              {'id': 'mode', 'label': 'Mode', 'kind': 'choice',
               'options': ['fast', 'thorough'], 'required': false},
              {'id': 'tags', 'label': 'Tags', 'kind': 'list',
               'options': <String>[], 'required': false},
              {'id': 'weird', 'label': 'From the future', 'kind': 'hologram',
               'required': false},
            ],
          },
          {
            'id': 'other',
            'label': 'Commission',
            'kind': 'port',
            'note': '',
            'room': '',
            'inputs': <Map<String, dynamic>>[],
          },
        ],
      };

  @override
  Future<dynamic> post(String path, [Object? payload]) async {
    posted.add((path, payload));
    return refuse ? {'ok': false, 'error': 'tell Nova where to look'}
                  : {'ok': true};
  }
}

Future<void> _pump(WidgetTester t, Widget child) async {
  await t.pumpWidget(MaterialApp(
    theme: ThemeData.dark(),
    home: Scaffold(
        body: SingleChildScrollView(child: SizedBox(width: 420, child: child))),
  ));
  await t.pumpAndSettle();
}

void main() {
  testWidgets('a start draws the form its plugin declared', (t) async {
    final api = _StartsApi();
    await _pump(t, StartForms(api: api, castleId: 'c1'));

    expect(find.text('Sweep the boards'), findsWidgets);
    expect(find.text('reads every configured board'), findsOneWidget);
    // One control per declared input kind.
    expect(find.widgetWithText(FilterChip, 'remoteok'), findsOneWidget);
    expect(find.widgetWithText(ChoiceChip, 'fast'), findsOneWidget);
    expect(find.byType(Switch), findsOneWidget);
  });

  testWidgets('a control this binary has never heard of becomes a text box',
      (t) async {
    // The environment gains behaviour by gaining plugins, and the app ships on
    // its own schedule — a plugin built against a newer vocabulary must still
    // be startable. Degrading to something you can type in always leaves it so.
    final api = _StartsApi();
    await _pump(t, StartForms(api: api, castleId: 'c1'));

    expect(find.text('From the future'), findsOneWidget);
    expect(find.byKey(const ValueKey('start-weird')), findsOneWidget);
  });

  testWidgets('a free list is SENT as a list, not as the string typed',
      (t) async {
    // The plugin declared the kind; sending a bare string would make the wire
    // disagree with the declaration for exactly the fields nobody tested.
    final api = _StartsApi();
    await _pump(t, StartForms(api: api, castleId: 'c1'));

    await t.enterText(find.byKey(const ValueKey('start-where')), 'Lyon');
    await t.enterText(find.byKey(const ValueKey('start-tags')), 'one, two ,three');
    await t.tap(find.widgetWithText(FilledButton, 'Sweep the boards'));
    await t.pumpAndSettle();

    final (path, body) = api.posted.single;
    expect(path, '/starts/sweep');
    final values = (body as Map)['values'] as Map;
    expect(values['tags'], ['one', 'two', 'three']);
    expect(values['where'], 'Lyon');
  });

  testWidgets('picking from a closed set sends the ids, not the labels',
      (t) async {
    final api = _StartsApi();
    await _pump(t, StartForms(api: api, castleId: 'c1'));

    await t.enterText(find.byKey(const ValueKey('start-where')), 'Lyon');
    await t.tap(find.widgetWithText(FilterChip, 'remoteok'));
    await t.tap(find.widgetWithText(ChoiceChip, 'thorough'));
    await t.pumpAndSettle();
    await t.tap(find.widgetWithText(FilledButton, 'Sweep the boards'));
    await t.pumpAndSettle();

    final values = (api.posted.single.$2 as Map)['values'] as Map;
    expect(values['only'], ['remoteok']);
    expect(values['mode'], 'thorough');
    expect(values['castle_id'] ?? (api.posted.single.$2 as Map)['castle_id'],
        'c1');
  });

  testWidgets('a refusal is shown, not swallowed', (t) async {
    // A start that declines answers `ok: false` and a sentence rather than
    // throwing. Half the refusals here are deliberate and every one of them
    // was invisible until a panel read it.
    final api = _StartsApi(refuse: true);
    await _pump(t, StartForms(api: api, castleId: 'c1'));

    await t.enterText(find.byKey(const ValueKey('start-where')), 'Lyon');
    await t.tap(find.widgetWithText(FilledButton, 'Sweep the boards'));
    await t.pumpAndSettle();

    expect(find.text('tell Nova where to look'), findsOneWidget);
  });

  testWidgets('a room shows only the starts that run in it', (t) async {
    final api = _StartsApi();
    await _pump(t, StartForms(api: api, castleId: 'c1', only: 'board@c1'));

    expect(find.text('Sweep the boards'), findsWidgets);
    expect(find.text('Commission'), findsNothing);
  });

  testWidgets('a castle that opens no work says so plainly', (t) async {
    await _pump(t, StartForms(api: _EmptyApi(), castleId: 'c1'));
    expect(find.textContaining('Nothing here opens work'), findsOneWidget);
  });
}

class _EmptyApi extends Api {
  _EmptyApi() : super('http://127.0.0.1:1');
  @override
  Future<dynamic> get(String path) async => {'starts': <dynamic>[]};
}
