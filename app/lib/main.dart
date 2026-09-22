import 'package:flutter/material.dart';

import 'api/client.dart';
import 'api/live.dart';
import 'model/approval.dart';
import 'model/castle.dart';
import 'model/record.dart';
import 'model/world.dart';
import 'ui/approvals.dart';
import 'ui/board.dart';
import 'ui/map_view.dart';
import 'ui/room_panel.dart';
import 'ui/settings.dart';

void main() => runApp(const TanrimApp());

class TanrimApp extends StatelessWidget {
  const TanrimApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Tanrim',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        brightness: Brightness.dark,
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF7AD7D7),
          brightness: Brightness.dark,
        ),
        scaffoldBackgroundColor: const Color(0xFF12141A),
      ),
      home: const WorldPage(),
    );
  }
}

class WorldPage extends StatefulWidget {
  const WorldPage({super.key});

  @override
  State<WorldPage> createState() => _WorldPageState();
}

class _WorldPageState extends State<WorldPage> {
  /// Where the environment is. One field, because the app is meant to point at
  /// a server the operator chooses — and later at one castle among several.
  final _server = TextEditingController(text: 'http://127.0.0.1:8765');

  Api? _api;
  Live? _live;

  List<Room> _rooms = const [];
  Map<String, int> _badges = const {};
  String? _selected;
  String _state = 'not connected';
  bool _connected = false;

  List<WorkRecord> _records = const [];
  List<String> _stages = const [];
  List<String> _deadStages = const [];
  Map<String, int> _counts = const {};
  String? _selectedRecord;

  List<Approval> _approvals = const [];

  /// Which pane the right-hand column is showing.
  _Pane _pane = _Pane.board;

  /// The right column folds away. The map is the thing worth looking at when
  /// nothing needs deciding, and on a narrow window the panel takes most of it.
  bool _panelOpen = true;

  List<Castle> _castles = const [];
  List<Map<String, dynamic>> _plugins = const [];
  List<String> _kinds = const [];

  @override
  void initState() {
    super.initState();
    _connect();
  }

  @override
  void dispose() {
    _live?.close();
    _api?.close();
    _server.dispose();
    super.dispose();
  }

  Future<void> _connect() async {
    _live?.close();
    _api?.close();
    setState(() {
      _state = 'connecting…';
      _connected = false;
      _rooms = const [];
    });

    final api = Api(_server.text.trim().replaceAll(RegExp(r'/+$'), ''));
    _api = api;
    try {
      final rooms = (await api.get('/rooms') as List)
          .map((r) => Room.fromJson(r as Map<String, dynamic>))
          .toList();
      setState(() {
        _rooms = rooms;
        _state = '${rooms.length} rooms';
        _connected = true;
      });
      await _loadPlugins();
      await _loadApprovals();
      await _loadBoard();
    } catch (e) {
      setState(() => _state = e is ApiError ? e.message : '$e');
      return;
    }

    final live = Live(api.socket);
    _live = live;
    live.events.listen((e) async {
      switch (e.kind) {
        case LiveKind.agentsChanged:
          setState(() {});
        case LiveKind.approvalsChanged:
          await _loadApprovals();
          await _loadBoard();
        case LiveKind.disconnected:
          setState(() => _state = 'reconnecting…');
        case LiveKind.connected:
          setState(() => _state = '${_rooms.length} rooms · live');
        case LiveKind.talk:
          break;
      }
    });
    await live.connect();
  }

  /// The board. Its stages and their order come from the server, so a plugin
  /// adding a pipeline needs no change here.
  Future<void> _loadBoard() async {
    final api = _api;
    if (api == null) return;
    try {
      final b = await api.get('/leads?slim=1') as Map<String, dynamic>;
      if (!mounted) return;
      setState(() {
        _records = ((b['leads'] ?? []) as List)
            .map((r) => WorkRecord(r as Map<String, dynamic>))
            .toList();
        _stages = ((b['stages'] ?? []) as List).cast<String>();
        _deadStages = ((b['dead_stages'] ?? []) as List).cast<String>();
        _counts = ((b['counts'] ?? {}) as Map)
            .map((k, v) => MapEntry(k as String, (v as num).toInt()));
      });
    } catch (_) {
      // A board that will not load is not worth killing the map for.
    }
  }

