import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:tanrim/ui/windows.dart';

/// Counts how many times it is built, so a test can prove it was not.
class _Counter extends StatelessWidget {
  const _Counter({required this.label, required this.builds});
  final String label;
  final List<String> builds;

  @override
  Widget build(BuildContext context) {
    builds.add(label);
    return Center(child: Text('body:$label'));
  }
}

/// A window layer whose contents are rebuilt on every pass, as the app's are.
class _Harness extends StatefulWidget {
  const _Harness({required this.builds});
  final List<String> builds;

  @override
  State<_Harness> createState() => _HarnessState();
}

class _HarnessState extends State<_Harness> {
  @override
  Widget build(BuildContext context) => WindowLayer(
        windows: [
          AppWindow(
            id: 'one',
            title: 'one',
            // A NEW instance every build, so an ancestor rebuild is visible.
            child: _Counter(label: 'one', builds: widget.builds),
          ),
        ],
        onClose: (_) {},
      );
}

Future<List<String>> _layer(
  WidgetTester t, {
  required List<String> ids,
  List<String>? closed,
  Size size = const Size(1000, 760),
}) async {
  final builds = <String>[];
  t.view
    ..physicalSize = size
    ..devicePixelRatio = 1.0;
  addTearDown(t.view.reset);

  await t.pumpWidget(MaterialApp(
    home: Scaffold(
      body: WindowLayer(
        windows: [
          for (final id in ids)
            AppWindow(
              id: id,
              title: id,
              child: _Counter(label: id, builds: builds),
            ),
        ],
        onClose: (id) => closed?.add(id),
      ),
    ),
  ));
  await t.pumpAndSettle();
  return builds;
}

