import 'dart:math' as math;

import 'dart:ui' show Picture, PictureRecorder;

import 'package:flutter/material.dart';

import '../model/castle.dart';
import '../model/world.dart';
import 'iso.dart';
import 'palette.dart';
import 'sprite.dart';

/// Draws the whole world in one pass.
///
/// A `CustomPainter` rather than a game engine, because there is no game here:
/// a dozen rooms, a dozen sprites and no physics, redrawn when something
/// actually changes. The web build reached for Phaser and used 37 of its calls.
class WorldPainter extends CustomPainter {
  WorldPainter({
    required this.rooms,
    required this.agents,
    required this.camera,
    required this.zoom,
    required this.iso,
    required this.tick,
    this.hoveredRoom,
    this.selectedRoom,
    this.badges = const {},
    this.fontFamily,
    this.castles = const [],
    this.castleBadges = const {},
    this.hoveredCastle,
    this.plots = const [],
    this.hoveredPlot,
  });

  /// Below this zoom a room is a few pixels across, so the map stops drawing
  /// rooms and draws one block per castle instead. Zooming out should show you
  /// the estate, not a smaller illegible copy of the same thing.
  ///
  /// Three times further out than it was. The layout step turned out to be the
  /// one you spend time in — it is where you compare castles — and it was only
  /// a notch and a half wide before the blocks took over.
  static const farZoom = 0.38 / 3;

  final List<Room> rooms;
  final List<AgentState> agents;

  /// Camera offset in SCREEN pixels, already zoom-independent.
  final Offset camera;
  final double zoom;
  final Iso iso;

  /// Animation clock, in seconds. Drives the walk cycle and the torch flicker.
  final double tick;
  final String? hoveredRoom;
  final String? selectedRoom;

  /// room id -> pending approval count, drawn as a badge.
  final Map<String, int> badges;

  /// Empty land. Outlined rather than filled, because the point of drawing it
  /// is that there is nothing there yet and something could be.
  final List<Plot> plots;

  /// `ring:slot` of the plot under the cursor.
  final String? hoveredPlot;

  /// The map's labels are drawn with a raw `TextPainter`, which inherits
  /// nothing from the widget tree — so the family has to be handed to it or
  /// the map ends up lettered differently from the rest of the app. `MapView`
  /// passes the theme's.
  final String? fontFamily;

  /// One per installed plugin that declares rooms. Drawn INSTEAD of the rooms
  /// when zoomed out past [farZoom].
  final List<Castle> castles;

  /// plugin id -> pending approvals across all its rooms.
  final Map<String, int> castleBadges;
  final String? hoveredCastle;

  /// Far enough out that rooms are illegible and the estate is the useful
  /// picture.
  bool get far => zoom < farZoom;

  /// Close enough for a label to be worth reading.
  ///
  /// The middle step. Between this and [farZoom] the rooms are still drawn —
  /// the LAYOUT is the useful thing at that distance, and it is what tells one
  /// castle from another — but nothing is lettered, because text this small is
  /// grey fuzz over the thing you are looking at.
  ///
  /// Set where [farZoom] used to be: names were going at 0.62, which is barely
  /// zoomed out at all and took the labels away while they were still
  /// perfectly readable.
  static const labelZoom = 0.38;

  bool get labelled => zoom >= labelZoom;

  static const _wallHeight = 1.4;

  /// What is on screen, in world coordinates, with a margin.
  ///
  /// Everything is culled against this. The web of plots is infinite by
  /// construction, and even the bounded slice the server offers is 120
  /// diamonds most of which are nowhere near the camera — drawing them all is
  /// work per frame that buys nothing, and it gets worse the further out you
  /// build.
  Rect _view = Rect.largest;

