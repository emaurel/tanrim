import 'package:flutter/services.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:tanrim/ui/blocks.dart';
import 'package:tanrim/ui/lightbox.dart';

/// A document an agent wrote, as the operator meets it.
///
/// The point of the block is that a PDF is READ before a send is approved.
/// The app has no PDF renderer — there is none for Flutter on the desktop
/// that does not drag in a native build — so the server hands over the pages
/// already rendered, and the thing worth testing is that they get drawn
/// rather than reduced to a filename in blue.

Map<String, dynamic> _doc({
  int pages = 2,
  String title = 'Portfolio — three projects',
  Object? bytes = 30708,
}) =>
    {
      'block': 'documents',
      'title': 'Documents',
      'items': [
        {
          'name': 'portfolio',
          'title': title,
          'url': 'http://h:8765/applications/r1/documents/portfolio.pdf',
          'pages': pages,
          'bytes': bytes,
          'made_at': '2026-10-14T16:04:28',
          'page_urls': [
            for (var n = 1; n <= pages; n++)
              'http://h:8765/applications/r1/documents/portfolio/p$n.png?v=1',
          ],
        },
      ],
    };

Future<void> _draw(WidgetTester t, Map<String, dynamic> block) async {
  t.view
    ..physicalSize = const Size(900, 900)
    ..devicePixelRatio = 1.0;
  addTearDown(t.view.reset);
  await t.pumpWidget(MaterialApp(
    home: Scaffold(body: SingleChildScrollView(child: Blocks(blocks: [block]))),
  ));
  await t.pumpAndSettle();
}

void main() {
  testWidgets('a document names itself, its length and when it was written',
      (t) async {
    await _draw(t, _doc());
    expect(find.text('Portfolio — three projects'), findsOneWidget);
    expect(find.textContaining('2 pages'), findsOneWidget);
    expect(find.textContaining('30 KB'), findsOneWidget);
    expect(find.textContaining('2026-10-14'), findsOneWidget);
    expect(find.byIcon(Icons.picture_as_pdf_outlined), findsOneWidget);
  });

  testWidgets('one page is a page, not 1 pages', (t) async {
    await _draw(t, _doc(pages: 1));
    expect(find.textContaining('1 page '), findsOneWidget);
    expect(find.textContaining('1 pages'), findsNothing);
  });

  testWidgets('a size in bytes is not read as kilobytes', (t) async {
    // `_bytes`, the value format, reads its number as KB because the web
    // agency's weight budget is in KB. A file reports real bytes, and 30,708
    // of those are 30 KB rather than 30 megabytes.
    await _draw(t, _doc(bytes: 30708));
    expect(find.textContaining('30 KB'), findsOneWidget);
    expect(find.textContaining('MB'), findsNothing);
  });

  testWidgets('tapping it opens the pages', (t) async {
    await _draw(t, _doc());
    await t.tap(find.text('Portfolio — three projects'));
    await t.pumpAndSettle();

    // The reader is open, with one Image per page.
    expect(find.text('2 pages'), findsOneWidget);
    expect(find.byType(Image), findsNWidgets(2));
    expect(find.text('open the PDF'), findsOneWidget);
  });

  testWidgets('escape closes the reader', (t) async {
    await _draw(t, _doc());
    await t.tap(find.text('Portfolio — three projects'));
    await t.pumpAndSettle();
    expect(find.text('2 pages'), findsOneWidget);

    await t.sendKeyEvent(LogicalKeyboardKey.escape);
    await t.pumpAndSettle();
    expect(find.text('2 pages'), findsNothing);
  });

  testWidgets('a document with no rendered pages says so rather than showing '
      'an empty white rectangle', (t) async {
    // A plugin that can serve the file but not its pages is allowed.
    final block = _doc(pages: 0);
    (block['items'] as List).first['page_urls'] = <String>[];
    await _draw(t, block);
    await t.tap(find.text('Portfolio — three projects'));
    await t.pumpAndSettle();
    expect(find.textContaining('no pages were rendered'), findsOneWidget);
  });

  testWidgets('a documents block with no items does not throw', (t) async {
    await _draw(t, {'block': 'documents', 'title': 'Documents', 'items': []});
    expect(find.text('DOCUMENTS'), findsOneWidget);
    expect(find.byIcon(Icons.picture_as_pdf_outlined), findsNothing);
  });

  testWidgets('the reader can be opened on its own', (t) async {
    // `showDocument` is the core of it and is worth reaching directly, the
    // way the record window will once a document can be opened from a card.
    late BuildContext ctx;
    await t.pumpWidget(MaterialApp(home: Builder(builder: (c) {
      ctx = c;
      return const Scaffold(body: SizedBox());
    })));
    showDocument(ctx,
        title: 'Written answer',
        pageUrls: const ['http://h/p1.png'],
        fileUrl: 'http://h/a.pdf');
    await t.pumpAndSettle();
    expect(find.text('Written answer'), findsOneWidget);
    expect(find.text('1 page'), findsOneWidget);
  });
}