void main() {
  testWidgets('several windows open at once, and none on top of another',
      (t) async {
    await _layer(t, ids: ['one', 'two', 'three']);
    expect(find.text('body:one'), findsOneWidget);
    expect(find.text('body:two'), findsOneWidget);
    expect(find.text('body:three'), findsOneWidget);

    // Cascaded, so the one underneath is visible enough to click.
    expect(t.getTopLeft(find.text('body:one')),
        isNot(t.getTopLeft(find.text('body:two'))));
  });

  testWidgets('a window is dragged by its header', (t) async {
    await _layer(t, ids: ['one']);
    final before = t.getTopLeft(find.text('body:one'));

    // The header is the draggable part.
    await t.timedDrag(
        find.byType(GestureDetector).first,
        const Offset(-120, 90),
        const Duration(milliseconds: 120));
    await t.pumpAndSettle();
    final after = t.getTopLeft(find.text('body:one'));
    expect(after.dx, lessThan(before.dx));
    expect(after.dy, greaterThan(before.dy));
  });

  testWidgets('dragging rebuilds nothing above the window', (t) async {
    // The reason the geometry lives in a ValueNotifier rather than in the
    // layer's state. A window moves every frame while it is dragged; if that
    // were a setState, the app would rebuild its whole window list sixty
    // times a second — and each window's contents with it, because the app
    // builds those fresh on every pass.
    //
    // Shaped like production: the harness rebuilds its window list on every
    // build, so anything that rebuilds an ancestor shows up here.
    //
    // What this pins down is that dragging does not propagate UPWARD — the
    // app is never asked to rebuild while a window is being moved. It is a
    // weaker guarantee than it first looks: Flutter short-circuits an
    // identical widget, so a layer-level `setState` would not rebuild these
    // contents either. Two deliberately broken versions were tried against
    // it and both passed, which is worth knowing before trusting it to catch
    // a regression inside the layer.
    final builds = <String>[];
    t.view
      ..physicalSize = const Size(1000, 760)
      ..devicePixelRatio = 1.0;
    addTearDown(t.view.reset);

    await t.pumpWidget(MaterialApp(
      home: Scaffold(body: _Harness(builds: builds)),
    ));
    await t.pumpAndSettle();
    final built = builds.length;
    expect(built, greaterThan(0), reason: 'it was built at least once');

    await t.timedDrag(
        find.byType(GestureDetector).first,
        const Offset(-150, 60),
        const Duration(milliseconds: 300));
    await t.pumpAndSettle();

    expect(builds.length, built,
        reason: 'dragging rebuilt the window list, and so its contents');
  });

  testWidgets('a window is resized by its grip, and has a floor', (t) async {
    await _layer(t, ids: ['one']);
    final box = find.byKey(const ValueKey('window-frame:one'));
    final before = t.getSize(box);

    final grip = find.byType(CustomPaint).last;
    await t.timedDrag(grip, const Offset(120, 80),
        const Duration(milliseconds: 120));
    await t.pumpAndSettle();
    expect(t.getSize(box).width, greaterThan(before.width));
    expect(t.getSize(box).height, greaterThan(before.height));

    // Dragged far enough back, it stops rather than inverting.
    await t.timedDrag(grip, const Offset(-4000, -4000),
        const Duration(milliseconds: 120));
    await t.pumpAndSettle();
    expect(t.getSize(box).width, greaterThanOrEqualTo(280));
    expect(t.getSize(box).height, greaterThanOrEqualTo(200));
  });

  testWidgets('clicking a window brings it to the front', (t) async {
    await _layer(t, ids: ['one', 'two']);

    // `two` opened last, so it is on top: its frame is the last in the tree.
    Iterable<String> order() => t
        .widgetList<Text>(find.byType(Text))
        .map((w) => w.data ?? '')
        .where((s) => s == 'body:one' || s == 'body:two');
    expect(order().last, 'body:two');

    // Drag `one` a little; starting a drag on it raises it.
    await t.timedDrag(find.byType(GestureDetector).first,
        const Offset(6, 6), const Duration(milliseconds: 60));
    await t.pumpAndSettle();
    expect(order().last, 'body:one');
  });

  testWidgets('closing asks the app, which owns what is open', (t) async {
    final closed = <String>[];
    await _layer(t, ids: ['one'], closed: closed);
    await t.tap(find.byIcon(Icons.close));
    await t.pumpAndSettle();
    expect(closed, ['one']);
  });

  testWidgets('a window cannot be dragged somewhere it cannot come back from',
      (t) async {
    await _layer(t, ids: ['one']);
    await t.timedDrag(find.byType(GestureDetector).first,
        const Offset(-4000, -4000), const Duration(milliseconds: 200));
    await t.pumpAndSettle();

    // An edge of it has to stay on screen — the header spans the whole width,
    // so a visible sliver is enough to grab and drag it back.
    final frame = find.byKey(const ValueKey('window-frame:one'));
    final at = t.getTopLeft(frame);
    final size = t.getSize(frame);
    expect(at.dy, greaterThanOrEqualTo(0.0));
    expect(at.dx + size.width, greaterThan(0.0),
        reason: 'the whole window went off the left edge');
  });

  testWidgets('a window that closes frees its geometry', (t) async {
    await _layer(t, ids: ['one', 'two']);
    await _layer(t, ids: ['two']);
    expect(find.text('body:one'), findsNothing);
    expect(find.text('body:two'), findsOneWidget);
  });

  testWidgets('the close button sits in the corner', (t) async {
    await _layer(t, ids: ['one']);
    final frame = t.getRect(find.byKey(const ValueKey('window-frame:one')));
    final cross = t.getRect(find.byIcon(Icons.close));

    // Flush right and flush top, within the header's own height. An
    // `IconButton` carries 8px of padding inside a 40px minimum box, which
    // put the cross visibly short of the corner.
    expect(frame.right - cross.right, lessThan(12));
    expect(cross.top - frame.top, lessThan(12));
  });

  testWidgets('a window with a rename edits its title in place', (t) async {
    // The name lives in the title bar, and only there. It used to be drawn
    // again inside the window with a second close button beside it.
    final asked = <String>[];
    t.view
      ..physicalSize = const Size(900, 700)
      ..devicePixelRatio = 1.0;
    addTearDown(t.view.reset);

    await t.pumpWidget(MaterialApp(
      home: Scaffold(
        body: WindowLayer(
          windows: [
            AppWindow(
              id: 'c',
              title: 'Web agency 1',
              onRename: (n) async {
                asked.add(n);
                return '';
              },
              child: const SizedBox(),
            ),
          ],
          onClose: (_) {},
        ),
      ),
    ));
    await t.pumpAndSettle();

    expect(find.byType(TextField), findsNothing);
    await t.tap(find.text('Web agency 1'));
    await t.pumpAndSettle();
    expect(find.byType(TextField), findsOneWidget);

    await t.enterText(find.byType(TextField), 'Nimes office');
    await t.testTextInput.receiveAction(TextInputAction.done);
    await t.pumpAndSettle();
    expect(asked, ['Nimes office']);
  });

  testWidgets('a window without a rename is not editable', (t) async {
    await _layer(t, ids: ['one']);
    await t.tap(find.text('one'));
    await t.pumpAndSettle();
    expect(find.byType(TextField), findsNothing);
  });
}