  bool _onScreen(double x, double y, double w, double h) {
    // Isometric: the four corners of a tile rect are not a screen rect, so
    // the extremes come from all four. Written out rather than through a list
    // and `reduce` — this runs per item per frame, and the allocations were
    // more expensive than the arithmetic.
    //
    // screen.x = (x - y) * tileW / 2, so x is extreme at the corners where
    // (x - y) is, and screen.y = (x + y) * tileH / 2 likewise.
    final halfW = iso.tileW / 2, halfH = iso.tileH / 2;
    final left = (x - (y + h)) * halfW;
    final right = ((x + w) - y) * halfW;
    final top = (x + y) * halfH;
    final bottom = ((x + w) + (y + h)) * halfH;
    // Walls and labels hang above the diamond.
    return _view.overlaps(Rect.fromLTRB(
        left, top - _wallHeight * iso.tileH - 40, right, bottom + 20));
  }

  @override
  void paint(Canvas canvas, Size size) {
    // The visible world, before the canvas transform, plus a margin so
    // something half off the edge is still drawn rather than popping in.
    const margin = 120.0;
    _view = Rect.fromLTRB(
      (-size.width / 2 - camera.dx) / zoom - margin,
      (-size.height / 3 - camera.dy) / zoom - margin,
      (size.width / 2 - camera.dx) / zoom + margin,
      (size.height * 2 / 3 - camera.dy) / zoom + margin,
    );

    canvas.save();
    canvas.translate(size.width / 2 + camera.dx, size.height / 3 + camera.dy);
    canvas.scale(zoom);

    // Ground first, at every distance. The plots used to appear only in the
    // estate view, so the step where you can see a castle's layout AND the
    // land around it — which is the step you would actually build from —
    // showed no land at all.
    _plots(canvas);

    if (far) {
      _estate(canvas);
      canvas.restore();
      return;
    }

    // Painter's order. Everything on the map is sorted by depth ONCE and drawn
    // back to front — rooms and sprites together, because a sprite standing in
    // a near room must cover the far room's wall behind it. Sorting rooms and
    // then sprites separately is how an isometric scene ends up with figures
    // showing through walls.
    final items = <_Drawable>[];
    for (final room in rooms) {
      if (!_onScreen(room.position.x, room.position.y,
                     room.size.x, room.size.y)) {
        continue;
      }
      items.add(_Drawable(
        depth: isoDepth(room.position.x, room.position.y),
        paint: (c) => _room(c, room),
      ));
    }
    for (final a in agents) {
      if (!_onScreen(a.x, a.y, 1, 1)) continue;
      items.add(_Drawable(
        // +0.5 so a sprite standing on a room's far edge draws after its wall.
        depth: isoDepth(a.x, a.y) + 0.5,
        paint: (c) => _agent(c, a),
      ));
    }
    items.sort((a, b) => a.depth.compareTo(b.depth));
    for (final it in items) {
      it.paint(canvas);
    }

    canvas.restore();
  }

  // -- empty land ------------------------------------------------------------

  /// An outline per free plot, with a plus in it.
  ///
  /// Drawn UNDER the castles and before them, so a castle always covers its
  /// own ground rather than an outline showing through. Kept to a dashed edge
  /// and a faint fill: filled plots read as buildings that are already there,
  /// which is the one thing they must not look like.
  void _plots(Canvas canvas) {
    // The land a castle stands ON, drawn under it. Without it a castle had no
    // edge you could click once you were inside it — the rooms were clickable
    // and the gaps between them were nothing at all.
    for (final c in castles) {
      final (x, y, w, h) = c.plot;
      if (!_onScreen(x, y, w, h)) continue;
      final hot = c.id == hoveredCastle;
      final face = iso.rectDiamond(x, y, w, h);
      canvas.drawPath(
          face,
          Paint()
            ..color = hot
                ? const Color(0x1EFFFFFF)
                : const Color(0x0AFFFFFF));
      canvas.drawPath(
          face,
          Paint()
            ..color = hot ? const Color(0x66FFFFFF) : const Color(0x22FFFFFF)
            ..style = PaintingStyle.stroke
            ..strokeWidth = (hot ? 2.0 : 1.2) / zoom);
    }

    for (final p in plots) {
      final (x, y, w, h) = p.bounds;
      if (!_onScreen(x, y, w, h)) continue;
      final hot = hoveredPlot == '${p.ring}:${p.slot}';
      final face = iso.rectDiamond(x, y, w, h);

      canvas.drawPath(
          face,
          Paint()
            ..color = hot
                ? const Color(0x2AFFFFFF)
                : const Color(0x12FFFFFF));
      canvas.drawPath(
          face,
          Paint()
            ..color = hot ? const Color(0x99FFFFFF) : const Color(0x44FFFFFF)
            ..style = PaintingStyle.stroke
            ..strokeWidth = (hot ? 2.4 : 1.4) / zoom);

      if (hot) {
        final c = iso.toScreen(x + w / 2, y + h / 2);
        final arm = 9 / zoom;
        final pen = Paint()
          ..color = Colors.white
          ..strokeWidth = 2.4 / zoom
          ..strokeCap = StrokeCap.round;
        canvas.drawLine(c.translate(-arm, 0), c.translate(arm, 0), pen);
        canvas.drawLine(c.translate(0, -arm), c.translate(0, arm), pen);
        _text(canvas, 'build here', c.translate(0, 22 / zoom),
            size: 12 / zoom, centre: true, colour: Colors.white70);
      }
    }
  }

