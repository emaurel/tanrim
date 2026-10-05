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
  _layoutTests();
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

void _layoutTests() {
  group('a tall picture', () {
    const window = Size(1200, 800);

    test('a filled form takes the full width and runs off the bottom', () {
      // 1,280 by 4,228 — a real one. Fitted, that is a sliver of illegible
      // grey, and the zoom was trapped inside the sliver.
      final laid = layFor(1280 / 4228, window);
      expect(laid.width, window.width);
      expect(laid.height, greaterThan(window.height * 3));
    });

    test('an ordinary photograph is fitted inside the frame', () {
      // 16:9 in a 3:2 window is WIDTH-limited: 1200 across, 675 down.
      final laid = layFor(1600 / 900, window);
      expect(laid.width, window.width);
      expect(laid.height, lessThanOrEqualTo(window.height));
    });

    test('a little taller than the frame is still fitted, not scrolled', () {
      // A square in a landscape window does not want to be scrolled.
      final laid = layFor(1.0, window);
      expect(laid.height, lessThanOrEqualTo(window.height));
    });



    test('the shape is never distorted', () {
      for (final a in [0.3, 0.75, 1.0, 1.78, 3.0]) {
        final laid = layFor(a, window);
        expect(laid.width / laid.height, closeTo(a, 1e-9),
            reason: 'aspect $a came out stretched');
      }
    });
  });
}
