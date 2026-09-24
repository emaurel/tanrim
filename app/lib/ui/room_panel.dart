import 'package:flutter/material.dart';

import '../api/client.dart';
import '../model/record.dart';
import '../model/world.dart';

/// A room, rendered from whatever its manifest and handler report.
///
/// Deliberately generic. The web UI hardcoded eleven panels by name, so every
/// new plugin needed a matching TypeScript file before its room did anything;
/// this one shows the queue, the crew, the benches and the run action for any
/// room that declares them. Bespoke panels are meant to be the exception.
class RoomPanel extends StatefulWidget {
  const RoomPanel({
    super.key,
    required this.api,
    required this.room,
    required this.here,
    required this.onChanged,
  });

  final Api api;
  final Room room;

  /// Workers currently standing in this room, from the live socket.
  final List<AgentState> here;

  /// Something happened that the rest of the app should reload.
  final VoidCallback onChanged;

  @override
  State<RoomPanel> createState() => _RoomPanelState();
}

class _RoomPanelState extends State<RoomPanel> {
  Map<String, dynamic>? _state;
  String? _error;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void didUpdateWidget(RoomPanel old) {
    super.didUpdateWidget(old);
    if (old.room.id != widget.room.id) {
      setState(() {
        _state = null;
        _error = null;
      });
      _load();
    }
  }