  // -- the estate, seen from far off ----------------------------------------

  /// One pale block per castle, with its name and what is waiting in it.
  ///
  /// Deliberately plain. At this distance the question is "where is the work
  /// and who has something waiting", and twelve coloured rooms with
  /// unreadable labels answer neither.
  void _estate(Canvas canvas) {
    final sorted = [...castles]..sort((a, b) {
        final (ax, ay, _, _) = a.plot;
        final (bx, by, _, _) = b.plot;
        return isoDepth(ax, ay).compareTo(isoDepth(bx, by));
      });

    for (final c in sorted) {
      // The PLOT, not the rooms' bounding box. The block is the piece of land
      // the castle stands on, so it lines up with the outlines around it and
      // with what you click — a block the size of the rooms was a different
      // shape from the thing it represented.
      final (x, y, w, h) = c.plot;
      if (!_onScreen(x, y, w, h)) continue;
      final hot = c.id == hoveredCastle;
      final face = iso.rectDiamond(x, y, w, h);

      // A low slab rather than a flat diamond: it still reads as a place.
      const lift = 1.2;
      canvas.drawPath(
          iso.wall(x, y + h, x + w, y + h, lift),
          Paint()..color = const Color(0xFF6E7687));
      canvas.drawPath(
          iso.wall(x + w, y, x + w, y + h, lift),
          Paint()..color = const Color(0xFF585F6D));

      // Green while something is running in it, pale otherwise. At this
      // distance the block IS the castle, so its colour is the only thing
      // that can say the place is busy without being read.
      final top = face.shift(Offset(0, -lift * iso.tileH));
      canvas.drawPath(
          top,
          Paint()
            ..color = c.working
                ? (hot ? const Color(0xFFBFF0CC) : const Color(0xFF8FD9A6))
                : (hot ? Colors.white : const Color(0xFFE7EAF0)));
      canvas.drawPath(
          top,
          Paint()
            ..color = const Color(0xFF2A2F3A)
            ..style = PaintingStyle.stroke
            ..strokeWidth = 1.5 / zoom);

      final centre = iso.toScreen(x + w / 2, y + h / 2)
          .translate(0, -lift * iso.tileH);
      _text(canvas, c.name, centre.translate(0, -10 / zoom),
          size: 15 / zoom,
          weight: FontWeight.w700,
          centre: true,
          // Green too when it is working, and the outline stays white either
          // way — it is what keeps the name readable over whatever is behind.
          colour: c.working
              ? const Color(0xFF14602F)
              : const Color(0xFF12141A),
          halo: Colors.white);
      _text(
          canvas,
          c.installed
              ? (c.working
                  ? 'working · ${c.records} records'
                  : '${c.rooms.length} rooms · ${c.records} records')
              : 'plugin not installed',
          centre.translate(0, 8 / zoom),
          size: 11 / zoom,
          centre: true,
          colour: !c.installed
              ? const Color(0xFF9A4B2F)
              : (c.working
                  ? const Color(0xFF2F7A4A)
                  : const Color(0xFF5A6272)));

      final waiting = castleBadges[c.id] ?? 0;
      if (waiting > 0) {
        final at = centre.translate(0, 30 / zoom);
        canvas.drawCircle(
            at, 12 / zoom, Paint()..color = const Color(0xFFE23D3D));
        _text(canvas, '$waiting', at.translate(0, -9 / zoom),
            size: 13 / zoom, weight: FontWeight.bold, centre: true);
      }
    }
  }

