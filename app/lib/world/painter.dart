import 'dart:math' as math;

import 'package:flutter/material.dart';

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
  });

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

  /// The map's labels are drawn with a raw `TextPainter`, which inherits
  /// nothing from the widget tree — so the family has to be handed to it or
  /// the map ends up lettered differently from the rest of the app. `MapView`
  /// passes the theme's.
  final String? fontFamily;

  static const _wallHeight = 1.4;

  @override
  void paint(Canvas canvas, Size size) {
    canvas.save();
    canvas.translate(size.width / 2 + camera.dx, size.height / 3 + camera.dy);
    canvas.scale(zoom);

    // Painter's order. Everything on the map is sorted by depth ONCE and drawn
    // back to front — rooms and sprites together, because a sprite standing in
    // a near room must cover the far room's wall behind it. Sorting rooms and
    // then sprites separately is how an isometric scene ends up with figures
    // showing through walls.
    final items = <_Drawable>[];
    for (final room in rooms) {
      items.add(_Drawable(
        depth: isoDepth(room.position.x, room.position.y),
        paint: (c) => _room(c, room),
      ));
    }
    for (final a in agents) {
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
    final at = iso.toScreen(
        room.position.x + room.size.x / 2, room.position.y);
    _text(
      canvas,
      room.name,
      at + Offset(0, -_wallHeight * iso.tileH - 18 / zoom),
      size: 13 / zoom,
      weight: FontWeight.w600,
      centre: true,
    );
    final badge = badges[room.id] ?? 0;
    if (badge > 0) {
      final c = at + Offset(0, -_wallHeight * iso.tileH - 38 / zoom);
      canvas.drawCircle(c, 10 / zoom, Paint()..color = const Color(0xFFE23D3D));
      _text(canvas, '$badge', c - Offset(0, 8 / zoom),
          size: 11 / zoom, weight: FontWeight.bold, centre: true);
    }
  }

  // -- agents --------------------------------------------------------------

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

    final paint = Paint();
    for (var ry = 0; ry < rows.length; ry++) {
      final row = rows[ry];
      for (var rx = 0; rx < row.length; rx++) {
        final col = pal.slot(row[rx]);
        if (col == null) continue;
        paint.color = col;
        canvas.drawRect(
          Rect.fromLTWH(originX + rx * px, originY + ry * px, px, px),
          paint,
        );
      }
    }

    _text(canvas, a.name, Offset(at.dx, originY - 14 / zoom),
        size: 11 / zoom, centre: true);
    final line = a.say ?? (a.busy ? a.status : null);
    if (line != null && line.isNotEmpty) {
      _text(canvas, line, Offset(at.dx, originY - 28 / zoom),
          size: 10 / zoom, centre: true, colour: const Color(0xFFBFD8C8));
    }
  }

  // -- text ----------------------------------------------------------------

  void _text(Canvas canvas, String s, Offset at,
      {double size = 12,
      FontWeight weight = FontWeight.normal,
      bool centre = false,
      Color colour = Colors.white}) {
    final tp = TextPainter(
      text: TextSpan(
        text: s,
        style: TextStyle(
          color: colour,
          fontFamily: fontFamily,
          fontSize: size,
          fontWeight: weight,
          shadows: const [Shadow(color: Colors.black87, blurRadius: 3)],
        ),
      ),
      textDirection: TextDirection.ltr,
      maxLines: 1,
      ellipsis: '…',
    )..layout(maxWidth: 220 / zoom);
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
      old.fontFamily != fontFamily;
}

double _sin(double t) => math.sin(t);

/// One thing to draw, and how far from the camera it is.
class _Drawable {
  _Drawable({required this.depth, required this.paint});
  final double depth;
  final void Function(Canvas) paint;
}