  /// Castles come from `/plugins`: a plugin that declares rooms is a castle,
  /// an extension that only patches is not.
  Future<void> _loadPlugins() async {
    final api = _api;
    if (api == null) return;
    try {
      final d = await api.get('/plugins') as Map<String, dynamic>;
      final list = ((d['plugins'] ?? []) as List)
          .map((p) => (p as Map).cast<String, dynamic>())
          .toList();
      if (!mounted) return;
      setState(() {
        _plugins = list;
        _kinds = ((d['lead_kinds'] ?? []) as List).cast<String>();
        _castles = Castle.group(
          _rooms,
          {for (final p in list)
            p['id'] as String: ((p['rooms'] ?? []) as List).cast<String>()},
          {for (final p in list)
            p['id'] as String: (p['name'] ?? p['id']) as String},
        );
      });
    } catch (_) {
      // The map still works without them; it just cannot group.
    }
  }

  /// Pending approvals per castle, for the badge you can read from far off.
  Map<String, int> get _castleBadges {
    final out = <String, int>{};
    for (final c in _castles) {
      var n = 0;
      for (final r in c.rooms) {
        n += _badges[r.id] ?? 0;
      }
      if (n > 0) out[c.pluginId] = n;
    }
    return out;
  }

  Future<void> _loadApprovals() async {
    final api = _api;
    if (api == null) return;
    try {
      final a = await api.get('/approvals?status=pending');
      if (!mounted) return;
      setState(() {
        _approvals = ((a['approvals'] ?? []) as List)
            .map((x) => Approval(x as Map<String, dynamic>))
            .toList();
        _badges = _countsByRoom(a);
      });
    } catch (_) {
      // Not worth killing the map for.
    }
  }

  Map<String, int> _countsByRoom(dynamic payload) {
    if (payload is Map && payload['counts_by_room'] is Map) {
      return (payload['counts_by_room'] as Map)
          .map((k, v) => MapEntry(k as String, (v as num).toInt()));
    }
    return const {};
  }

  @override
  Widget build(BuildContext context) {
    final agents = _live?.agents.values.toList() ?? const <AgentState>[];
    final selected =
        _rooms.where((r) => r.id == _selected).cast<Room?>().firstOrNull;

    return Scaffold(
      body: !_connected
          ? _disconnected()
          // The map fills the window and the panel sits OVER it, so folding
          // the panel away gives the map the whole screen rather than a
          // slightly wider column.
          : Stack(children: [
              Positioned.fill(
                child: MapView(
                  rooms: _rooms,
                  agents: agents,
                  badges: _badges,
                  castles: _castles,
                  castleBadges: _castleBadges,
                  selectedRoom: _selected,
                  onRoomTapped: (r) => setState(() {
                    _selected = r.id;
                    _pane = _Pane.room;
                    _panelOpen = true;
                  }),
                ),
              ),
              if (_panelOpen)
                Positioned(
                  top: 0,
                  right: 0,
                  bottom: 0,
                  width: 400,
                  child: Material(
                    color: const Color(0xFF161922),
                    elevation: 8,
                    child: Column(children: [
                      _tabs(),
                      Expanded(
                        child: switch (_pane) {
                          _Pane.approvals => Approvals(
                              api: _api!,
                              approvals: _approvals,
                              onResolved: () async {
                                await _loadApprovals();
                                await _loadBoard();
                              },
                              onOpenRecord: (id) => setState(() {
                                _selectedRecord = id;
                                _pane = _Pane.board;
                              }),
                            ),
                          _Pane.room when selected != null => RoomPanel(
                              api: _api!,
                              room: selected,
                              here: agents
                                  .where((a) => a.roomId == selected.id)
                                  .toList(),
                              onClose: () =>
                                  setState(() => _pane = _Pane.board),
                              onChanged: () async {
                                await _loadBoard();
                                await _loadApprovals();
                              },
                            ),
                          _ => _boardPane(),
                        },
                      ),
                    ]),
                  ),
                ),
              _controls(),
            ]),
    );
  }

