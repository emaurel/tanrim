import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:tanrim/api/client.dart';
import 'package:tanrim/ui/record_window.dart';

class _FakeApi extends Api {
  _FakeApi(this.view) : super('http://127.0.0.1:1');
  final Map<String, dynamic> view;

  @override
  Future<dynamic> get(String path) async => view;
}

Map<String, dynamic> _view({List<Map<String, dynamic>>? blocks}) => {
      'id': 'rec-1',
      'name': 'Piscines Bellerive',
      'kind': 'port',
      'stage': 'built',
      'blocks': blocks ??
          [
            {
              'block': 'fields',
              'title': 'Contact',
              'rows': [
                {'label': 'Phone', 'value': '06 03 36 64 05'},
              ],
            },
            {
              'block': 'timeline',
              'title': 'History',
              'steps': [
                {
                  'ts': 1790000000.0,
                  'from_stage': 'intake',
                  'stage': 'surveyed',
                  'agent': 'probe',
                  'room': 'assay@c1',
                  'room_name': 'Assay Room',
                  'note': 'read their site',
                  'wrote': ['profile'],
                  'by_hand': false,
                },
              ],
            },
          ],
    };

Future<void> _open(WidgetTester t, {List<Map<String, dynamic>>? blocks}) async {
  t.view
    ..physicalSize = const Size(560, 860)
    ..devicePixelRatio = 1.0;
  addTearDown(t.view.reset);

  await t.pumpWidget(MaterialApp(
    home: Scaffold(
      body: RecordWindow(
        api: _FakeApi(_view(blocks: blocks)),
        recordId: 'rec-1',
      ),
    ),
  ));
  await t.pumpAndSettle();
}

void main() {
  testWidgets('it says what kind of work this is and where it has got to',
      (t) async {
    // Shown by the window rather than left to the plugin: every record has a
    // kind and a stage whatever else it has, and a view that could omit them
    // would be a record you cannot place.
    await _open(t);
    expect(find.text('port'), findsOneWidget);
    expect(find.text('built'), findsOneWidget);
  });

  testWidgets('the history is its own tab, not the last card', (t) async {
    // It is the one part of a record that always exists and always grows, so
    // in line it becomes the thing you scroll past to reach anything else.
    await _open(t);

    expect(find.text('Details'), findsOneWidget);
    expect(find.text('History (1)'), findsOneWidget);

    // Details first, and the history is not in it.
    expect(find.text('CONTACT'), findsOneWidget);
    expect(find.text('intake → surveyed'), findsNothing);
    // Cards start closed, so the heading is what Details shows.

    await t.tap(find.text('History (1)'));
    await t.pumpAndSettle();
    await t.tap(find.text('HISTORY'));
    await t.pumpAndSettle();
    expect(find.text('intake → surveyed'), findsOneWidget);
    expect(find.text('CONTACT'), findsNothing);
  });

  testWidgets('a record with nothing but a history says so', (t) async {
    await _open(t, blocks: [
      {'block': 'timeline', 'title': 'History', 'steps': const []},
    ]);
    expect(find.text('nothing recorded yet'), findsOneWidget);
    expect(find.text('History (0)'), findsOneWidget);
  });

  testWidgets('the newest step is at the top', (t) async {
    // The server reverses the ledger, which appends. What just happened is
    // what you opened the record to find out.
    await _open(t, blocks: [
      {
        'block': 'timeline',
        'title': 'History',
        'steps': [
          {'ts': 3.0, 'from_stage': 'visualised', 'stage': 'built',
           'agent': 'forge', 'room': '', 'room_name': '', 'note': '',
           'wrote': null, 'by_hand': false},
          {'ts': 1.0, 'from_stage': 'intake', 'stage': 'surveyed',
           'agent': 'probe', 'room': '', 'room_name': '', 'note': '',
           'wrote': null, 'by_hand': false},
        ],
      },
    ]);
    await t.tap(find.text('History (2)'));
    await t.pumpAndSettle();
    await t.tap(find.text('HISTORY'));
    await t.pumpAndSettle();

    final newest = t.getTopLeft(find.text('visualised → built'));
    final oldest = t.getTopLeft(find.text('intake → surveyed'));
    expect(newest.dy, lessThan(oldest.dy));
  });

  testWidgets('a long record does not build every row to open', (t) async {
    // Six hundred rows and cells were constructed in one frame when a record
    // opened, which is what the stall was. Only the blocks on screen are
    // built now.
    final many = [
      for (var i = 0; i < 60; i++)
        {
          'block': 'fields',
          'title': 'Block $i',
          'rows': [
            for (var j = 0; j < 10; j++)
              {'label': 'row $j', 'value': 'value $i-$j'},
          ],
        },
    ];
    await _open(t, blocks: many);

    expect(find.text('BLOCK 0'), findsOneWidget);
    // The far end of the list is not built until it is scrolled to.
    expect(find.text('BLOCK 59'), findsNothing);
  });
}
