import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:tanrim/api/client.dart';
import 'package:tanrim/model/approval.dart';
import 'package:tanrim/ui/approvals.dart';

Approval _a(String kind,
        {Map<String, dynamic> payload = const {},
        double ts = 0,
        String castle = 'c1'}) =>
    Approval({
      'id': 'a$kind$ts$castle',
      'kind': kind,
      'room_id': 'r',
      'castle_id': castle,
      'summary': '$kind happened',
      'ts': ts,
      'payload': payload,
    });

Future<void> _pump(WidgetTester t, List<Approval> list) => t.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SizedBox(
            width: 420,
            height: 900,
            child: Approvals(
              api: Api('http://127.0.0.1:1'),
              approvals: list,
              onResolved: () {},
              castleNames: const {
                'c1': 'Web agency 1',
                'c2': 'Nimes office',
              },
            ),
          ),
        ),
      ),
    );

void main() {
  testWidgets('nothing waiting says so', (t) async {
    await _pump(t, const []);
    expect(find.text('nothing waiting on you'), findsOneWidget);
  });

  testWidgets('oldest first', (t) async {
    // A card that has sat for two days is the one to look at, not the one
    // that arrived while you were reading.
    await _pump(t, [_a('x', ts: 200), _a('y', ts: 100)]);
    expect(t.getTopLeft(find.text('y happened')).dy,
        lessThan(t.getTopLeft(find.text('x happened')).dy));
  });

  testWidgets('a decision offers reject as easily as approve', (t) async {
    // Saying no must never be the harder button: an unsolicited email and a
    // page on a public URL are both easier to prevent than to undo.
    await _pump(t, [_a('send_outreach', payload: const {'to': 'a@b.fr'})]);
    expect(find.text('Approve'), findsOneWidget);
    expect(find.text('Reject'), findsOneWidget);
    expect(find.byType(TextField), findsOneWidget,
        reason: 'a rejection needs somewhere to say why — without it the '
            'rebuild is identical to the one that was rejected');
  });

  testWidgets('an informational card only offers dismiss', (t) async {
    // There is no handler behind these; approving one would mean nothing.
    await _pump(t, [_a('agent_crashed', payload: const {'agent': 'probe'})]);
    expect(find.text('Dismiss'), findsOneWidget);
    expect(find.text('Approve'), findsNothing);
    expect(find.text('Reject'), findsNothing);
  });

  testWidgets('an email gate shows what the stranger will read', (t) async {
    await _pump(t, [
      _a('send_outreach', payload: const {
        'to': 'owner@example.fr',
        'business': 'Chez Test',
        'subject': 'J\'ai fait un site',
        'body': 'Bonjour, je suis un développeur indépendant…',
        'quote': {'amount': 130, 'currency': 'EUR'},
      })
    ]);
    expect(find.textContaining('owner@example.fr'), findsOneWidget);
    expect(find.textContaining("J'ai fait un site"), findsOneWidget);
    expect(find.textContaining('Bonjour'), findsOneWidget);
    expect(find.textContaining('130 EUR'), findsOneWidget);
  });

  testWidgets('a publish gate shows what QA still objects to', (t) async {
    await _pump(t, [
      _a('publish_site', payload: const {
        'business': 'Chez Test',
        'staging_url': 'http://x/staging/1/',
        'qa_summary': 'A three-page site.',
        'qa_problems': [
          {'severity': 'critical', 'problem': 'tap targets under 44px'},
          {'severity': 'major', 'problem': 'contrast fails AA'},
        ],
      })
    ]);
    expect(find.textContaining('2 open problems'), findsOneWidget);
    expect(find.textContaining('tap targets'), findsOneWidget);
  });

  testWidgets('a gate this build has never seen still renders', (t) async {
    // The environment is plugin-driven: a plugin adds a gate kind and the app
    // must still let the operator decide it. A card it refuses to draw is a
    // decision that cannot be made.
    await _pump(t, [
      _a('something_new', payload: const {
        'invented_field': 'a value',
        'what_this_means': 'approving does the thing',
      })
    ]);
    expect(find.textContaining('invented_field'), findsOneWidget);
    expect(find.textContaining('a value'), findsOneWidget);
    expect(find.textContaining('approving does the thing'), findsOneWidget);
    expect(find.text('Approve'), findsOneWidget);
  });

  test('a traceback keeps the frames and drops the carets', () {
    const trace = '''
Traceback (most recent call last):
  File "a.py", line 1, in f
    thing.call()
    ^^^^^^^^^^^^
  File "b.py", line 2, in g
    other()
    ~~~~~~~
AttributeError: no attribute 'x\'''';
    final out = tracebackTail(trace, lines: 4);
    expect(out, contains('AttributeError'));
    expect(out, isNot(contains('^^^')));
    expect(out, isNot(contains('~~~')));
    expect(out.split('\n').length, 4);
  });

  testWidgets('cards are grouped by the castle that raised them', (t) async {
    // A decision belongs to a place. Two agencies' gates in one list is the
    // old board's problem again — you cannot tell whose email you are about
    // to send.
    await _pump(t, [
      _a('send_outreach', ts: 1, castle: 'c1'),
      _a('publish_site', ts: 2, castle: 'c2'),
    ]);

    expect(find.text('WEB AGENCY 1'), findsOneWidget);
    expect(find.text('NIMES OFFICE'), findsOneWidget);
    expect(find.textContaining('send_outreach happened'), findsOneWidget);
    expect(find.textContaining('publish_site happened'), findsOneWidget);
  });

  testWidgets('a castle collapses, and starts open', (t) async {
    // Open by default: a pending approval is something waiting on you, and
    // hiding it behind a click is how one sits unnoticed for two days.
    await _pump(t, [_a('send_outreach', ts: 1, castle: 'c1')]);
    expect(find.textContaining('send_outreach happened'), findsOneWidget);

    await t.tap(find.text('WEB AGENCY 1'));
    await t.pumpAndSettle();
    expect(find.textContaining('send_outreach happened'), findsNothing);
    // The header stays, with its count, so you can open it again.
    expect(find.text('WEB AGENCY 1'), findsOneWidget);
    expect(find.text('1'), findsOneWidget);

    await t.tap(find.text('WEB AGENCY 1'));
    await t.pumpAndSettle();
    expect(find.textContaining('send_outreach happened'), findsOneWidget);
  });

  testWidgets('a card with no castle is still shown', (t) async {
    // A pending approval nobody can see is the one kind of state this
    // environment must never have.
    await _pump(t, [_a('agent_crashed', ts: 1, castle: '')]);
    expect(find.text('NO CASTLE'), findsOneWidget);
    expect(find.textContaining('agent_crashed happened'), findsOneWidget);
  });
}
