import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:tanrim/model/castle.dart';
import 'package:tanrim/ui/kingdom.dart';

Castle _castle(String id, String name,
        {String status = 'idle', int waiting = 0, int records = 0}) =>
    Castle.fromJson({
      'id': id,
      'plugin': 'web_agency',
      'plugin_name': 'Web agency',
      'name': name,
      'ring': 1,
      'slot': 0,
      'x': 0,
      'y': 0,
      'span': 52,
      'records': records,
      'installed': true,
      'status': status,
      'waiting': waiting,
    });

Future<List<Castle>> _kingdom(WidgetTester t, List<Castle> castles,
    {List<Castle>? opened}) async {
  t.view
    ..physicalSize = const Size(460, 700)
    ..devicePixelRatio = 1.0;
  addTearDown(t.view.reset);

  await t.pumpWidget(MaterialApp(
    home: Scaffold(
      body: Kingdom(
        castles: castles,
        badges: {for (final c in castles) c.id: c.waiting},
        onOpen: (c) => opened?.add(c),
      ),
    ),
  ));
  await t.pumpAndSettle();
  return castles;
}

void main() {
  testWidgets('it lists castles grouped by what they are instances of',
      (t) async {
    await _kingdom(t, [_castle('a', 'Web agency 1'), _castle('b', 'Nimes')]);
    expect(find.text('WEB AGENCY'), findsOneWidget);
    expect(find.text('Web agency 1'), findsOneWidget);
    expect(find.text('Nimes'), findsOneWidget);
  });

  testWidgets('a working castle is marked, an idle one is not', (t) async {
    await _kingdom(t, [
      _castle('a', 'Busy', status: 'working', records: 3),
      _castle('b', 'Quiet', records: 3),
    ]);
    // The status reads without being read: a dot, and the word on the busy one.
    expect(find.textContaining('working'), findsOneWidget);
    expect(find.text('3 rooms · 3 records'), findsNothing);
  });

  testWidgets('what is waiting sits beside the name', (t) async {
    // Not at the far end of the row, where you do not look.
    await _kingdom(t, [_castle('a', 'Web agency 1', waiting: 4)]);
    final name = t.getRect(find.text('Web agency 1'));
    final badge = t.getRect(find.text('4'));
    expect(badge.left - name.right, lessThan(30),
        reason: 'the count should be next to the name it belongs to');
  });

  testWidgets('a castle with nothing waiting shows no badge', (t) async {
    await _kingdom(t, [_castle('a', 'Web agency 1')]);
    expect(find.text('0'), findsNothing);
  });

  testWidgets('there is no build button; building is a plot click', (t) async {
    await _kingdom(t, [_castle('a', 'Web agency 1')]);
    expect(find.text('Build a castle'), findsNothing);
    expect(find.textContaining('outlined plot'), findsOneWidget);
  });

  testWidgets('clicking a castle opens it', (t) async {
    final opened = <Castle>[];
    await _kingdom(t, [_castle('a', 'Web agency 1')], opened: opened);
    await t.tap(find.text('Web agency 1'));
    await t.pumpAndSettle();
    expect(opened.single.id, 'a');
  });

  testWidgets('an empty kingdom says how to start one', (t) async {
    await _kingdom(t, const []);
    expect(find.textContaining('No castles yet'), findsOneWidget);
  });
}
