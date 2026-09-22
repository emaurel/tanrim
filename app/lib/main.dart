import 'package:flutter/material.dart';

import 'api/client.dart';
import 'api/live.dart';
import 'model/world.dart';
import 'ui/map_view.dart';

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
                          onRoomTapped: (r) =>
                              setState(() => _selected = r.id),
                        ),
                      ),
                      if (selected != null)
                        _RoomPanel(
                          room: selected,
                          agents: agents
                              .where((a) => a.roomId == selected.id)
                              .toList(),
                          onClose: () => setState(() => _selected = null),
                        ),
                    ],
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

/// The room a click selects. Deliberately generic — it renders whatever the
/// manifest says, so a plugin adding a room gets a working panel with no app
/// change. Bespoke panels come later and are the exception.
class _RoomPanel extends StatelessWidget {
  const _RoomPanel(
      {required this.room, required this.agents, required this.onClose});

  final Room room;
  final List<AgentState> agents;
  final VoidCallback onClose;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 340,
      color: const Color(0xFF161922),
      padding: const EdgeInsets.all(16),
      child: ListView(
        children: [
          Row(
            children: [
              Expanded(
                  child: Text(room.name,
                      style: const TextStyle(
                          fontSize: 18, fontWeight: FontWeight.w700))),
              IconButton(onPressed: onClose, icon: const Icon(Icons.close)),
            ],
          ),
          if (room.purpose.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 4, bottom: 12),
              child: Text(room.purpose,
                  style: const TextStyle(color: Colors.white60, height: 1.35)),
            ),
          _section('Crew', [
            for (final a in room.agents)
              _line(a.name, a.role, Color(a.color)),
          ]),
          _section('Workbenches', [
            for (final b in room.workbenches)
              _line(b.name, b.stages.isEmpty ? b.job : b.stages.join(' · '),
                  null),
          ]),
          if (room.tools.isNotEmpty)
            _section('Tools', [for (final t in room.tools) _line(t, '', null)]),
          if (agents.isNotEmpty)
            _section('Here now', [
              for (final a in agents)
                _line(a.name,
                    a.busy ? (a.status.isEmpty ? 'working' : a.status) : 'idle',
                    Color(a.color)),
            ]),
        ],
      ),
    );
  }

  Widget _section(String title, List<Widget> rows) {
    if (rows.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(top: 14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(title.toUpperCase(),
              style: const TextStyle(
                  fontSize: 11,
                  letterSpacing: 1.2,
                  color: Colors.white38,
                  fontWeight: FontWeight.w700)),
          const SizedBox(height: 6),
          ...rows,
        ],
      ),
    );
  }

  Widget _line(String a, String b, Color? dot) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (dot != null) ...[
            Padding(
              padding: const EdgeInsets.only(top: 5),
              child: Icon(Icons.circle, size: 9, color: dot),
            ),
            const SizedBox(width: 8),
          ],
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(a, style: const TextStyle(fontWeight: FontWeight.w600)),
                if (b.isNotEmpty)
                  Text(b,
                      style: const TextStyle(
                          color: Colors.white54, fontSize: 12, height: 1.3)),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
