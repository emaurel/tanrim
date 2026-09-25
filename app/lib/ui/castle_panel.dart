import 'package:flutter/material.dart';

import '../api/client.dart';
import '../model/castle.dart';
import 'castle_settings.dart';
import '../model/record.dart';
import '../model/world.dart';
import 'board.dart';

/// One castle, in the right-hand column.
///
/// A panel and not a dialog. A castle is a PLACE with work in it — its rooms,
/// its records, what it is an instance of — and a modal that asks for a name
/// and goes away can hold none of that. The first version was one, which also
/// crashed: it put a `Spacer` in `AlertDialog.actions`, and those lay out in
/// an `OverflowBar` rather than a `Row`, so the parent-data cast failed the
/// moment a castle was clicked.
class CastlePanel extends StatefulWidget {
  const CastlePanel({
    super.key,
    required this.castle,
    required this.rooms,
    required this.badges,
    required this.onRaze,
    required this.onOpenRoom,
    required this.records,
    required this.stages,
    required this.deadStages,
    required this.onTapRecord,
    this.selectedRecord,
    this.working = const {},
    this.onRunRecord,
    this.onStopRecord,
    this.agents = const [],
    this.api,
    this.onChanged,
  });

  final Castle castle;

  /// Every room on the map, so this can pick out its own.
  final List<Room> rooms;
  final Map<String, int> badges;


  final Future<void> Function() onRaze;
  final void Function(Room) onOpenRoom;

  /// Every record on the board. This picks out its own — work belongs to a
  /// castle, and two agencies' leads are not one queue.
  final List<WorkRecord> records;
  final List<String> stages;
  final List<String> deadStages;
  final void Function(WorkRecord) onTapRecord;
  final String? selectedRecord;

  /// Record ids an agent is busy on, so a row can say so.
  final Set<String> working;

  /// Start or stop a record from its row, without opening it.
  final void Function(WorkRecord)? onRunRecord;
  final void Function(WorkRecord)? onStopRecord;

  /// Every worker on the map. The Working tab is a join over these and the
  /// records — nothing new is asked of the server, because an agent already
  /// carries the record it was spawned for, the room it is in, the bench it
  /// is standing at and when it started.
  final List<AgentState> agents;

  /// For the Settings tab, which writes directly rather than through a
  /// callback per field — there are five of them and they all go to the same
  /// place.
  final Api? api;
  final VoidCallback? onChanged;

  @override
  State<CastlePanel> createState() => _CastlePanelState();
}

class _CastlePanelState extends State<CastlePanel> {

  List<Room> get _mine => widget.rooms
      .where((r) => r.castleId == widget.castle.id || r.castleId.isEmpty)
      .toList();

  /// This castle's work, by kind.
  ///
  /// Split by kind because a castle can run more than one — the web agency
  /// takes prospects AND ports, and they are different pipelines with
  /// different stages. One list of both was the old board's problem in
  /// miniature.
  Map<String, List<WorkRecord>> get _work {
    final out = <String, List<WorkRecord>>{};
    for (final r in widget.records) {
      if (r.castleId != widget.castle.id) continue;
      out.putIfAbsent(r.kind.isEmpty ? 'work' : r.kind, () => []).add(r);
    }
    return out;
  }

  /// What is being worked right now, in this castle.
  ///
  /// A join rather than a question: an agent carries the record it was
  /// spawned for, the room, the bench and when it started; the record carries
  /// its name, kind and stage. Asking the server for a list would be a third
  /// copy of facts it is already streaming.
  List<({AgentState agent, WorkRecord? record, Room? room})> get _inFlight {
    final byId = {for (final r in widget.records) r.id: r};
    // Matched against THIS castle's rooms rather than by parsing the id.
    // `castles.base` is the server's job; the client already knows which
    // rooms are its own.
    final rooms = {for (final r in _mine) r.id: r};
    final out = <({AgentState agent, WorkRecord? record, Room? room})>[];
    for (final a in widget.agents) {
      if (!a.busy || !rooms.containsKey(a.roomId)) continue;
      out.add((
        agent: a,
        record: (a.recordId == null) ? null : byId[a.recordId!],
        room: rooms[a.roomId],
      ));
    }
    // Longest-running first: the one you want to know about is the one that
    // has been going the longest.
    out.sort((x, y) =>
        (x.agent.busySince ?? 0).compareTo(y.agent.busySince ?? 0));
    return out;
  }

