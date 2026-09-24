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

/// Empty land, with an outline on it.
///
/// Served rather than computed for the same reason as a castle's centre: the
/// rooms of whatever gets built here are positioned from this geometry, and
/// two sides working it out separately is two sides that can disagree.
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