  Widget _disconnected() => Center(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Text(_state, style: const TextStyle(color: Colors.white54)),
          const SizedBox(height: 14),
          FilledButton.tonal(
            onPressed: () => _settings().open(context, tab: 'connection'),
            child: const Text('Connection settings'),
          ),
        ]),
      );

  Settings _settings() => Settings(
        server: _server,
        onConnect: () {
          Navigator.of(context).maybePop();
          _connect();
        },
        connected: _connected,
        status: _state,
        rooms: _rooms.length,
        plugins: _plugins,
        stages: _stages,
        kinds: _kinds,
      );

  /// The only chrome: fold the panel, and the menu. Everything that was in
  /// the old top bar lives in Settings now — a bar across the top of a map is
  /// a bar across the top of the thing you came to look at.
  Widget _controls() {
    return Positioned(
      top: 10,
      right: _panelOpen ? 412 : 12,
      child: Row(children: [
        _round(
          icon: _panelOpen ? Icons.chevron_right : Icons.chevron_left,
          tip: _panelOpen ? 'hide the panel' : 'show the panel',
          onTap: () => setState(() => _panelOpen = !_panelOpen),
          badge: _panelOpen ? 0 : _approvals.length,
        ),
        const SizedBox(width: 8),
        _round(
          icon: Icons.menu,
          tip: 'settings',
          onTap: () => _settings().open(context),
        ),
      ]),
    );
  }

  Widget _round({
    required IconData icon,
    required String tip,
    required VoidCallback onTap,
    int badge = 0,
  }) {
    return Tooltip(
      message: tip,
      child: Stack(clipBehavior: Clip.none, children: [
        Material(
          color: const Color(0xCC1C2029),
          shape: const CircleBorder(),
          child: InkWell(
            customBorder: const CircleBorder(),
            onTap: onTap,
            child: Padding(
              padding: const EdgeInsets.all(9),
              child: Icon(icon, size: 20, color: Colors.white70),
            ),
          ),
        ),
        if (badge > 0)
          Positioned(
            right: -2,
            top: -2,
            child: Container(
              padding:
                  const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
              decoration: BoxDecoration(
                color: const Color(0xFFE23D3D),
                borderRadius: BorderRadius.circular(9),
              ),
              child: Text('$badge',
                  style: const TextStyle(
                      fontSize: 10.5, fontWeight: FontWeight.w700)),
            ),
          ),
      ]),
    );
  }

  /// The right-hand column's tabs. Approvals carries a count, because a gate
  /// nobody notices is a gate that does not work — the whole design assumes
  /// the operator can walk away and be called back.
  Widget _tabs() {
    final pending = _approvals.length;
    return Container(
      color: const Color(0xFF191C24),
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
      child: Row(
        children: [
          _tab('Board', _Pane.board),
          _tab('Approvals', _Pane.approvals, count: pending),
          if (_selected != null) _tab('Room', _Pane.room),
        ],
      ),
    );
  }

  Widget _tab(String label, _Pane pane, {int count = 0}) {
    final on = _pane == pane;
    return Padding(
      padding: const EdgeInsets.only(right: 4),
      child: TextButton(
        onPressed: () => setState(() => _pane = pane),
        style: TextButton.styleFrom(
          backgroundColor: on ? Colors.white12 : Colors.transparent,
          foregroundColor: on ? Colors.white : Colors.white54,
          padding: const EdgeInsets.symmetric(horizontal: 12),
          minimumSize: const Size(0, 34),
        ),
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          Text(label, style: const TextStyle(fontSize: 13)),
          if (count > 0) ...[
            const SizedBox(width: 6),
            Container(
              padding:
                  const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
              decoration: BoxDecoration(
                color: const Color(0xFFE23D3D),
                borderRadius: BorderRadius.circular(9),
              ),
              child: Text('$count',
                  style: const TextStyle(
                      fontSize: 11,
                      color: Colors.white,
                      fontWeight: FontWeight.w700)),
            ),
          ],
        ]),
      ),
    );
  }

  Widget _boardPane() {
    return Container(
      color: const Color(0xFF161922),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 8, 2),
            child: Row(
              children: [
                Text('${_records.length} records',
                    style: const TextStyle(color: Colors.white38)),
                const Spacer(),
                IconButton(
                  tooltip: 'reload',
                  onPressed: () async {
                    await _loadBoard();
                    await _loadApprovals();
                  },
                  icon: const Icon(Icons.refresh, size: 18),
                ),
              ],
            ),
          ),
          Expanded(
            child: Board(
              records: _records,
              stages: _stages,
              deadStages: _deadStages,
              counts: _counts,
              selectedId: _selectedRecord,
              onTapRecord: (r) => setState(() => _selectedRecord = r.id),
            ),
          ),
        ],
      ),
    );
  }


}

/// Which pane the right-hand column shows.
enum _Pane { board, approvals, room }
