import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:tanrim/ui/blocks.dart';

Future<void> _draw(WidgetTester t, List<Map<String, dynamic>> blocks,
    {void Function(String)? onOpenRoom}) async {
  t.view
    ..physicalSize = const Size(560, 900)
    ..devicePixelRatio = 1.0;
  addTearDown(t.view.reset);

  await t.pumpWidget(MaterialApp(
    home: Scaffold(
      body: SingleChildScrollView(
        child: Blocks(blocks: blocks, onOpenRoom: onOpenRoom),
      ),
    ),
  ));
  await t.pumpAndSettle();
}

void main() {
  testWidgets('a block this build has never seen is drawn, not dropped',
      (t) async {
    // The same rule the live socket follows for an unknown frame. Nothing a
    // plugin sends should be able to make part of a record invisible.
    await _draw(t, [
      {'block': 'something_invented_later', 'title': 'New', 'value': {'a': 1}},
    ]);
    expect(find.text('NEW'), findsOneWidget);
    expect(find.textContaining('"a": 1'), findsOneWidget);
  });

  testWidgets('a cited fact is marked and an uncited one is not', (t) async {
    // The dossier's rule — every fact carries a source URL, anything uncited
    // goes in `unverified` — made visible for any plugin.
    await _draw(t, [
      {
        'block': 'fields',
        'rows': [
          {'label': 'Phone', 'value': '06', 'source': 'https://x.fr/'},
          {'label': 'Hours', 'value': '9-5'},
        ],
      }
    ]);
    expect(find.byIcon(Icons.link), findsOneWidget);
  });

  testWidgets('a contested fact says so', (t) async {
    await _draw(t, [
      {
        'block': 'fields',
        'rows': [
          {'label': 'Closes', 'value': '17:00', 'conflict': true},
        ],
      }
    ]);
    expect(find.byIcon(Icons.call_split), findsOneWidget);
  });

  testWidgets('formats are read, not guessed at draw time', (t) async {
    await _draw(t, [
      {
        'block': 'fields',
        'rows': [
          {'label': 'Cost', 'value': 3.2, 'format': 'money'},
          {'label': 'Weight', 'value': 2048, 'format': 'bytes'},
          {'label': 'Accent', 'value': '#C0352A', 'format': 'colour'},
        ],
      }
    ]);
    expect(find.text('\$3.20'), findsOneWidget);
    expect(find.text('2.0 MB'), findsOneWidget);
    expect(find.text('#C0352A'), findsOneWidget);
  });

  testWidgets('a table draws its columns and a missing cell reads as missing',
      (t) async {
    await _draw(t, [
      {
        'block': 'table',
        'title': 'Offering',
        'columns': ['Name', 'Price'],
        'rows': [
          {'cells': ['Coque polyester', null]},
          {'cells': ['Entretien', '45€'], 'source': 'https://x.fr/'},
        ],
      }
    ]);
    expect(find.text('NAME'), findsOneWidget);
    expect(find.text('Coque polyester'), findsOneWidget);
    expect(find.text('—'), findsOneWidget);
    expect(find.byIcon(Icons.link), findsOneWidget);
  });

  testWidgets('a section nests its children', (t) async {
    await _draw(t, [
      {
        'block': 'section',
        'title': 'Profile',
        'note': 'what the build may use',
        'children': [
          {'block': 'text', 'body': 'a family firm', 'tone': 'normal'},
        ],
      }
    ]);
    expect(find.text('Profile'), findsOneWidget);
    expect(find.text('what the build may use'), findsOneWidget);
    expect(find.text('a family firm'), findsOneWidget);
  });

  group('the timeline', () {
    Map<String, dynamic> step(Map<String, dynamic> over) => {
          'ts': 1790000000.0,
          'from_stage': 'intake',
          'stage': 'surveyed',
          'agent': 'probe',
          'room': 'assay@c1',
          'room_name': 'Assay Room',
          'note': 'read their site',
          'wrote': null,
          'by_hand': false,
          ...over,
        };

    testWidgets('it shows the move, who made it and where', (t) async {
      await _draw(t, [
        {'block': 'timeline', 'title': 'History', 'steps': [step(const {})]}
      ]);
      expect(find.text('intake → surveyed'), findsOneWidget);
      expect(find.text('probe'), findsOneWidget);
      expect(find.text('· Assay Room'), findsOneWidget);
      expect(find.text('read their site'), findsOneWidget);
    });

    testWidgets('what a step produced, when the ledger recorded it',
        (t) async {
      await _draw(t, [
        {
          'block': 'timeline',
          'steps': [step({'wrote': ['profile', 'visual']})]
        }
      ]);
      expect(find.text('profile'), findsOneWidget);
      expect(find.text('visual'), findsOneWidget);
    });

    testWidgets('a step from before that was recorded claims nothing',
        (t) async {
      // 670 transitions predate the field. Leaving the line out is honest;
      // drawing an empty one would say the step produced nothing.
      await _draw(t, [
        {'block': 'timeline', 'steps': [step(const {'wrote': null})]}
      ]);
      expect(find.text('profile'), findsNothing);
    });

    testWidgets('an operator hand-move is marked as one', (t) async {
      await _draw(t, [
        {'block': 'timeline', 'steps': [step(const {'by_hand': true})]}
      ]);
      expect(find.byIcon(Icons.pan_tool_alt_outlined), findsOneWidget);
    });

    testWidgets('the room opens from the step', (t) async {
      final opened = <String>[];
      await _draw(t, [
        {'block': 'timeline', 'steps': [step(const {})]}
      ], onOpenRoom: opened.add);
      await t.tap(find.text('· Assay Room'));
      await t.pumpAndSettle();
      expect(opened, ['assay@c1']);
    });
  });
}
