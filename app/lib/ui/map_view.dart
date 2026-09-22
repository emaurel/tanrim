import 'package:flutter/material.dart';
import 'dart:math' as math;

import 'package:flutter/gestures.dart';

import '../model/castle.dart';
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
    this.castles = const [],
    this.castleBadges = const {},
  });

  final List<Room> rooms;
  final List<AgentState> agents;
  final Map<String, int> badges;
  final void Function(Room) onRoomTapped;
  final String? selectedRoom;

  /// Drawn instead of the rooms when zoomed out far enough.
  final List<Castle> castles;
  final Map<String, int> castleBadges;

  @override
  State<MapView> createState() => _MapViewState();
}

class _MapViewState extends State<MapView>
    with SingleTickerProviderStateMixin {
  final _iso = const Iso();

  /// Far enough out to see an estate of castles, close enough to read a
  /// bench. The old floor of 0.2 never reached the estate view.
  static const _minZoom = 0.06;
  static const _maxZoom = 3.0;

  bool get _far => _zoom < WorldPainter.farZoom;

  Offset _camera = Offset.zero;
  double _zoom = 1;
  String? _hovered;
  String? _hoveredCastle;

  /// A camera move in flight. Clicking a castle from far off should travel to
  /// it rather than teleport: the jump is what makes an operator lose track of
  /// where they were.
  _Flight? _flight;
  late final Ticker _ticker = Ticker(_onTick)..start();
  double _tick = 0;

  // Pinch/drag bookkeeping.
  Offset _dragAnchor = Offset.zero;
  double _zoomAnchor = 1;
  bool _framed = false;

  void _onTick(Duration elapsed) {
    setState(() {
      _tick = elapsed.inMilliseconds / 1000.0;
      final f = _flight;
      if (f == null) return;
      final t = ((_tick - f.startedAt) / f.seconds).clamp(0.0, 1.0);
      // Ease in and out, so it reads as travel rather than a snap.
      final e = t < 0.5 ? 2 * t * t : 1 - math.pow(-2 * t + 2, 2) / 2;
      _zoom = f.fromZoom + (f.toZoom - f.fromZoom) * e;
      _camera = Offset.lerp(f.fromCamera, f.toCamera, e.toDouble())!;
      if (t >= 1.0) _flight = null;
    });
  }

  /// Centre the view on a rectangle of tiles, at a zoom that fits it.
  void _flyTo(double x, double y, double w, double h, Size size) {
    // The four corners of the box, projected, give the screen extent — a
    // rectangle in tile space is a diamond on screen, so its width is not its
    // tile width.
    var minX = double.infinity, maxX = -double.infinity;
    var minY = double.infinity, maxY = -double.infinity;
    for (final c in [
      _iso.toScreen(x, y),
      _iso.toScreen(x + w, y),
      _iso.toScreen(x + w, y + h),
      _iso.toScreen(x, y + h),
    ]) {
      minX = c.dx < minX ? c.dx : minX;
      maxX = c.dx > maxX ? c.dx : maxX;
      minY = c.dy < minY ? c.dy : minY;
      maxY = c.dy > maxY ? c.dy : maxY;
    }
    final fit = ((size.width * 0.86) / (maxX - minX))
        .clamp(_minZoom, _maxZoom);
    final fitY = ((size.height * 0.72) / (maxY - minY))
        .clamp(_minZoom, _maxZoom);
    final z = (fit < fitY ? fit : fitY).toDouble();
    final centre = Offset((minX + maxX) / 2, (minY + maxY) / 2);
    _flight = _Flight(
      startedAt: _tick,
      seconds: 0.55,
      fromZoom: _zoom,
      toZoom: z,
      fromCamera: _camera,
      toCamera: Offset(-centre.dx * z, -centre.dy * z),
    );
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
    final fit = (size.width * 0.92 / w).clamp(_minZoom, _maxZoom);
    final fitY = (size.height * 0.80 / h).clamp(_minZoom, _maxZoom);
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

  Castle? _castleAt(Offset local, Size size) {
    final t = _tileAt(local, size);
    for (final c in widget.castles.reversed) {
      final (x, y, w, h) = c.bounds;
      if (t.dx >= x && t.dx < x + w && t.dy >= y && t.dy < y + h) return c;
    }
    return null;
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
    // The painter is not in the widget tree, so it cannot read the theme.
    final family = DefaultTextStyle.of(context).style.fontFamily ??
        Theme.of(context).textTheme.bodyMedium?.fontFamily;

    return LayoutBuilder(builder: (context, box) {
      final size = Size(box.maxWidth, box.maxHeight);
      _frame(size);
      return Listener(
        // Wheel zoom, anchored so the map does not slide away under the cursor.
        onPointerSignal: (e) {
          if (e is! PointerScrollEvent) return;
          setState(() {
            final before = _zoom;
            _flight = null;   // the operator took the wheel
            _zoom = (_zoom * (e.scrollDelta.dy > 0 ? 0.9 : 1.1))
                .clamp(_minZoom, _maxZoom);
            final k = _zoom / before;
            _camera = Offset(_camera.dx * k, _camera.dy * k);
          });
        },
        child: MouseRegion(
          onHover: (e) {
            if (_far) {
              final c = _castleAt(e.localPosition, size);
              if (c?.pluginId != _hoveredCastle) {
                setState(() => _hoveredCastle = c?.pluginId);
              }
              return;
            }
            final r = _roomAt(e.localPosition, size);
            if (r?.id != _hovered) setState(() => _hovered = r?.id);
          },
          onExit: (_) => setState(() {
            _hovered = null;
            _hoveredCastle = null;
          }),
          child: GestureDetector(
            behavior: HitTestBehavior.opaque,
            onScaleStart: (d) {
              _flight = null;
              _dragAnchor = d.localFocalPoint - _camera;
              _zoomAnchor = _zoom;
            },
            onScaleUpdate: (d) {
              setState(() {
                if (d.scale != 1.0) {
                  _zoom =
                      (_zoomAnchor * d.scale).clamp(_minZoom, _maxZoom);
                }
                _camera = d.localFocalPoint - _dragAnchor;
              });
            },
            onTapUp: (d) {
              // Zoomed out, a tap is "take me there" rather than "open this":
              // there is nothing to open at a distance where a room is four
              // pixels across.
              if (_far) {
                final c = _castleAt(d.localPosition, size);
                if (c != null) {
                  final (x, y, w, h) = c.bounds;
                  setState(() {
                    _hoveredCastle = null;
                    _flyTo(x, y, w, h, size);
                  });
                }
                return;
              }
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
                castles: widget.castles,
                castleBadges: widget.castleBadges,
                hoveredCastle: _hoveredCastle,
                hoveredRoom: _hovered,
                selectedRoom: widget.selectedRoom,
                badges: widget.badges,
                fontFamily: family,
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

/// A camera move in progress: where it started, where it is going, and when.
class _Flight {
  _Flight({
    required this.startedAt,
    required this.seconds,
    required this.fromZoom,
    required this.toZoom,
    required this.fromCamera,
    required this.toCamera,
  });

  final double startedAt;
  final double seconds;
  final double fromZoom;
  final double toZoom;
  final Offset fromCamera;
  final Offset toCamera;
}