  // -- rooms ---------------------------------------------------------------

  void _room(Canvas canvas, Room room) {
    final p = RoomPalette(room.color);
    final x = room.position.x;
    final y = room.position.y;
    final w = room.size.x;
    final h = room.size.y;

    // Floor: one diamond for the whole room, then a checker over it. Drawing
    // 96 separate tiles per room cost more than the texture was worth.
    canvas.drawPath(
        iso.rectDiamond(x, y, w, h), Paint()..color = p.floor);
    final alt = Paint()..color = p.floorAlt;
    for (var ty = 0; ty < h; ty++) {
      for (var tx = 0; tx < w; tx++) {
        if ((tx + ty).isEven) continue;
        canvas.drawPath(iso.tileDiamond(x + tx, y + ty), alt);
      }
    }

    // Two walls, on the far edges, so the room reads as a box you look into.
    // The near two are left off deliberately — drawn, they hide the floor and
    // everyone standing on it.
    canvas.drawPath(iso.wall(x, y, x + w, y, _wallHeight),
        Paint()..color = p.wallRight);
    canvas.drawPath(iso.wall(x, y, x, y + h, _wallHeight),
        Paint()..color = p.wallLeft);

    // The lip along the top of each wall, which is what gives them thickness.
    final cap = Paint()
      ..color = p.wallTop
      ..strokeWidth = 2 / zoom
      ..style = PaintingStyle.stroke;
    final lift = Offset(0, -_wallHeight * iso.tileH);
    canvas.drawLine(iso.toScreen(x, y) + lift,
        iso.toScreen(x + w, y) + lift, cap);
    canvas.drawLine(iso.toScreen(x, y) + lift,
        iso.toScreen(x, y + h) + lift, cap);

    final outline = Paint()
      ..color = room.id == selectedRoom
          ? Colors.white
          : room.id == hoveredRoom
              ? Colors.white70
              : p.edge
      ..style = PaintingStyle.stroke
      ..strokeWidth = (room.id == selectedRoom ? 2.5 : 1.2) / zoom;
    canvas.drawPath(iso.rectDiamond(x, y, w, h), outline);

    for (final bench in room.workbenches) {
      _bench(canvas, room, bench, p);
    }
    _roomLabel(canvas, room);
  }

  void _bench(Canvas canvas, Room room, Workbench bench, RoomPalette p) {
    final bp = bench.position;
    final bs = bench.size;
    // Geometry is computed server-side; a bench without it is simply not
    // placed yet, and skipping it is better than inventing a position.
    if (bp == null || bs == null) return;

    final x = room.position.x + bp.x;
    final y = room.position.y + bp.y;
    const h = 0.42;

    canvas.drawPath(
        iso.rectDiamond(x, y, bs.x, bs.y),
        Paint()..color = p.wallLeft.withValues(alpha: 0.85));
    // The top face, lifted, so a bench looks like a surface and not a stain.
    final top = iso.rectDiamond(x, y, bs.x, bs.y)
        .shift(Offset(0, -h * iso.tileH));
    canvas.drawPath(top, Paint()..color = p.wallTop);
    canvas.drawPath(
        top,
        Paint()
          ..color = p.edge
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1 / zoom);
  }

