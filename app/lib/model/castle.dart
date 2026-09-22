import 'world.dart';

/// A plugin's rooms, taken together.
///
/// Each installed plugin that declares rooms of its own is one castle. An
/// EXTENSION is not: `website_recreation` adds benches to two of the web
/// agency's rooms and declares none, so it lives inside that castle rather
/// than beside it — which is exactly what it is.
///
/// Today there is one. The shape exists because the world is meant to hold
/// several, and because zooming out far enough should show you the estate
/// rather than a wall of unreadable tiles.
class Castle {
  Castle({
    required this.pluginId,
    required this.name,
    required this.rooms,
  });

  final String pluginId;
  final String name;
  final List<Room> rooms;

  /// The bounding box over its rooms, in tile space.
  (double, double, double, double) get bounds {
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

  /// Build one castle per plugin that declares rooms.
  ///
  /// `byPlugin` is `/plugins` → `{pluginId: [roomId]}`. A room no plugin
  /// claims still gets a castle of its own rather than vanishing: an
  /// unclaimed room is a bug worth SEEING, not hiding.
  static List<Castle> group(
    List<Room> rooms,
    Map<String, List<String>> byPlugin,
    Map<String, String> pluginNames,
  ) {
    final out = <Castle>[];
    final claimed = <String>{};
    for (final entry in byPlugin.entries) {
      final mine =
          rooms.where((r) => entry.value.contains(r.id)).toList();
      if (mine.isEmpty) continue;
      claimed.addAll(mine.map((r) => r.id));
      out.add(Castle(
        pluginId: entry.key,
        name: pluginNames[entry.key] ?? entry.key,
        rooms: mine,
      ));
    }
    final orphans = rooms.where((r) => !claimed.contains(r.id)).toList();
    if (orphans.isNotEmpty) {
      out.add(Castle(
          pluginId: '', name: 'unclaimed', rooms: orphans));
    }
    return out;
  }
}
