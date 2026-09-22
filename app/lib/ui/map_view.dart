import 'package:flutter/material.dart';
import 'package:flutter/gestures.dart';

import '../model/world.dart';
import '../world/iso.dart';
import '../world/painter.dart';

/// The world, pannable and zoomable, with rooms you can click.
class MapView extends StatefulWidget {
  const MapView({
    super.key,
    required this.rooms,
    required this.agents,
    required this.badges,
    required this.onRoomTapped,
    this.selectedRoom,
  });

  final List<Room> rooms;
  final List<AgentState> agents;
  final Map<String, int> badges;
  final void Function(Room) onRoomTapped;
  final String? selectedRoom;

  @override
  State<MapView> createState() => _MapViewState();
}

class _MapViewState extends State<MapView>
    with SingleTickerProviderStateMixin {
  final _iso = const Iso();

  Offset _camera = Offset.zero;
  double _zoom = 1;
  String? _hovered;
  late final Ticker _ticker = Ticker(_onTick)..start();
  double _tick = 0;

  // Pinch/drag bookkeeping.
  Offset _dragAnchor = Offset.zero;
  double _zoomAnchor = 1;
  bool _framed = false;

  void _onTick(Duration elapsed) {
    setState(() => _tick = elapsed.inMilliseconds / 1000.0);
  }

  @override
  void dispose() {
    _ticker.dispose();
    super.dispose();
  }

  /// Put the whole map on screen the first time rooms arrive. Without this the
  /// operator opens the app looking at empty space somewhere near the origin.
  void _frame(Size size) {
    if (_framed || widget.rooms.isEmpty) return;
    _framed = true;
    var minX = double.infinity, maxX = -double.infinity;
    var minY = double.infinity, maxY = -double.infinity;
    for (final r in widget.rooms) {
      for (final c in [
        _iso.toScreen(r.position.x, r.position.y),
        _iso.toScreen(r.position.x + r.size.x, r.position.y),
        _iso.toScreen(r.position.x + r.size.x, r.position.y + r.size.y),
        _iso.toScreen(r.position.x, r.position.y + r.size.y),
      ]) {
        minX = c.dx < minX ? c.dx : minX;
        maxX = c.dx > maxX ? c.dx : maxX;
        minY = c.dy < minY ? c.dy : minY;
        maxY = c.dy > maxY ? c.dy : maxY;
      }
    }
    final w = maxX - minX, h = maxY - minY;
    if (w <= 0 || h <= 0) return;
    final fit = (size.width * 0.92 / w).clamp(0.2, 2.0);
    final fitY = (size.height * 0.80 / h).clamp(0.2, 2.0);
    _zoom = fit < fitY ? fit : fitY;
    final centre = Offset((minX + maxX) / 2, (minY + maxY) / 2);
    _camera = Offset(-centre.dx * _zoom, -centre.dy * _zoom);
  }

  /// Screen point -> tile, undoing the camera. Used for hit-testing.
  Offset _tileAt(Offset local, Size size) {
    final world = Offset(
      (local.dx - size.width / 2 - _camera.dx) / _zoom,
      (local.dy - size.height / 3 - _camera.dy) / _zoom,
    );
    return _iso.toTile(world);
  }

  Room? _roomAt(Offset local, Size size) {
    final t = _tileAt(local, size);
    // Near rooms first: they are drawn last and so are the ones on top.
    for (final r in widget.rooms.reversed) {
      if (t.dx >= r.position.x &&
          t.dx < r.position.x + r.size.x &&
          t.dy >= r.position.y &&
          t.dy < r.position.y + r.size.y) {
        return r;
      }
    }
    return null;
  }

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(builder: (context, box) {
      final size = Size(box.maxWidth, box.maxHeight);
      _frame(size);
      return Listener(
        // Wheel zoom, anchored so the map does not slide away under the cursor.
        onPointerSignal: (e) {
          if (e is! PointerScrollEvent) return;
          setState(() {
            final before = _zoom;
            _zoom = (_zoom * (e.scrollDelta.dy > 0 ? 0.9 : 1.1))
                .clamp(0.25, 3.0);
            final k = _zoom / before;
            _camera = Offset(_camera.dx * k, _camera.dy * k);
          });
        },
        child: MouseRegion(
          onHover: (e) {
            final r = _roomAt(e.localPosition, size);
            if (r?.id != _hovered) setState(() => _hovered = r?.id);
          },
          onExit: (_) => setState(() => _hovered = null),
          child: GestureDetector(
            behavior: HitTestBehavior.opaque,
            onScaleStart: (d) {
              _dragAnchor = d.localFocalPoint - _camera;
              _zoomAnchor = _zoom;
            },
            onScaleUpdate: (d) {
              setState(() {
                if (d.scale != 1.0) {
                  _zoom = (_zoomAnchor * d.scale).clamp(0.25, 3.0);
                }
                _camera = d.localFocalPoint - _dragAnchor;
              });
            },
            onTapUp: (d) {
              final r = _roomAt(d.localPosition, size);
              if (r != null) widget.onRoomTapped(r);
            },
            child: CustomPaint(
              size: size,
              painter: WorldPainter(
                rooms: widget.rooms,
                agents: widget.agents,
                camera: _camera,
                zoom: _zoom,
                iso: _iso,
                tick: _tick,
                hoveredRoom: _hovered,
                selectedRoom: widget.selectedRoom,
                badges: widget.badges,
              ),
            ),
          ),
        ),
      );
    });
  }
}

/// A frame ticker without pulling in the scheduler binding boilerplate.
class Ticker {
  Ticker(this.onTick);
  final void Function(Duration) onTick;
  bool _running = false;
  final _watch = Stopwatch();

  void start() {
    if (_running) return;
    _running = true;
    _watch.start();
    _schedule();
  }

  void _schedule() {
    if (!_running) return;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_running) return;
      onTick(_watch.elapsed);
      _schedule();
    });
  }

  void dispose() {
    _running = false;
    _watch.stop();
  }
}
