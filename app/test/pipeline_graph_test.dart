import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:tanrim/api/client.dart';
import 'package:tanrim/ui/pipeline_graph.dart';

/// The real shape of `/pipeline`, including the two things that make it a
/// graph rather than a list: a stage worked by two roles, and a rejection that
/// sends a record BACK.
class _Api extends Api {
  _Api({this.kinds = const ['application']}) : super('http://127.0.0.1:1');
  final List<String> kinds;
  final List<String> asked = [];

  @override
  Future<dynamic> get(String path) async {
    asked.add(path);
    return {
      'kinds': kinds,
      'stages': ['spotted', 'screened', 'written', 'prepared', 'submitted'],
      'dead_stages': ['passed_over', 'closed'],
      'steps': [
        {'stage': 'spotted', 'role': 'screener', 'room_name': 'Screening',
         'waiting': 0, 'gated': false,
         'outcomes': [{'to': 'screened', 'kind': 'forward'},
                      {'to': 'passed_over', 'kind': 'reject'}]},
        {'stage': 'screened', 'role': 'penman', 'room_name': 'Writing',
         'waiting': 2, 'gated': false,
         'outcomes': [{'to': 'written', 'kind': 'forward'}]},
        {'stage': 'written', 'role': 'filler', 'room_name': 'Forms',
         'waiting': 4, 'gated': false,
         'outcomes': [{'to': 'prepared', 'kind': 'forward'}]},
        // Two roles at one stage, and the second edge goes BACKWARDS.
        {'stage': 'prepared', 'role': 'postman', 'room_name': 'Post',
         'waiting': 10, 'gated': true, 'permanent': true,
         'outcomes': [{'to': 'submitted', 'kind': 'forward'}]},
        {'stage': 'prepared', 'role': 'operator', 'room_name': 'Post',
         'waiting': 10, 'gated': true,
         'outcomes': [{'to': 'screened', 'kind': 'reject'}]},
        {'stage': 'submitted', 'role': 'operator', 'room_name': '',
         'waiting': 0, 'gated': false,
         'outcomes': [{'to': 'closed', 'kind': 'forward'}]},
      ],
    };
  }
}

Future<void> _pump(WidgetTester t, Widget w) async {
  await t.pumpWidget(MaterialApp(theme: ThemeData.dark(),
      home: Scaffold(body: SizedBox(width: 700, height: 900, child: w))));
  for (var i = 0; i < 8; i++) {
    await t.pump(const Duration(milliseconds: 40));
  }
}

void main() {
  testWidgets('it draws, from the server rather than from a hand-drawn map',
      (t) async {
    // `docs/pipeline.html` was a diagram somebody maintained, and it went
    // stale the week a stage was added. A generated one cannot.
    await _pump(t, PipelineGraph(api: _Api(), castleId: 'c1'));
    expect(find.byType(CustomPaint), findsWidgets);
  });

  testWidgets('it asks for the castle and adopts a kind', (t) async {
    final api = _Api(kinds: const ['application']);
    await _pump(t, PipelineGraph(api: api, castleId: 'c1'));
    expect(api.asked.first, contains('castle_id=c1'));
    expect(api.asked.last, contains('kind=application'),
        reason: 'it never re-asked, so the graph is every pipeline at once');
  });

  testWidgets('one kind offers no picker', (t) async {
    await _pump(t, PipelineGraph(api: _Api(), castleId: 'c1'));
    expect(find.byType(ChoiceChip), findsNothing);
  });

  testWidgets('several kinds do', (t) async {
    await _pump(t, PipelineGraph(
        api: _Api(kinds: const ['application', 'port']), castleId: 'c1'));
    expect(find.byType(ChoiceChip), findsNWidgets(2));
  });

  testWidgets('a pipeline with no steps says so rather than drawing nothing',
      (t) async {
    await _pump(t, PipelineGraph(api: _Empty(), castleId: 'c1'));
    expect(find.textContaining('no steps'), findsOneWidget);
  });

  testWidgets('a failure is shown, not swallowed', (t) async {
    await _pump(t, PipelineGraph(api: _Broken(), castleId: 'c1'));
    expect(find.textContaining('nope'), findsOneWidget);
  });
}

class _Empty extends Api {
  _Empty() : super('http://127.0.0.1:1');
  @override
  Future<dynamic> get(String path) async =>
      {'kinds': <String>[], 'stages': <String>[], 'dead_stages': <String>[],
       'steps': <dynamic>[]};
}

class _Broken extends Api {
  _Broken() : super('http://127.0.0.1:1');
  @override
  Future<dynamic> get(String path) async => throw ApiError('/pipeline', 500,
      '{"detail": "nope"}');
}
