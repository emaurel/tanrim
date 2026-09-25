import 'dart:ui';

/// The isometric projection.
///
/// The backend lays rooms out on a plain tile grid — a room is a rectangle at
/// `position` of `size` tiles — and knows nothing about how it is drawn. So the
/// whole difference between the old top-down map and this one lives here: a
/// tile (x, y) becomes a 2:1 diamond on screen.
///
///     screen.x = (x - y) * tileW / 2
///     screen.y = (x + y) * tileH / 2
///
/// `tileH` is exactly half `tileW`, which is what makes the diamonds meet
/// without seams. Anything else leaves hairlines between floor tiles that no
/// amount of overdraw hides.
class Iso {
  const Iso({this.tileW = 64}) : tileH = tileW / 2;

  final double tileW;
  final double tileH;

  /// Tile space -> screen space, before camera pan and zoom.
  Offset toScreen(double x, double y) =>
      Offset((x - y) * tileW / 2, (x + y) * tileH / 2);

  /// Screen space -> tile space. Needed for hit-testing: the operator clicks a
  /// diamond and expects the room under it, and inverting the projection is
  /// the only honest way to know which one that is.
  Offset toTile(Offset p) {
    final a = p.dx / (tileW / 2);
    final b = p.dy / (tileH / 2);
    return Offset((a + b) / 2, (b - a) / 2);
  }

  /// The four corners of one tile's diamond, clockwise from the top.
  Path tileDiamond(double x, double y) {
    final top = toScreen(x, y);
    final right = toScreen(x + 1, y);
    final bottom = toScreen(x + 1, y + 1);
    final left = toScreen(x, y + 1);
    return Path()
      ..moveTo(top.dx, top.dy)
      ..lineTo(right.dx, right.dy)
      ..lineTo(bottom.dx, bottom.dy)
      ..lineTo(left.dx, left.dy)
      ..close();
  }

  /// The diamond covering a whole rectangle of tiles, for a room floor. One
  /// path rather than w*h of them: a twelve-by-eight room is 96 tiles, and at
  /// twelve rooms that is over a thousand paths per frame for a shape with
  /// four corners.
  Path rectDiamond(double x, double y, double w, double h) {
    final top = toScreen(x, y);
    final right = toScreen(x + w, y);
    final bottom = toScreen(x + w, y + h);
    final left = toScreen(x, y + h);
    return Path()
      ..moveTo(top.dx, top.dy)
      ..lineTo(right.dx, right.dy)
      ..lineTo(bottom.dx, bottom.dy)
      ..lineTo(left.dx, left.dy)
      ..close();
  }

  /// A wall standing on the edge from (x1,y1) to (x2,y2), `height` tiles tall.
  /// Walls are what make an isometric scene read as rooms rather than as a
  /// pattern on a floor.
  Path wall(double x1, double y1, double x2, double y2, double height) {
    final lift = height * tileH;
    final a = toScreen(x1, y1);
    final b = toScreen(x2, y2);
    return Path()
      ..moveTo(a.dx, a.dy)
      ..lineTo(b.dx, b.dy)
      ..lineTo(b.dx, b.dy - lift)
      ..lineTo(a.dx, a.dy - lift)
      ..close();
  }
}

/// Painter's order for an isometric scene: things further from the viewer are
/// drawn first. Depth is `x + y` — the axis running away from the camera — so
/// a sprite in front of a wall is drawn after it and overlaps correctly.
double isoDepth(double x, double y) => x + y;