  void _roomLabel(Canvas canvas, Room room) {
    // The middle of the floor, not the top of the back wall. On the wall the
    // name sat over whatever room was behind it and read as belonging to
    // that one; in the middle it is unambiguously this room's.
    final at = iso.toScreen(
        room.position.x + room.size.x / 2, room.position.y + room.size.y / 2);
    if (labelled) {
      _text(
        canvas,
        room.name,
        at + Offset(0, -6 / zoom),
        size: 13 / zoom,
        weight: FontWeight.w600,
        centre: true,
        // Outlined rather than shadowed: it lies on a floor that may be any
        // colour, and a blur reads as smudge where an outline reads as a
        // label. Black, because the floors are light enough to need it.
        halo: Colors.black,
      );
    }
    // The badge stays at every distance. It is the one thing on a room that
    // means "come here", and losing it is how you stop noticing an approval
    // simply by having zoomed out.
    final badge = badges[room.id] ?? 0;
    if (badge > 0) {
      final c = at + Offset(0, -26 / zoom);
      canvas.drawCircle(c, 10 / zoom, Paint()..color = const Color(0xFFE23D3D));
      _text(canvas, '$badge', c - Offset(0, 8 / zoom),
          size: 11 / zoom, weight: FontWeight.bold, centre: true);
    }
  }

  // -- agents --------------------------------------------------------------

  /// One figure, recorded once per pose and colour and replayed thereafter.
  ///
  /// Recorded at ONE art pixel per unit, so the caller scales it — a cache
  /// keyed on the zoom as well would miss on every frame of a zoom, which is
  /// exactly when there is least to spare.
  static final Map<String, Picture> _figures = {};

  static Picture _figure(
      Pose pose, Object colour, Palette pal, List<String> rows) {
    final key = '${pose.index}|$colour';
    final hit = _figures[key];
    if (hit != null) return hit;

    final recorder = PictureRecorder();
    final canvas = Canvas(recorder);
    final paint = Paint();
    for (var ry = 0; ry < rows.length; ry++) {
      final row = rows[ry];
      for (var rx = 0; rx < row.length; rx++) {
        final col = pal.slot(row[rx]);
        if (col == null) continue;
        paint.color = col;
        canvas.drawRect(
          Rect.fromLTWH(rx.toDouble(), ry.toDouble(), 1, 1),
          paint,
        );
      }
    }
    final pic = recorder.endRecording();
    _figures[key] = pic;
    return pic;
  }

  void _agent(Canvas canvas, AgentState a) {
    final at = iso.toScreen(a.x, a.y);
    final pal = Palette(a.color);

    // Breathing, and a walk cycle only while busy. A still room of sprites all
    // bobbing in lockstep reads as a screensaver, so the phase is offset per
    // agent by its id.
    final phase = a.id.hashCode % 100 / 100 * 6.283;
    final bob = a.busy ? 0.0 : (1.2 * (0.5 + 0.5 * _sin(tick * 1.6 + phase)));
    final pose = a.busy
        ? (a.workbench != null
            ? (_sin(tick * 3 + phase) > 0 ? Pose.workDown : Pose.workUp)
            : (_sin(tick * 6 + phase) > 0 ? Pose.step : Pose.stand))
        : Pose.stand;

    const px = 2.0; // one art pixel, in world units
    final rows = posture(pose);
    final originX = at.dx - spriteWidth * px / 2;
    final originY = at.dy - rows.length * px - bob;

    // The shadow, so the figure sits ON the floor rather than floating.
    canvas.drawOval(
      Rect.fromCenter(center: at, width: 11 * px, height: 5 * px),
      Paint()..color = Colors.black.withValues(alpha: 0.30),
    );

    // One `drawPicture` instead of one `drawRect` per art pixel.
    //
    // This was most of the lag. A figure is about 11x16 art pixels, so drawing
    // it a pixel at a time is ~150 draw calls per sprite per frame — with
    // twenty-nine agents on the map, five thousand calls a frame, three
    // hundred thousand a second, to draw figures that change between four
    // fixed poses.
    canvas.save();
    canvas.translate(originX, originY);
    canvas.scale(px);
    canvas.drawPicture(_figure(pose, a.color, pal, rows));
    canvas.restore();

    if (!labelled) return;
    _text(canvas, a.name, Offset(at.dx, originY - 14 / zoom),
        size: 11 / zoom, centre: true);
    final line = a.say ?? (a.busy ? a.status : null);
    if (line != null && line.isNotEmpty) {
      _text(canvas, line, Offset(at.dx, originY - 28 / zoom),
          size: 10 / zoom, centre: true, colour: const Color(0xFFBFD8C8));
    }
  }