  Future<void> _load() async {
    try {
      final s = await widget.api.get('/rooms/${widget.room.id}/state');
      if (mounted) {
        setState(() {
          _state = s as Map<String, dynamic>;
          // A run that was REFUSED after starting reports itself here, not in
          // `last_error` — the task did not throw, it declined. Half the
          // refusals in this system are deliberate (a wrong stage, a busy
          // room, a business holding our email), and every one of them was
          // invisible: the POST had already answered `ok: true`.
          final last = _state?['last_result'];
          if (last is Map && last['ok'] == false && last['error'] != null) {
            _error = last['error'].toString();
          }
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() => _error = e is ApiError ? e.message : '$e');
      }
    }
  }

  Future<void> _run(String recordId) async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final action = _state?['action_name'] as String?;
      if (action == null) return;
      // `payload`, NESTED. The server reads the action's arguments from
      // `body.payload`; sent flat they were dropped, the endpoint answered
      // `ok: true, started: true` because starting the task DID succeed, and
      // the task then refused itself with "lead_id required" where nothing
      // was looking. Clicking Run did nothing, visibly or otherwise.
      final out = await widget.api.post(
        '/rooms/${widget.room.id}/action',
        {'name': action, 'payload': {'lead_id': recordId}},
      );
      // A room answers a refusal with `ok: false` and a sentence. Showing it
      // matters more than it sounds: half the refusals in this system are
      // deliberate — a wrong stage, a busy room, a business holding our email.
      if (out is Map && out['ok'] == false) {
        setState(() => _error = (out['error'] ?? 'refused').toString());
      }
      widget.onChanged();
    } catch (e) {
      setState(() => _error = e is ApiError ? e.message : '$e');
    } finally {
      if (mounted) setState(() => _busy = false);
      await _load();
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = _state;
    final queue = ((s?['queue'] ?? []) as List)
        .map((r) => WorkRecord(r as Map<String, dynamic>))
        .toList();
    final running = (s?['running'] ?? false) == true;
    final atCapacity = (s?['at_capacity'] ?? false) == true;

    return Container(
      width: 380,
      color: const Color(0xFF161922),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _header(s),
          if (_error != null) _errorBar(),
          Expanded(
            child: s == null
                ? const Center(
                    child: SizedBox(
                        width: 18,
                        height: 18,
                        child: CircularProgressIndicator(strokeWidth: 2)))
                : ListView(
                    padding: const EdgeInsets.fromLTRB(16, 4, 16, 20),
                    children: [
                      if (widget.room.purpose.isNotEmpty)
                        Padding(
                          padding: const EdgeInsets.only(bottom: 14),
                          child: Text(widget.room.purpose,
                              style: const TextStyle(
                                  color: Colors.white60, height: 1.35)),
                        ),
                      if (queue.isNotEmpty)
                        _section(
                          'Waiting  ·  ${queue.length}',
                          [
                            for (final r in queue)
                              _queueRow(r, running || atCapacity || _busy),
                          ],
                        ),
                      if (queue.isEmpty && (s['has_handler'] ?? false) == true)
                        _note('nothing waiting at '
                            '${(s['accepts_stages'] as List?)?.join(", ") ?? "this room"}'),
                      _crew(s),
                      _benches(),
                      if (widget.room.tools.isNotEmpty)
                        _section('Tools', [
                          for (final t in widget.room.tools) _plain(t),
                        ]),
                      if (widget.room.skills.isNotEmpty)
                        _section('Skills', [
                          for (final t in widget.room.skills) _plain(t),
                        ]),
                    ],
                  ),
          ),
        ],
      ),
    );
  }

  /// What model the room runs on, and nothing else.
  ///
  /// The name and the close button used to be here too — and are drawn by the
  /// window's own title bar, so every room showed its name twice and carried
  /// two crosses.
  Widget _header(Map<String, dynamic>? s) {
    final model = s?['model'];
    if (model == null || '$model'.isEmpty) return const SizedBox(height: 6);
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 10, 16, 4),
      child: Text('$model',
          style: const TextStyle(fontSize: 11, color: Colors.white38)),
    );
  }

  Widget _errorBar() {
    return Container(
      width: double.infinity,
      margin: const EdgeInsets.fromLTRB(16, 0, 16, 10),
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: const Color(0xFF3A1F22),
        borderRadius: BorderRadius.circular(6),
      ),
      child: Text(_error!,
          style: const TextStyle(fontSize: 12, color: Color(0xFFFFB4AE))),
    );
  }

  /// A stable colour per kind.
  ///
  /// Derived from the name rather than listed, because the kinds come from
  /// whatever plugins are installed and a hardcoded map would be a core file
  /// naming one plugin's pipelines.
  static Color _kindColour(String kind) {
    const palette = [
      Color(0xFF8ECAE6),
      Color(0xFFE5989B),
      Color(0xFFBCD35F),
      Color(0xFFE0A458),
      Color(0xFF9AB8F0),
      Color(0xFFC9ADA7),
    ];
    return palette[kind.hashCode.abs() % palette.length];
  }

  Widget _queueRow(WorkRecord r, bool blocked) {
    return Container(
      margin: const EdgeInsets.only(bottom: 6),
      padding: const EdgeInsets.fromLTRB(10, 8, 6, 8),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: .04),
        borderRadius: BorderRadius.circular(6),
      ),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(children: [
                  // Which PIPELINE this is on. A room can work more than one
                  // — the Assay Room takes prospects and ports, which are
                  // different kinds of work with different stages — and the
                  // queue gave no way to tell one row from another.
                  if (r.kind.isNotEmpty) ...[
                    Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 6, vertical: 1),
                      decoration: BoxDecoration(
                        color: _kindColour(r.kind).withValues(alpha: .22),
                        borderRadius: BorderRadius.circular(4),
                      ),
                      child: Text(r.kind,
                          style: TextStyle(
                              fontSize: 10,
                              fontWeight: FontWeight.w700,
                              color: _kindColour(r.kind))),
                    ),
                    const SizedBox(width: 7),
                  ],
                  Expanded(
                    child: Text(r.name,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(fontWeight: FontWeight.w600)),
                  ),
                ]),
                const SizedBox(height: 2),
                Text('${r.stage} · ${ago(r.updated)}',
                    style: const TextStyle(
                        fontSize: 11, color: Colors.white38)),
              ],
            ),
          ),
          IconButton(
            tooltip: blocked ? 'the room is busy' : 'run',
            onPressed: blocked ? null : () => _run(r.id),
            icon: const Icon(Icons.play_arrow, size: 20),
          ),
        ],
      ),
    );
  }

  Widget _crew(Map<String, dynamic> s) {
    final limit = s['worker_limit'];
    final busy = s['workers_busy'];
    return _section(
      limit == null ? 'Crew' : 'Crew  ·  $busy/$limit busy',
      [
        for (final a in widget.room.agents)
          _row(a.name, a.role, Color(a.color)),
        for (final a in widget.here)
          if (!widget.room.agents.any((d) => d.id == a.id))
            _row(a.name, a.busy ? 'working' : 'idle', Color(a.color)),
      ],
    );
  }

  Widget _benches() {
    if (widget.room.workbenches.isEmpty) return const SizedBox.shrink();
    return _section('Workbenches', [
      for (final b in widget.room.workbenches)
        _row(
          b.name,
          b.stages.isEmpty ? b.job : b.stages.join(' · '),
          null,
          // Who is standing at it right now.
          badge: widget.here
              .where((a) => a.workbench == b.id)
              .map((a) => a.name)
              .join(', '),
        ),
    ]);
  }

  // -- bits ----------------------------------------------------------------

  Widget _section(String title, List<Widget> rows) {
    if (rows.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(title.toUpperCase(),
              style: const TextStyle(
                  fontSize: 10.5,
                  letterSpacing: 1.2,
                  color: Colors.white38,
                  fontWeight: FontWeight.w700)),
          const SizedBox(height: 8),
          ...rows,
        ],
      ),
    );
  }

  Widget _note(String s) => Padding(
        padding: const EdgeInsets.only(bottom: 16),
        child: Text(s,
            style: const TextStyle(color: Colors.white30, fontSize: 12.5)),
      );

  Widget _plain(String s) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 2),
        child: Text(s, style: const TextStyle(fontSize: 12.5)),
      );

  Widget _row(String a, String b, Color? dot, {String badge = ''}) {
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
                Row(children: [
                  Flexible(
                      child: Text(a,
                          style:
                              const TextStyle(fontWeight: FontWeight.w600))),
                  if (badge.isNotEmpty) ...[
                    const SizedBox(width: 6),
                    Text('· $badge',
                        style: const TextStyle(
                            fontSize: 11, color: Color(0xFF7AD7D7))),
                  ],
                ]),
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
