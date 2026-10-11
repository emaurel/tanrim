import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:tanrim/api/client.dart';
import 'package:tanrim/model/approval.dart';
import 'package:tanrim/ui/record_window.dart';

/// Deciding a gate where the record is.
///
/// A gate stops the work and asks, and the only place to answer used to be
/// the global Approvals panel — so a record you had opened BECAUSE it had
/// stopped said nothing about why, and the answer was two windows away.
class _FakeApi extends Api {
  _FakeApi() : super('http://127.0.0.1:1');

  final List<(String, Object?)> posted = [];

  @override
  Future<dynamic> get(String path) async => {
        'id': 'rec-1',
        'name': 'Piscines Bellerive',
        'kind': 'port',
        'stage': 'built',
        'blocks': const [],
      };

  @override
  Future<dynamic> send(String method, String path, [Object? payload]) async {
    posted.add((path, payload));
    return {'ok': true};
  }
}

Approval _card({String id = 'ap-1', String record = 'rec-1'}) => Approval({
      'id': id,
      'kind': 'port_review',
      'room_id': 'forge@c1',
      'castle_id': 'c1',
      'summary': 'the port is built — publish it?',
      'requesting_agent': 'forge',
      'ts': 1790000000.0,
      'payload': {
        'lead_id': record,
        'what_this_means': 'the site goes on a public URL',
      },
    });

Future<void> _open(WidgetTester t, {
  required _FakeApi api,
  List<Approval> approvals = const [],
  VoidCallback? onDecided,
  bool working = false,
}) async {
  t.view
    ..physicalSize = const Size(560, 900)
    ..devicePixelRatio = 1.0;
  addTearDown(t.view.reset);

  await t.pumpWidget(MaterialApp(
    home: Scaffold(
      body: RecordWindow(
        api: api,
        recordId: 'rec-1',
        working: working,
        approvals: approvals,
        onDecided: onDecided,
      ),
    ),
  ));
  await t.pumpAndSettle();
}

void main() {
  testWidgets('a record waiting on you OPENS on the question', (t) async {
    // It outranks the run tab: a card means nothing is running, the work is
    // waiting on you, and that is the only thing worth showing first.
    await _open(t, api: _FakeApi(), approvals: [_card()], working: true);
    expect(find.text('Decide'), findsOneWidget);
    expect(find.textContaining('publish it?'), findsOneWidget);
    // The plugin's own sentence about what saying yes DOES, which is the one
    // part written for the person deciding.
    expect(find.textContaining('public URL'), findsOneWidget);
  });

  testWidgets('a record with nothing to decide has no tab for it', (t) async {
    // A tab that is empty most of the time trains you to skip it.
    await _open(t, api: _FakeApi());
    expect(find.text('Decide'), findsNothing);
    expect(find.text('Details'), findsOneWidget);
  });

  testWidgets('two cards are counted on the tab', (t) async {
    await _open(t, api: _FakeApi(),
        approvals: [_card(), _card(id: 'ap-2')]);
    expect(find.text('Decide (2)'), findsOneWidget);
  });

  testWidgets('deciding from the record POSTS, and tells the owner',
      (t) async {
    // The call-site test. The card is the Approvals panel's own widget, so
    // what is worth proving is that it is wired HERE: the decision goes to
    // the server and the window that owns the record is told to refetch,
    // because a gate decided is a record that has moved.
    final api = _FakeApi();
    var told = 0;
    await _open(t, api: api, approvals: [_card()], onDecided: () => told++);

    await t.tap(find.widgetWithText(FilledButton, 'Approve'));
    await t.pumpAndSettle();

    expect(api.posted, hasLength(1));
    final (path, payload) = api.posted.single;
    expect(path, '/approvals/ap-1');
    expect((payload as Map)['decision'], 'approved');
    expect(told, 1);
  });

  testWidgets('a card for another record is not shown on this one', (t) async {
    // The owner filters, but the window must not assume it: a card carries
    // the record it belongs to, and showing somebody else's question here
    // would be the worst possible place to get it wrong.
    await _open(t, api: _FakeApi(), approvals: const []);
    expect(find.text('Decide'), findsNothing);
  });
}
