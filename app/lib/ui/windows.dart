import 'dart:math' as math;

import 'package:flutter/material.dart';

/// One window's contents, as the app describes it.
///
/// Deliberately has no geometry: where a window IS belongs to the layer, which
/// remembers it across rebuilds, and the app should not have to hold a
/// position for every panel it might open.
class AppWindow {
  const AppWindow({
    required this.id,
    required this.title,
    required this.child,
    this.icon,
    this.initialSize = const Size(420, 560),
    this.subtitle = '',
  });

  final String id;
  final String title;
  final String subtitle;
  final IconData? icon;
  final Widget child;
  final Size initialSize;
}

/// Floating windows over the map.
///
/// ## Why the geometry is a `ValueNotifier` per window
///
/// Dragging a window moves it every frame. If the layer held positions in its
/// own state, each of those frames would rebuild every open window — and with
/// a room panel and a board in two of them, that is a lot of widget building
/// to move a title bar sixty times a second.
///
/// So each window owns a `ValueNotifier<Rect>`, and its frame is a
/// `ValueListenableBuilder` whose CONTENT is passed as `child`. The content
/// is built once and handed through untouched; only the `Positioned` around
/// it is rebuilt. Dragging one window rebuilds nothing but that window's
/// frame, and nothing at all inside it.
///
/// Each window is also a `RepaintBoundary`, so moving one does not repaint
/// the others or the map underneath.
class WindowLayer extends StatefulWidget {
  const WindowLayer({
    super.key,
    required this.windows,
    required this.onClose,
    this.onFocus,
  });

  /// The windows that should be open, in no particular order — the layer keeps
  /// its own front-to-back ordering, because that is a property of what you
  /// last clicked and not of what the app has open.
  final List<AppWindow> windows;
  final void Function(String id) onClose;
  final void Function(String id)? onFocus;

  @override
  State<WindowLayer> createState() => WindowLayerState();
}

class WindowLayerState extends State<WindowLayer> {
  final Map<String, ValueNotifier<Rect>> _rects = {};

  /// Back to front. The last id is the one on top.
  final List<String> _order = [];

  static const _minSize = Size(280, 200);
  static const _headerHeight = 34.0;

  /// Where the next new window goes.
  ///
  /// Cascaded rather than centred: two windows opened one after the other
  /// should not land exactly on top of each other, and the offset is what
  /// makes the one underneath visible enough to click.
  int _opened = 0;

  @override
  void dispose() {
    for (final r in _rects.values) {
      r.dispose();
    }
    super.dispose();
  }

  void _sync(Size bounds) {
    final ids = {for (final w in widget.windows) w.id};

    for (final w in widget.windows) {
      if (_rects.containsKey(w.id)) continue;
      final step = (_opened++ % 6) * 28.0;
      final size = Size(
        math.min(w.initialSize.width, math.max(_minSize.width, bounds.width - 40)),
        math.min(w.initialSize.height,
            math.max(_minSize.height, bounds.height - 40)),
      );
      final left = math.max(
          12.0,
          math.min(bounds.width - size.width - 12,
              bounds.width - size.width - 24 - step));
      final top = math.max(12.0, 56.0 + step);
      _rects[w.id] =
          ValueNotifier(Rect.fromLTWH(left, top, size.width, size.height));
      _order.add(w.id);
    }

    for (final gone in _rects.keys.where((id) => !ids.contains(id)).toList()) {
      _rects.remove(gone)!.dispose();
      _order.remove(gone);
    }
  }

  void _raise(String id) {
    if (_order.isEmpty || _order.last == id) return;
    setState(() {
      _order.remove(id);
      _order.add(id);
    });
    widget.onFocus?.call(id);
  }

  /// Keep a window reachable: its header must stay inside the layer, or it
  /// can be dragged somewhere it can never be dragged back from.
  Rect _clamp(Rect r, Size bounds) {
    final left = r.left.clamp(24.0 - r.width, bounds.width - 24.0);
    final top = r.top.clamp(0.0, bounds.height - _headerHeight);
    return Rect.fromLTWH(left, top, r.width, r.height);
  }

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(builder: (context, constraints) {
      final bounds = Size(constraints.maxWidth, constraints.maxHeight);
      _sync(bounds);

      final byId = {for (final w in widget.windows) w.id: w};
      return Stack(
        children: [
          for (final id in _order)
            if (byId[id] != null)
              _Frame(
                key: ValueKey(id),
                window: byId[id]!,
                rect: _rects[id]!,
                bounds: bounds,
                minSize: _minSize,
                headerHeight: _headerHeight,
                clamp: _clamp,
                onRaise: () => _raise(id),
                onClose: () => widget.onClose(id),
              ),
        ],
      );
    });
  }
}