  /// Which section is showing: a kind of work, or 'rooms'.
  ///
  /// Resolved on first build rather than fixed, because which kinds a castle
  /// has depends on what is installed — and a castle with no work at all
  /// should land on the one tab it does have.
  String? _tab;

  String get _showing {
    final tabs = [
      if (_inFlight.isNotEmpty) 'working',
      ..._work.keys,
      'rooms',
      if (widget.api != null) 'settings',
    ];
    final wanted = _tab;
    if (wanted != null && tabs.contains(wanted)) return wanted;
    return tabs.first;
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.castle;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 10),
          child: _facts(c),
        ),
        _tabs(),
        Expanded(child: _body(c)),
      ],
    );
  }

  Widget _tabs() {
    final work = _work;
    // The WORK first and the rooms last. A castle is a place that does
    // something; its rooms are how, and you open one to look at what is in it
    // rather than at the building.
    //
    // `working` comes before all of it, and only when there IS something: a
    // tab that is empty most of the time trains you to skip it, and the whole
    // value of this one is that its presence means something is happening.
    final flight = _inFlight;
    final tabs = [
      if (flight.isNotEmpty) 'working',
      ...work.keys,
      'rooms',
      if (widget.api != null) 'settings',
    ];
    return SizedBox(
      height: 34,
      child: ListView(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: 12),
        children: [
          for (final id in tabs)
            Padding(
              padding: const EdgeInsets.only(right: 6),
              child: _tabButton(
                  id,
                  // Every tab that is not a KIND of work is named here. The
                  // fallback reads `work[id]!`, so a tab this chain does not
                  // recognise crashes the whole window — which is what
                  // `settings` did the moment it was added.
                  switch (id) {
                    'settings' => 'Settings',
                    'rooms' => 'Rooms (${_mine.length})',
                    'working' => 'Working (${flight.length})',
                    _ => '$id (${work[id]?.length ?? 0})',
                  }),
            ),
        ],
      ),
    );
  }

  Widget _tabButton(String id, String label) {
    final on = _showing == id;
    return TextButton(
      onPressed: () => setState(() => _tab = id),
      style: TextButton.styleFrom(
        visualDensity: VisualDensity.compact,
        backgroundColor:
            on ? Colors.white.withValues(alpha: .10) : Colors.transparent,
        foregroundColor: on ? Colors.white : Colors.white54,
      ),
      child: Text(label, style: const TextStyle(fontSize: 12)),
    );
  }

  Widget _body(Castle c) {
    if (_showing == 'settings') {
      return CastleSettings(
        api: widget.api!,
        castle: c,
        onChanged: widget.onChanged ?? () {},
        onRaze: widget.onRaze,
      );
    }
    if (_showing == 'working') return _working();
    if (_showing != 'rooms') {
      final mine = _work[_showing] ?? const <WorkRecord>[];
      final counts = <String, int>{};
      for (final r in mine) {
        counts[r.stage] = (counts[r.stage] ?? 0) + 1;
      }
      return Board(
        records: mine,
        stages: widget.stages,
        deadStages: widget.deadStages,
        counts: counts,
        onTapRecord: widget.onTapRecord,
        selectedId: widget.selectedRecord,
        working: widget.working,
        onRun: widget.onRunRecord,
        onStop: widget.onStopRecord,
      );
    }
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 10, 16, 16),
      children: [
        if (_mine.isEmpty)
          Text(
            c.installed
                ? 'none'
                : 'The plugin this is an instance of is not installed, so it '
                    'has no rooms. Install or enable it to bring them back.',
            style: const TextStyle(fontSize: 12, color: Colors.white38),
          )
        else
          for (final r in _mine) _roomRow(r),

      ],
    );
  }



  Widget _working() {
    final flight = _inFlight;
    if (flight.isEmpty) {
      return const Padding(
        padding: EdgeInsets.all(16),
        child: Text('nothing is running',
            style: TextStyle(fontSize: 12, color: Colors.white38)),
      );
    }
    return ListView.builder(
      padding: const EdgeInsets.fromLTRB(16, 10, 16, 16),
      itemCount: flight.length,
      itemBuilder: (_, i) {
        final it = flight[i];
        final record = it.record;
        final bench = it.room?.workbenches
            .where((b) => b.id == it.agent.workbench)
            .firstOrNull;
        return InkWell(
          onTap: record == null ? null : () => widget.onTapRecord(record),
          child: Padding(
            padding: const EdgeInsets.symmetric(vertical: 8),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(children: [
                  Container(
                    width: 8,
                    height: 8,
                    decoration: const BoxDecoration(
                        color: Color(0xFF6BD68A), shape: BoxShape.circle),
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      // A worker with no record is doing work that is not
                      // ABOUT one — sourcing, a sweep — and saying so is
                      // better than an empty line.
                      record?.name ?? 'no record — ${it.agent.name}',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(fontWeight: FontWeight.w600),
                    ),
                  ),
                  if (it.agent.busySince != null)
                    Text(_elapsed(it.agent.busySince!),
                        style: const TextStyle(
                            fontSize: 11, color: Colors.white38)),
                ]),
                const SizedBox(height: 3),
                Padding(
                  padding: const EdgeInsets.only(left: 16),
                  child: Wrap(spacing: 6, runSpacing: 4, children: [
                    if (record != null) _chip(record.kind),
                    if (record != null) _chip(record.stage, accent: true),
                    _chip(it.agent.name),
                    if (it.room != null) _chip(it.room!.name),
                    if (bench != null) _chip(bench.name),
                  ]),
                ),
                if ((it.agent.say ?? '').isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.only(left: 16, top: 4),
                    child: Text(it.agent.say!,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                            fontSize: 11.5,
                            color: Colors.white54,
                            fontStyle: FontStyle.italic)),
                  ),
              ],
            ),
          ),
        );
      },
    );
  }

  /// `4m`, `1h 12m`. Rounded down, because a run is minutes and a precise
  /// second on something that takes ten of them is noise.
  static String _elapsed(double since) {
    final secs =
        DateTime.now().millisecondsSinceEpoch / 1000 - since;
    if (secs < 60) return '${secs.clamp(0, 59).toInt()}s';
    final mins = secs ~/ 60;
    if (mins < 60) return '${mins}m';
    return '${mins ~/ 60}h ${mins % 60}m';
  }

  Widget _facts(Castle c) => Wrap(
        spacing: 6,
        runSpacing: 6,
        children: [
          _chip(c.pluginName),
          if (!c.installed) _chip('plugin not installed', warn: true),
          _chip('ring ${c.ring}, plot ${c.slot}'),
          _chip('${_mine.length} rooms'),
          _chip('${c.records} records'),
        ],
      );

  Widget _roomRow(Room r) {
    final badge = widget.badges[r.id] ?? 0;
    return InkWell(
      onTap: () => widget.onOpenRoom(r),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 7),
        child: Row(children: [
          Container(
            width: 9,
            height: 9,
            decoration: BoxDecoration(
                color: Color(r.color), shape: BoxShape.circle),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Text(r.name,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontSize: 13)),
          ),
          if (badge > 0)
            Container(
              padding:
                  const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
              decoration: BoxDecoration(
                color: const Color(0xFFE23D3D),
                borderRadius: BorderRadius.circular(9),
              ),
              child: Text('$badge',
                  style: const TextStyle(
                      fontSize: 11, fontWeight: FontWeight.bold)),
            ),
          const SizedBox(width: 6),
          Icon(Icons.chevron_right,
              size: 16, color: Colors.white.withValues(alpha: .3)),
        ]),
      ),
    );
  }

  Widget _chip(String s, {bool warn = false, bool accent = false}) =>
      Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
        decoration: BoxDecoration(
          color: accent
              ? const Color(0xFF8ECAE6).withValues(alpha: .18)
              : Colors.white.withValues(alpha: .06),
          borderRadius: BorderRadius.circular(5),
        ),
        child: Text(s,
            style: TextStyle(
                fontSize: 11.5,
                color: warn
                    ? const Color(0xFFE0A458)
                    : accent
                        ? const Color(0xFF8ECAE6)
                        : null)),
      );
}
