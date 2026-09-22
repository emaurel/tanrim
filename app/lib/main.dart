import 'package:flutter/material.dart';

import 'api/client.dart';
import 'api/live.dart';
import 'model/record.dart';
import 'model/world.dart';
import 'ui/board.dart';
import 'ui/map_view.dart';
import 'ui/room_panel.dart';

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
  bool _showBoard = true;

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
      final approvals = await api.get('/approvals?status=pending');
      setState(() {
        _rooms = rooms;
        _badges = _countsByRoom(approvals);
        _state = '${rooms.length} rooms';
        _connected = true;
      });
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
          final a = await api.get('/approvals?status=pending');
          setState(() => _badges = _countsByRoom(a));
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
      body: Column(
        children: [
          _bar(),
          Expanded(
            child: !_connected
                ? Center(
                    child: Text(_state,
                        style: const TextStyle(color: Colors.white54)))
                : Row(
                    children: [
                      Expanded(
                        child: MapView(
                          rooms: _rooms,
                          agents: agents,
                          badges: _badges,
                          selectedRoom: _selected,
                          onRoomTapped: (r) => setState(() {
                            _selected = r.id;
                            _showBoard = false;
                          }),
                        ),
                      ),
                      SizedBox(
                        width: 380,
                        child: _showBoard || selected == null
                            ? _boardPane()
                            : RoomPanel(
                                api: _api!,
                                room: selected,
                                here: agents
                                    .where((a) => a.roomId == selected.id)
                                    .toList(),
                                onClose: () =>
                                    setState(() => _showBoard = true),
                                onChanged: _loadBoard,
                              ),
                      ),
                    ],
                  ),
          ),
        ],
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
            padding: const EdgeInsets.fromLTRB(16, 14, 16, 2),
            child: Row(
              children: [
                const Text('Board',
                    style: TextStyle(
                        fontSize: 18, fontWeight: FontWeight.w700)),
                const SizedBox(width: 8),
                Text('${_records.length}',
                    style: const TextStyle(color: Colors.white38)),
                const Spacer(),
                IconButton(
                  tooltip: 'reload',
                  onPressed: _loadBoard,
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

  Widget _bar() {
    return Container(
      padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
      color: const Color(0xFF191C24),
      child: Row(
        children: [
          const Text('Tanrim',
              style: TextStyle(fontWeight: FontWeight.w700, fontSize: 16)),
          const SizedBox(width: 16),
          SizedBox(
            width: 260,
            child: TextField(
              controller: _server,
              style: const TextStyle(fontSize: 13),
              decoration: const InputDecoration(
                isDense: true,
                border: OutlineInputBorder(),
                labelText: 'server',
              ),
              onSubmitted: (_) => _connect(),
            ),
          ),
          const SizedBox(width: 8),
          FilledButton.tonal(
              onPressed: _connect, child: const Text('Connect')),
          const SizedBox(width: 16),
          Icon(Icons.circle,
              size: 10,
              color: _connected ? const Color(0xFF63C77B) : Colors.orange),
          const SizedBox(width: 6),
          Text(_state, style: const TextStyle(color: Colors.white60)),
        ],
      ),
    );
  }
}
