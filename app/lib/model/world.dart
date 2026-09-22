/// The shapes the backend serves.
///
/// Deliberately thin: these mirror `GET /rooms` and the WebSocket payloads and
/// nothing more. The environment is plugin-driven — rooms, agents and stages
/// all arrive from whatever is installed — so the app must never hardcode a
/// room id, a role or a stage name. Anything it cannot learn from the API is
/// something the API should be teaching it.
class Vec2 {
  const Vec2(this.x, this.y);
  final double x;
  final double y;

  static Vec2 from(Map<String, dynamic>? j, [Vec2 fallback = const Vec2(0, 0)]) {
    if (j == null) return fallback;
    final x = (j['x'] ?? j['w'] ?? 0).toDouble();
    final y = (j['y'] ?? j['h'] ?? 0).toDouble();
    return Vec2(x, y);
  }
}

class Workbench {
  Workbench({
    required this.id,
    required this.name,
    required this.job,
    required this.stages,
    this.position,
    this.size,
  });

  final String id;
  final String name;
  final String job;
  final List<String> stages;

  /// Tile offsets RELATIVE to the room. Null when the backend did not lay it
  /// out, which is legitimate — geometry is computed server-side and a bench
  /// may simply not have one yet.
  final Vec2? position;
  final Vec2? size;

  static Workbench fromJson(Map<String, dynamic> j) => Workbench(
        id: j['id'] as String,
        name: (j['name'] ?? j['id']) as String,
        job: (j['job'] ?? '') as String,
        stages: ((j['stages'] ?? []) as List).cast<String>(),
        position: j['position'] == null
            ? null
            : Vec2.from(j['position'] as Map<String, dynamic>),
        size: j['size'] == null
            ? null
            : Vec2.from(j['size'] as Map<String, dynamic>),
      );
}

class AgentSpec {
  AgentSpec({
    required this.id,
    required this.name,
    required this.role,
    required this.color,
    this.station,
  });

  final String id;
  final String name;

  /// The one-line description, not the role id — that is `id`. The backend
  /// field is called `role` for historical reasons.
  final String role;
  final int color;
  final String? station;

  static AgentSpec fromJson(Map<String, dynamic> j) => AgentSpec(
        id: j['id'] as String,
        name: (j['name'] ?? j['id']) as String,
        role: (j['role'] ?? '') as String,
        color: parseColor(j['color'] as String?),
        station: j['station'] as String?,
      );
}

class Room {
  Room({
    required this.id,
    required this.name,
    required this.purpose,
    required this.position,
    required this.size,
    required this.color,
    required this.agents,
    required this.workbenches,
    required this.tools,
    required this.skills,
  });

  final String id;
  final String name;
  final String purpose;

  /// Tile coordinates on the shared grid.
  final Vec2 position;
  final Vec2 size;
  final int color;
  final List<AgentSpec> agents;
  final List<Workbench> workbenches;
  final List<String> tools;
  final List<String> skills;

  static Room fromJson(Map<String, dynamic> j) => Room(
        id: j['id'] as String,
        name: (j['name'] ?? j['id']) as String,
        purpose: (j['purpose'] ?? '') as String,
        position: Vec2.from(j['position'] as Map<String, dynamic>?),
        size: Vec2.from(j['size'] as Map<String, dynamic>?, const Vec2(12, 8)),
        color: parseColor(j['color'] as String?),
        agents: ((j['agents'] ?? []) as List)
            .map((a) => AgentSpec.fromJson(a as Map<String, dynamic>))
            .toList(),
        workbenches: ((j['workbenches'] ?? []) as List)
            .map((b) => Workbench.fromJson(b as Map<String, dynamic>))
            .toList(),
        tools: ((j['tools'] ?? []) as List).cast<String>(),
        skills: ((j['skills'] ?? []) as List).cast<String>(),
      );
}

/// A worker's live position and status, from the `agent_update` wire event.
class AgentState {
  AgentState({
    required this.id,
    required this.name,
    required this.roomId,
    required this.x,
    required this.y,
    required this.color,
    this.busy = false,
    this.status = '',
    this.workbench,
    this.say,
    this.recordId,
  });

  final String id;
  final String name;
  final String roomId;

  /// Tile coordinates, absolute on the shared grid.
  final double x;
  final double y;
  final int color;
  final bool busy;
  final String status;
  final String? workbench;
  final String? say;
  final String? recordId;

  static AgentState fromJson(Map<String, dynamic> j) => AgentState(
        id: j['id'] as String,
        name: (j['name'] ?? j['id']) as String,
        roomId: (j['room_id'] ?? j['room'] ?? '') as String,
        x: (j['x'] ?? 0).toDouble(),
        y: (j['y'] ?? 0).toDouble(),
        color: parseColor(j['color'] as String?),
        busy: (j['busy'] ?? false) as bool,
        status: (j['status'] ?? '') as String,
        workbench: j['workbench'] as String?,
        say: j['say'] as String?,
        // `lead_id` is the wire format and deliberately unchanged; the app
        // calls it what the environment calls it internally.
        recordId: (j['record_id'] ?? j['lead_id']) as String?,
      );

  AgentState copyWith({double? x, double? y}) => AgentState(
        id: id,
        name: name,
        roomId: roomId,
        x: x ?? this.x,
        y: y ?? this.y,
        color: color,
        busy: busy,
        status: status,
        workbench: workbench,
        say: say,
        recordId: recordId,
      );
}

/// `"#bcd35f"` -> 0xFFBCD35F. Anything unparseable becomes white rather than
/// throwing: a bad colour should cost a sprite its tint, not the whole map.
int parseColor(String? hex, [int fallback = 0xFFFFFFFF]) {
  if (hex == null || hex.isEmpty) return fallback;
  var s = hex.trim();
  if (s.startsWith('#')) s = s.substring(1);
  if (s.length == 6) s = 'FF$s';
  return int.tryParse(s, radix: 16) ?? fallback;
}
