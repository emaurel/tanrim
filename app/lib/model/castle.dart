import 'dart:math' as math;

import 'package:flutter/painting.dart' show Rect, Offset;

import 'world.dart';

/// One running instance of a plugin, and the land it stands on.
///
/// A plugin says what a kind of work IS; a castle is one copy of it, with its
/// own rooms on the map, its own sprites and its own records. Two castles of
/// the web agency are two agencies: same trade, different work in them.
///
/// Built by the SERVER rather than inferred here. The app used to group rooms
/// by which plugin declared them, which could only ever produce one castle per
/// plugin — and, more quietly, computed nothing about where they sat. The plot
/// geometry has to match the room coordinates exactly, and the only way to be
/// sure of that is for one side to own both.
class Castle {
  Castle({
    required this.id,
    required this.pluginId,
    required this.pluginName,
    required this.name,
    required this.ring,
    required this.slot,
    required this.centre,
    required this.span,
    required this.records,
    required this.installed,
    this.rooms = const [],
  });

  final String id;
  final String pluginId;
  final String pluginName;

  /// `[PLUGIN NAME] [N]` until it is renamed.
  final String name;

  final int ring;
  final int slot;

  /// The centre of its plot, in tiles.
  final (double, double) centre;
  final double span;

  final int records;

  /// False when the plugin it is an instance of is no longer installed — the
  /// castle outlives it, so the map has to be able to say so rather than
  /// drawing an empty place with no explanation.
  final bool installed;

  final List<Room> rooms;

  /// The plot's square, in tile space.
  (double, double, double, double) get plot =>
      (centre.$1 - span / 2, centre.$2 - span / 2, span, span);

  /// The bounding box over its rooms, or the plot when it has none.
  (double, double, double, double) get bounds {
    if (rooms.isEmpty) return plot;
    var minX = double.infinity, minY = double.infinity;
    var maxX = -double.infinity, maxY = -double.infinity;
    for (final r in rooms) {
      minX = r.position.x < minX ? r.position.x : minX;
      minY = r.position.y < minY ? r.position.y : minY;
      final rx = r.position.x + r.size.x;
      final ry = r.position.y + r.size.y;
      maxX = rx > maxX ? rx : maxX;
      maxY = ry > maxY ? ry : maxY;
    }
    return (minX, minY, maxX - minX, maxY - minY);
  }

  Castle withRooms(List<Room> all) => Castle(
        id: id,
        pluginId: pluginId,
        pluginName: pluginName,
        name: name,
        ring: ring,
        slot: slot,
        centre: centre,
        span: span,
        records: records,
        installed: installed,
        // A room with no castle belongs to every castle only when there is
        // exactly one — an install that predates castles serves unscoped
        // rooms, and they have to land somewhere.
        rooms: all
            .where((r) => r.castleId == id || (r.castleId.isEmpty))
            .toList(),
      );

  static Castle fromJson(Map<String, dynamic> j) => Castle(
        id: j['id'] as String,
        pluginId: (j['plugin'] ?? '') as String,
        pluginName: (j['plugin_name'] ?? j['plugin'] ?? '') as String,
        name: (j['name'] ?? j['id']) as String,
        ring: (j['ring'] ?? 1) as int,
        slot: (j['slot'] ?? 0) as int,
        centre: (
          ((j['x'] ?? 0) as num).toDouble(),
          ((j['y'] ?? 0) as num).toDouble(),
        ),
        span: ((j['span'] ?? 64) as num).toDouble(),
        records: (j['records'] ?? 0) as int,
        installed: j['installed'] != false,
      );
}

/// The web of plots, as an equation.
///
/// The server sends these two numbers and the list of what is built on; the
/// app generates the plots its viewport actually covers. Sending PLOTS meant
/// choosing how many, and any number is wrong: too few and zooming out reveals
/// nothing new, so the web plainly stops; enough to fill a zoomed-out view and
/// one castle on ring 50 lists eight thousand pieces of empty land.
///
/// The equation is duplicated across the two languages, which is a real risk
/// and the reason `castle_test.dart` checks this side against coordinates
/// captured from the server. Everything else about a castle's position comes
/// from the server precisely so the two cannot drift; this is the one place
/// they have to agree by construction.
class Web {
  const Web({this.span = 64, this.ringSpacing = 1.6});

  final double span;
  final double ringSpacing;

  static Web fromJson(Map<String, dynamic> j) => Web(
        span: ((j['span'] ?? 64) as num).toDouble(),
        ringSpacing: ((j['ring_spacing'] ?? 1.6) as num).toDouble(),
      );

  /// Ring `n` holds `6n` plots. The hub is ring 0 and holds none.
  int slotsOn(int ring) => ring > 0 ? 6 * ring : 0;

  /// Ring 1 slot 0 is due north, so the first castle built lands at the top
  /// of the map where it is easy to find.
  (double, double) centre(int ring, int slot) {
    if (ring <= 0) return (0, 0);
    final count = slotsOn(ring);
    final angle = (2 * math.pi * (slot % count) / count) - (math.pi / 2);
    final radius = ring * span * ringSpacing;
    return (radius * math.cos(angle), radius * math.sin(angle));
  }

  /// How many rings could possibly reach a point this far from the hub.
  int ringsWithin(double radius) =>
      (radius / (span * ringSpacing)).ceil() + 1;

  /// Every free plot whose square overlaps [view], in tile space.
  ///
  /// Culled per plot rather than per ring, because the visible region is a
  /// rotated rectangle in tile space and a ring is a circle: the rings that
  /// matter at the edges of the view are not the rings that matter at its
  /// centre.
  List<Plot> visible(
    Rect view,
    Set<(int, int)> taken, {
    int cap = 600,
  }) {
    // The furthest corner of the view from the hub decides how far to look.
    final reach = [
      view.topLeft, view.topRight, view.bottomLeft, view.bottomRight,
    ].map((c) => c.distance).reduce(math.max);

    final out = <Plot>[];
    for (var ring = 1; ring <= ringsWithin(reach) && out.length < cap; ring++) {
      for (var slot = 0; slot < slotsOn(ring); slot++) {
        if (taken.contains((ring, slot))) continue;
        final (cx, cy) = centre(ring, slot);
        final box = Rect.fromCenter(
            center: Offset(cx, cy), width: span, height: span);
        if (!view.overlaps(box)) continue;
        out.add(Plot(ring: ring, slot: slot, centre: (cx, cy), span: span));
        if (out.length >= cap) break;
      }
    }
    return out;
  }
}

/// Empty land, with an outline on it.
class Plot {
  const Plot({
    required this.ring,
    required this.slot,
    required this.centre,
    required this.span,
  });

  final int ring;
  final int slot;
  final (double, double) centre;
  final double span;

  (double, double, double, double) get bounds =>
      (centre.$1 - span / 2, centre.$2 - span / 2, span, span);

  static Plot fromJson(Map<String, dynamic> j) => Plot(
        ring: (j['ring'] ?? 1) as int,
        slot: (j['slot'] ?? 0) as int,
        centre: (
          ((j['x'] ?? 0) as num).toDouble(),
          ((j['y'] ?? 0) as num).toDouble(),
        ),
        span: ((j['span'] ?? 64) as num).toDouble(),
      );
}