  // -- text ----------------------------------------------------------------

  /// Laid-out text, kept between frames.
  ///
  /// This was the lag. Every label built a `TextPainter` and laid it out on
  /// every frame — around fifty of them at 60fps, three thousand text layouts
  /// a second, to draw words that had not changed. Laying out text is the
  /// expensive part of drawing it, and none of it was being reused.
  ///
  /// Keyed on everything that changes the result, with the size rounded: the
  /// font size is `13 / zoom`, so it is constant whenever the camera is still
  /// — which is when you are looking at the map rather than moving it.
  static final Map<String, TextPainter> _laidOut = {};
  static const _maxLaidOut = 600;

  TextPainter _measured(String s, double size, FontWeight weight, Color colour,
      Color? halo, double wrap) {
    final key = '$s|${size.toStringAsFixed(2)}|${weight.value}|'
        '${colour.toARGB32()}|${halo?.toARGB32() ?? 0}|'
        '${wrap.toStringAsFixed(0)}|$fontFamily';
    final hit = _laidOut[key];
    if (hit != null) return hit;

    if (_laidOut.length > _maxLaidOut) _laidOut.clear();
    final tp = TextPainter(
      text: TextSpan(
        text: s,
        style: TextStyle(
          color: halo == null ? colour : null,
          foreground: halo == null ? null : (Paint()..color = colour),
          fontFamily: fontFamily,
          fontSize: size,
          fontWeight: weight,
          shadows: halo == null
              ? const [Shadow(color: Colors.black87, blurRadius: 3)]
              : null,
        ),
      ),
      textDirection: TextDirection.ltr,
      maxLines: 1,
      ellipsis: '…',
    )..layout(maxWidth: wrap);
    _laidOut[key] = tp;
    return tp;
  }

  void _text(Canvas canvas, String s, Offset at,
      {double size = 12,
      FontWeight weight = FontWeight.normal,
      bool centre = false,
      Color colour = Colors.white,
      Color? halo}) {
    final wrap = 220 / zoom;
    // The outline is a second pass in a heavier stroked style UNDER the fill,
    // rather than a shadow: a castle's name sits on a pale block and over
    // whatever the map draws behind it, and a blur reads as smudge where an
    // outline reads as a label.
    if (halo != null) {
      final under = _measured(s, size, weight, halo, halo, wrap);
      final stroke = Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 3.5 / zoom
        ..strokeJoin = StrokeJoin.round
        ..color = halo;
      final outlined = TextPainter(
        text: TextSpan(
          text: s,
          style: TextStyle(
            foreground: stroke,
            fontFamily: fontFamily,
            fontSize: size,
            fontWeight: weight,
          ),
        ),
        textDirection: TextDirection.ltr,
        maxLines: 1,
        ellipsis: '…',
      )..layout(maxWidth: wrap);
      outlined.paint(
          canvas, centre ? at - Offset(under.width / 2, 0) : at);
    }
    final tp = _measured(s, size, weight, colour, halo, wrap);
    tp.paint(canvas, centre ? at - Offset(tp.width / 2, 0) : at);
  }

  @override
  bool shouldRepaint(WorldPainter old) =>
      old.tick != tick ||
      old.rooms != rooms ||
      old.agents != agents ||
      old.camera != camera ||
      old.zoom != zoom ||
      old.hoveredRoom != hoveredRoom ||
      old.selectedRoom != selectedRoom ||
      old.badges != badges ||
      old.fontFamily != fontFamily ||
      old.castles != castles ||
      old.castleBadges != castleBadges ||
      old.hoveredCastle != hoveredCastle;
}

double _sin(double t) => math.sin(t);

/// One thing to draw, and how far from the camera it is.
class _Drawable {
  _Drawable({required this.depth, required this.paint});
  final double depth;
  final void Function(Canvas) paint;
}
