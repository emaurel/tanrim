import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:tanrim/api/client.dart';
import 'package:tanrim/api/live.dart';
import 'package:tanrim/ui/record_window.dart';

class _Api extends Api {
  _Api({this.lines = const []}) : super('http://127.0.0.1:1');
  final List<Map<String, dynamic>> lines;

  @override
  Future<dynamic> get(String path) async {
    if (path.endsWith('/run')) {
      return {
        'running': lines.isNotEmpty,
        'workers': [
          {'id': 'forge@c1', 'workbench': 'floor', 'role': 'forge'}
        ],
        'lines': lines,
      };
    }
    return {
      'name': 'fmh piscines',
      'stage': 'visualised',
      'blocks': [
        {'block': 'section', 'title': 'Brief'},
        {'block': 'timeline', 'steps': []},
      ],
    };
  }

  @override
  Future<dynamic> post(String path, [Object? p]) async => {'ok': true};
}

Future<void> _pump(WidgetTester t, Widget w) async {
  await t.pumpWidget(MaterialApp(theme: ThemeData.dark(), home: Scaffold(body: w)));
  await t.pumpAndSettle();
}

void main() {
  testWidgets('a record being worked opens on the run', (t) async {
    // You opened it BECAUSE something is happening to it.
    await _pump(
      t,
      RecordWindow(
        api: _Api(lines: [
          {'ts': 1, 'kind': 'text', 'text': 'reading their site'},
        ]),
        recordId: 'r1',
        working: true,
      ),
    );
    expect(find.text('Current run'), findsOneWidget);
    expect(find.text('reading their site'), findsOneWidget);
  });

  testWidgets('an idle record has no run tab at all', (t) async {
    // A tab that is empty most of the time trains you to skip it; the whole
    // value of this one is that its presence means work is happening.
    await _pump(t, RecordWindow(api: _Api(), recordId: 'r1', working: false));
    expect(find.text('Current run'), findsNothing);
    expect(find.text('Details'), findsOneWidget);
  });

  testWidgets('lines arrive as they are said', (t) async {
    final live = StreamController<LiveEvent>.broadcast();
    addTearDown(live.close);
    await _pump(
      t,
      RecordWindow(
        api: _Api(lines: [
          {'ts': 1, 'kind': 'text', 'text': 'starting'},
        ]),
        recordId: 'r1',
        working: true,
        live: live.stream,
      ),
    );
    expect(find.text('Write(file_path=index.html)'), findsNothing);

    live.add(const LiveEvent.runLine('r1', {
      'ts': 2, 'kind': 'tool', 'text': 'Write(file_path=index.html)',
    }));
    await t.pumpAndSettle();

    expect(find.text('Write(file_path=index.html)'), findsOneWidget);
  });

  testWidgets('another record\'s lines are ignored', (t) async {
    final live = StreamController<LiveEvent>.broadcast();
    addTearDown(live.close);
    await _pump(
      t,
      RecordWindow(
        api: _Api(lines: [{'ts': 1, 'kind': 'text', 'text': 'mine'}]),
        recordId: 'r1',
        working: true,
        live: live.stream,
      ),
    );

    live.add(const LiveEvent.runLine('r2', {
      'ts': 2, 'kind': 'text', 'text': 'someone else',
    }));
    await t.pumpAndSettle();

    expect(find.text('someone else'), findsNothing);
    expect(find.text('mine'), findsOneWidget);
  });

  testWidgets('a run with nothing said yet says so', (t) async {
    await _pump(t, RecordWindow(api: _Api(), recordId: 'r1', working: true));
    expect(find.textContaining('nothing said yet'), findsOneWidget);
  });
}