class _Frame extends StatelessWidget {
  const _Frame({
    super.key,
    required this.window,
    required this.rect,
    required this.bounds,
    required this.minSize,
    required this.headerHeight,
    required this.clamp,
    required this.onRaise,
    required this.onClose,
  });

  final AppWindow window;
  final ValueNotifier<Rect> rect;
  final Size bounds;
  final Size minSize;
  final double headerHeight;
  final Rect Function(Rect, Size) clamp;
  final VoidCallback onRaise;
  final VoidCallback onClose;

  @override
  Widget build(BuildContext context) {
    // Built ONCE, here, and handed to the builder below as `child`. This is
    // the whole reason dragging is cheap: the contents never rebuild.
    final content = RepaintBoundary(
      key: ValueKey('window-frame:${window.id}'),
      child: Material(
        color: const Color(0xFF161922),
        elevation: 10,
        borderRadius: BorderRadius.circular(9),
        clipBehavior: Clip.antiAlias,
        child: Column(children: [
          _header(context),
          Expanded(child: window.child),
        ]),
      ),
    );

    return Positioned.fill(
      child: ValueListenableBuilder<Rect>(
        valueListenable: rect,
        child: content,
        builder: (_, r, child) => Stack(children: [
          Positioned(
            left: r.left,
            top: r.top,
            width: r.width,
            height: r.height,
            child: Stack(children: [
              Positioned.fill(child: child!),
              // The grip, on top of the content's own corner.
              Positioned(
                right: 0,
                bottom: 0,
                child: _grip(),
              ),
            ]),
          ),
        ]),
      ),
    );
  }

  Widget _header(BuildContext context) => GestureDetector(
        behavior: HitTestBehavior.opaque,
        onPanStart: (_) => onRaise(),
        onPanUpdate: (d) {
          final r = rect.value;
          rect.value = clamp(
              Rect.fromLTWH(r.left + d.delta.dx, r.top + d.delta.dy,
                  r.width, r.height),
              bounds);
        },
        child: Container(
          height: headerHeight,
          padding: const EdgeInsets.only(left: 12, right: 4),
          color: Colors.white.withValues(alpha: .05),
          child: Row(children: [
            if (window.icon != null) ...[
              Icon(window.icon,
                  size: 14, color: Colors.white.withValues(alpha: .45)),
              const SizedBox(width: 8),
            ],
            Flexible(
              child: Text(window.title,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                      fontSize: 12.5, fontWeight: FontWeight.w600)),
            ),
            if (window.subtitle.isNotEmpty) ...[
              const SizedBox(width: 8),
              Flexible(
                child: Text(window.subtitle,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                        fontSize: 11, color: Colors.white38)),
              ),
            ],
            const Spacer(),
            IconButton(
              onPressed: onClose,
              iconSize: 15,
              visualDensity: VisualDensity.compact,
              icon: const Icon(Icons.close),
              tooltip: 'Close',
            ),
          ]),
        ),
      );

  Widget _grip() => MouseRegion(
        cursor: SystemMouseCursors.resizeDownRight,
        child: GestureDetector(
          behavior: HitTestBehavior.opaque,
          onPanStart: (_) => onRaise(),
          onPanUpdate: (d) {
            final r = rect.value;
            rect.value = Rect.fromLTWH(
              r.left,
              r.top,
              math.max(minSize.width, r.width + d.delta.dx),
              math.max(minSize.height, r.height + d.delta.dy),
            );
          },
          child: SizedBox(
            width: 18,
            height: 18,
            child: CustomPaint(painter: _GripPainter()),
          ),
        ),
      );
}

class _GripPainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    final p = Paint()
      ..color = Colors.white.withValues(alpha: .28)
      ..strokeWidth = 1.2;
    for (var i = 1; i <= 3; i++) {
      final d = i * 4.5;
      canvas.drawLine(
          Offset(size.width - d, size.height - 2),
          Offset(size.width - 2, size.height - d),
          p);
    }
  }

  @override
  bool shouldRepaint(_GripPainter old) => false;
}
