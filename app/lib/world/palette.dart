import 'dart:ui';

/// One agent's colours, derived from the single colour its plugin declares.
///
/// The plugin gives a room's agent one hex value and nothing else, so every
/// other shade is computed from it — which is what keeps a new plugin's agent
/// looking like it belongs without the plugin author picking six colours.
class Palette {
  Palette(int rgb)
      : hood = _shade(rgb, -0.45),
        face = const Color(0xFFE8C39E),
        body = Color(rgb),
        trim = _shade(rgb, -0.30),
        emblem = _shade(rgb, 0.45),
        sleeve = _shade(rgb, 0.28);

  final Color hood;
  final Color face;
  final Color body;
  final Color trim;
  final Color emblem;
  final Color sleeve;

  static const eye = Color(0xFF141018);

  Color? slot(String c) => switch (c) {
        'h' || 'l' => hood,
        'f' => face,
        'e' => eye,
        'b' => body,
        't' || 'a' => trim,
        'k' => emblem,
        's' => sleeve,
        _ => null,
      };
}

/// Lighten (positive) or darken (negative) towards white or black.
Color _shade(int rgb, double amount) {
  final c = Color(rgb);
  final t = amount < 0 ? 0.0 : 255.0;
  final k = amount.abs();
  return Color.fromARGB(
    255,
    (c.r * 255 + (t - c.r * 255) * k).round().clamp(0, 255),
    (c.g * 255 + (t - c.g * 255) * k).round().clamp(0, 255),
    (c.b * 255 + (t - c.b * 255) * k).round().clamp(0, 255),
  );
}

/// A room's floor and walls, from the one colour its manifest declares.
class RoomPalette {
  RoomPalette(int rgb)
      : floor = _shade(rgb, 0.10),
        floorAlt = _shade(rgb, 0.02),
        wallTop = _shade(rgb, 0.34),
        wallLeft = _shade(rgb, -0.18),
        wallRight = _shade(rgb, -0.38),
        edge = _shade(rgb, -0.55);

  final Color floor;
  final Color floorAlt;
  final Color wallTop;
  final Color wallLeft;
  final Color wallRight;
  final Color edge;
}
