import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:tanrim/ui/blocks.dart';
import 'package:tanrim/ui/lightbox.dart';

Future<void> _pump(WidgetTester t, Widget child) async {
  await t.pumpWidget(MaterialApp(
      theme: ThemeData.dark(), home: Scaffold(body: child)));
  await t.pumpAndSettle();
}

void main() {
  testWidgets('a picture opens full size with a cross to close it', (t) async {
    await _pump(t, Builder(
      builder: (c) => TextButton(
        onPressed: () => showPicture(c, 'http://x.test/a.jpg', caption: 'a bay'),
        child: const Text('open'),
      ),
    ));
    await t.tap(find.text('open'));
    await t.pumpAndSettle();

    expect(find.byIcon(Icons.close_rounded), findsOneWidget);
    expect(find.text('a bay'), findsOneWidget);

    await t.tap(find.byIcon(Icons.close_rounded));
    await t.pumpAndSettle();
    expect(find.byIcon(Icons.close_rounded), findsNothing);
  });

  testWidgets('escape closes it, without reaching for the mouse', (t) async {
    await _pump(t, Builder(
      builder: (c) => TextButton(
        onPressed: () => showPicture(c, 'http://x.test/a.jpg'),
        child: const Text('open'),
      ),
    ));
    await t.tap(find.text('open'));
    await t.pumpAndSettle();

    await t.sendKeyEvent(LogicalKeyboardKey.escape);
    await t.pumpAndSettle();
    expect(find.byIcon(Icons.close_rounded), findsNothing);
  });

  testWidgets('an images block draws them and each one opens', (t) async {
    // A thumbnail is enough to know a photograph is there and not enough to
    // judge it — and judging it is why the build carries it at all.
    await _pump(t, Blocks(blocks: const [{
      'block': 'images',
      'title': 'Photographs (2)',
      'items': [
        {'url': 'http://x.test/one.jpg', 'caption': 'one.jpg'},
        {'url': 'http://x.test/two.jpg', 'caption': 'two.jpg'},
      ],
    }]));

    expect(find.byType(Image), findsNWidgets(2));
    await t.tap(find.byType(Image).first);
    await t.pumpAndSettle();
    expect(find.byIcon(Icons.close_rounded), findsOneWidget);
  });

  testWidgets('a value the server calls an image is shown, not spelled out',
      (t) async {
    // A path is the one thing about a photograph you cannot read.
    await _pump(t, Blocks(blocks: const [{
      'block': 'fields',
      'rows': [
        {'label': 'Logo', 'value': '/staging/x/photos/logo.png',
         'format': 'image'},
      ],
    }]));

    expect(find.byType(Image), findsOneWidget);
    // It opens, like any other picture.
    await t.tap(find.byType(Image));
    await t.pumpAndSettle();
    expect(find.byIcon(Icons.close_rounded), findsOneWidget);
  });

  testWidgets('an unreachable picture falls back to its path', (t) async {
    // Better than a broken-image icon: a filename at least tells you the name,
    // which is what the row said before any of this.
    await _pump(t, Blocks(blocks: const [{
      'block': 'fields',
      'rows': [
        {'label': 'Logo', 'value': '/gone/logo.png', 'format': 'image'},
      ],
    }]));
    // Network images never load in a test, so this is the error path.
    expect(find.text('/gone/logo.png'), findsOneWidget);
  });

  testWidgets('an ordinary value is still text', (t) async {
    await _pump(t, Blocks(blocks: const [{
      'block': 'fields',
      'rows': [{'label': 'Name', 'value': 'Garage Il Primo'}],
    }]));
    expect(find.text('Garage Il Primo'), findsOneWidget);
    expect(find.byType(Image), findsNothing);
  });
}
