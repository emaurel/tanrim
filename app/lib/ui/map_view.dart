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
    this.web = const Web(),
    this.taken = const {},
    this.onPlotTapped,
    this.onCastleTapped,
  });

  final List<Room> rooms;
  final List<AgentState> agents;
  final Map<String, int> badges;
  final void Function(Room) onRoomTapped;
  final String? selectedRoom;

  /// Drawn instead of the rooms when zoomed out far enough.
  final List<Castle> castles;
  final Map<String, int> castleBadges;

  /// The web's equation, and what is already built on. The plots themselves
  /// are generated per frame for whatever the viewport covers — which is what
  /// makes zooming out reveal more land instead of running out of it.
  final Web web;
  final Set<(int, int)> taken;

  /// Tapped an empty plot: `(ring, slot)`.
  final void Function(int ring, int slot)? onPlotTapped;

  /// Tapped a castle from far off. The map still flies to it; this is for
  /// anything the app wants to do as well, like opening its card.
  final void Function(Castle)? onCastleTapped;

  @override
  State<MapView> createState() => _MapViewState();
}

class _MapViewState extends State<MapView>
    with SingleTickerProviderStateMixin {
  final _iso = const Iso();

  /// Far enough out to see an estate of castles, close enough to read a
  /// bench. The old floor of 0.2 never reached the estate view.
  /// The zoom floor for a small world.
  ///
  /// Not the floor full stop — see [_floor]. The web of plots is infinite by
  /// construction, so a fixed floor is a promise that stops being true as soon
  /// as somebody builds far enough out: past about ring 4 the whole estate no
  /// longer fits on screen at 0.06 and there is no way to pull back further.
  static const _baseMinZoom = 0.06;

  /// The zoom at which everything that exists fits on screen, or
  /// [_baseMinZoom] — whichever is further out.
  ///
  /// Recomputed from the world rather than held as a constant, so "zoom all
  /// the way out" always means "show me everything" however far the map has
  /// grown. It only ever LOWERS the floor: a two-castle world still stops at
  /// 0.06 rather than letting you zoom into the middle distance and lose the
  /// map entirely.
  double _floor(Size size) {
    final b = _worldBounds();
    if (b == null) return _baseMinZoom;
    final (minX, minY, maxX, maxY) = b;
    final w = maxX - minX, h = maxY - minY;
    if (w <= 0 || h <= 0) return _baseMinZoom;
    final fit = math.min(size.width * 0.92 / w, size.height * 0.80 / h);
    return math.min(_baseMinZoom, fit);
  }

  /// The screen-space box around everything on the map — rooms, castles and
  /// the empty land being offered.
  (double, double, double, double)? _worldBounds() {
    var minX = double.infinity, maxX = -double.infinity;
    var minY = double.infinity, maxY = -double.infinity;
    var any = false;

    void take(double x, double y, double w, double h) {
      any = true;
      for (final c in [
        _iso.toScreen(x, y),
        _iso.toScreen(x + w, y),
        _iso.toScreen(x + w, y + h),
        _iso.toScreen(x, y + h),
      ]) {
        minX = math.min(minX, c.dx);
        maxX = math.max(maxX, c.dx);
        minY = math.min(minY, c.dy);
        maxY = math.max(maxY, c.dy);
      }
    }

    for (final r in widget.rooms) {
      take(r.position.x, r.position.y, r.size.x, r.size.y);
    }
    // The castles, not the plots: the web is infinite, so measuring the land
    // would make the floor drop until the map was a dot. What must always fit
    // is what has been BUILT, plus a ring of room around it to build in.
    for (final c in widget.castles) {
      final (x, y, w, h) = c.plot;
      take(x - widget.web.span, y - widget.web.span,
           w + widget.web.span * 2, h + widget.web.span * 2);
    }
    return any ? (minX, minY, maxX, maxY) : null;
  }

  static const _maxZoom = 3.0;

  bool get _far => _zoom < WorldPainter.farZoom;

  /// For tests: the camera's current zoom.
  @visibleForTesting
  double get debugZoom => _zoom;

  /// For tests: whether a camera move is in flight.
  @visibleForTesting
  bool get debugFlying => _flight != null;

  @visibleForTesting
  String? debugCastleAt(Offset local, Size size) =>
      _castleAt(local, size)?.id;

  @visibleForTesting
  double get debugTick => _tick;

  /// For tests: which tile is under a screen point. The inverse of
  /// [debugScreenOf], and what a zoom anchored at the pointer has to keep
  /// still.
  @visibleForTesting
  Offset debugTileAt(Offset local, Size size) => _tileAt(local, size);

  @visibleForTesting
  Offset debugScreenOf(double x, double y, Size size) {
    final w = _iso.toScreen(x, y);
    return Offset(w.dx * _zoom + size.width / 2 + _camera.dx,
        w.dy * _zoom + size.height / 3 + _camera.dy);
  }

  Offset _camera = Offset.zero;
  double _zoom = 1;
  String? _hovered;
  String? _hoveredCastle;
  String? _hoveredPlot;

  /// A camera move in flight. Clicking a castle from far off should travel to
  /// it rather than teleport: the jump is what makes an operator lose track of
  /// where they were.
  _Flight? _flight;
  /// NOT `late final … ..start()`: that is lazy, and the only other mention
  /// of the field is in `dispose`, so it was never initialised and the clock
  /// never ran. The sprites did not breathe and a camera flight never moved —
  /// it was created correctly and then simply never advanced.
  final Ticker _ticker = Ticker();
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
    final floor = _floor(size);
    final fit = ((size.width * 0.86) / (maxX - minX))
        .clamp(floor, _maxZoom);
    final fitY = ((size.height * 0.72) / (maxY - minY))
        .clamp(floor, _maxZoom);
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
  void initState() {
    super.initState();
    _ticker
      ..onTick = _onTick
      ..start();
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
    final floor = _floor(size);
    final fit = (size.width * 0.92 / w).clamp(floor, _maxZoom);
    final fitY = (size.height * 0.80 / h).clamp(floor, _maxZoom);
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

  /// The free plots the viewport covers, in tile space.
  ///
  /// The visible region is a diamond in tile coordinates, so this is its
  /// bounding box — which over-covers, and the painter culls the difference.
  List<Plot> _visiblePlots(Size size) {
    final corners = [
      _tileAt(Offset.zero, size),
      _tileAt(Offset(size.width, 0), size),
      _tileAt(Offset(0, size.height), size),
      _tileAt(Offset(size.width, size.height), size),
    ];
    var minX = double.infinity, maxX = -double.infinity;
    var minY = double.infinity, maxY = -double.infinity;
    for (final c in corners) {
      minX = math.min(minX, c.dx);
      maxX = math.max(maxX, c.dx);
      minY = math.min(minY, c.dy);
      maxY = math.max(maxY, c.dy);
    }
    return widget.web.visible(
        Rect.fromLTRB(minX, minY, maxX, maxY), widget.taken);
  }

  Plot? _plotAt(Offset local, Size size) {
    // Castles first: a castle sits ON a plot, and land that is built on is not
    // empty land however the outlines are ordered.
    if (_castleAt(local, size) != null) return null;
    final t = _tileAt(local, size);
    for (final p in _visiblePlots(size)) {
      final (x, y, w, h) = p.bounds;
      if (t.dx >= x && t.dx < x + w && t.dy >= y && t.dy < y + h) return p;
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
            // Twice the travel per notch. 1.1 squared and 0.9 squared, so a
            // notch moves exactly twice as far in the scale the zoom actually
            // works in — a multiplier, not an amount.
            _zoom = (_zoom * (e.scrollDelta.dy > 0 ? 0.81 : 1.21))
                .clamp(_floor(size), _maxZoom);
            final k = _zoom / before;
            // Zoom about the POINTER, not the middle of the window. Scaling
            // the camera about the centre means the thing you are pointing at
            // slides away as you zoom towards it, and you chase it with the
            // drag — which is most of what made the map feel awkward.
            final d = e.localPosition -
                Offset(size.width / 2, size.height / 3);
            _camera = d * (1 - k) + _camera * k;
          });
        },
        child: MouseRegion(
          onHover: (e) {
            if (_far) {
              final c = _castleAt(e.localPosition, size);
              final p = c == null ? _plotAt(e.localPosition, size) : null;
              final plotKey = p == null ? null : '${p.ring}:${p.slot}';
              if (c?.id != _hoveredCastle || plotKey != _hoveredPlot) {
                setState(() {
                  _hoveredCastle = c?.id;
                  _hoveredPlot = plotKey;
                });
              }
              return;
            }
            final r = _roomAt(e.localPosition, size);
            if (r?.id != _hovered) setState(() => _hovered = r?.id);
          },
          onExit: (_) => setState(() {
            _hovered = null;
            _hoveredCastle = null;
            _hoveredPlot = null;
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
                      (_zoomAnchor * d.scale).clamp(_floor(size), _maxZoom);
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
                    _hoveredPlot = null;
                    _flyTo(x, y, w, h, size);
                  });
                  widget.onCastleTapped?.call(c);
                  return;
                }
                // Empty land. Building is the operator's decision, so this
                // only ASKS — it does not travel there first, because flying
                // to a plot that may not get built on is a camera move you
                // did not want.
                final p = _plotAt(d.localPosition, size);
                if (p != null) {
                  setState(() => _hoveredPlot = null);
                  widget.onPlotTapped?.call(p.ring, p.slot);
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
                plots: _visiblePlots(size),
                hoveredPlot: _hoveredPlot,
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

/// A frame ticker.
///
/// It reports the FRAME's timestamp — the `Duration` the scheduler hands the
/// post-frame callback — and not a `Stopwatch`. That is the difference
/// between an animation that can be tested and one that cannot: `pump()`
/// advances the binding's clock and leaves wall time alone, so a stopwatch
/// sits still while the test believes a second has passed. The camera flight
/// looked broken for exactly that reason.
class Ticker {
  /// Deliberately takes NO callback. A constructor that wants one invites
  /// `late final Ticker _t = Ticker(_onTick)..start()`, and that is the bug
  /// this comment exists because of: `late final` is lazy, nothing else read
  /// the field, so it was never built and the clock never started. Assign
  /// [onTick] from `initState`, where it cannot be skipped.
  Ticker();

  void Function(Duration)? onTick;
  bool _running = false;

  void start() {
    if (_running) return;
    _running = true;
    _schedule();
  }

  void _schedule() {
    if (!_running) return;
    WidgetsBinding.instance.addPostFrameCallback((elapsed) {
      if (!_running) return;
      onTick?.call(elapsed);
      _schedule();
    });
  }

  void dispose() => _running = false;
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
