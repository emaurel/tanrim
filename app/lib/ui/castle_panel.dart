import 'package:flutter/material.dart';

import '../model/castle.dart';
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
    required this.onClose,
    required this.onRename,
    required this.onRaze,
    required this.onOpenRoom,
    required this.records,
    required this.stages,
    required this.deadStages,
    required this.onTapRecord,
    this.selectedRecord,
  });

  final Castle castle;

  /// Every room on the map, so this can pick out its own.
  final List<Room> rooms;
  final Map<String, int> badges;

  final VoidCallback onClose;

  /// Returns a problem, or empty when it worked.
  final Future<String> Function(String name) onRename;
  final Future<void> Function() onRaze;
  final void Function(Room) onOpenRoom;

  /// Every record on the board. This picks out its own — work belongs to a
  /// castle, and two agencies' leads are not one queue.
  final List<WorkRecord> records;
  final List<String> stages;
  final List<String> deadStages;
  final void Function(WorkRecord) onTapRecord;
  final String? selectedRecord;

  @override
  State<CastlePanel> createState() => _CastlePanelState();
}

class _CastlePanelState extends State<CastlePanel> {
  late final TextEditingController _name =
      TextEditingController(text: widget.castle.name);
  final FocusNode _focus = FocusNode();
  bool _editing = false;
  bool _busy = false;
  String? _said;

  @override
  void didUpdateWidget(CastlePanel old) {
    super.didUpdateWidget(old);
    if (old.castle.id != widget.castle.id) {
      _name.text = widget.castle.name;
      _editing = false;
      _said = null;
    }
  }

  @override
  void dispose() {
    _name.dispose();
    _focus.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    final wanted = _name.text.trim();
    if (wanted.isEmpty) {
      setState(() {
        _name.text = widget.castle.name;
        _editing = false;
      });
      return;
    }
    if (wanted == widget.castle.name) {
      setState(() => _editing = false);
      return;
    }
    setState(() {
      _busy = true;
      _said = null;
    });
    final problem = await widget.onRename(wanted);
    if (!mounted) return;
    setState(() {
      _busy = false;
      _editing = false;
      _said = problem.isEmpty ? null : problem;
      if (problem.isNotEmpty) _name.text = widget.castle.name;
    });
  }

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

  /// Which section is showing: 'rooms', or a kind of work.
  String _tab = 'rooms';

  @override
  Widget build(BuildContext context) {
    final c = widget.castle;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _header(c),
        if (_said != null)
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
            child: Text(_said!,
                style: const TextStyle(
                    fontSize: 12, color: Color(0xFFE0A458))),
          ),
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 0, 16, 10),
          child: _facts(c),
        ),
        _tabs(),
        Expanded(child: _body(c)),
      ],
    );
  }

  Widget _tabs() {
    final work = _work;
    final tabs = ['rooms', ...work.keys];
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
                  id == 'rooms'
                      ? 'Rooms (${_mine.length})'
                      : '$id (${work[id]!.length})'),
            ),
        ],
      ),
    );
  }

  Widget _tabButton(String id, String label) {
    final on = _tab == id;
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
    if (_tab != 'rooms') {
      final mine = _work[_tab] ?? const <WorkRecord>[];
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
        const SizedBox(height: 22),
        OutlinedButton.icon(
          onPressed: _busy ? null : widget.onRaze,
          icon: const Icon(Icons.local_fire_department_outlined, size: 17),
          label: const Text('Raze this castle'),
          style:
              OutlinedButton.styleFrom(foregroundColor: Colors.red.shade300),
        ),
        const SizedBox(height: 8),
        Text(
          c.records == 0
              ? 'It holds no records.'
              : 'Its ${c.records} record(s) would be kept — they are the work, '
                  'and razing a place should not delete what was done there.',
          style: const TextStyle(
              fontSize: 11.5, color: Colors.white38, height: 1.4),
        ),
      ],
    );
  }


  /// The name, edited in place.
  ///
  /// In place rather than behind a dialog because the default is
  /// `[PLUGIN NAME] [N]` — it tells you what a castle IS and nothing about
  /// what it is for, so renaming is the first thing you do and should not be
  /// two clicks and a modal away.
  Widget _header(Castle c) => Padding(
        padding: const EdgeInsets.fromLTRB(16, 14, 8, 10),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: _editing
                  ? TextField(
                      controller: _name,
                      focusNode: _focus,
                      autofocus: true,
                      style: const TextStyle(
                          fontSize: 17, fontWeight: FontWeight.w700),
                      decoration: const InputDecoration(
                        isDense: true,
                        border: OutlineInputBorder(),
                        contentPadding:
                            EdgeInsets.symmetric(horizontal: 8, vertical: 8),
                      ),
                      onSubmitted: (_) => _save(),
                      onTapOutside: (_) => _save(),
                    )
                  : InkWell(
                      onTap: () => setState(() => _editing = true),
                      child: Padding(
                        padding: const EdgeInsets.symmetric(vertical: 8),
                        child: Row(children: [
                          Flexible(
                            child: Text(c.name,
                                overflow: TextOverflow.ellipsis,
                                style: const TextStyle(
                                    fontSize: 17,
                                    fontWeight: FontWeight.w700)),
                          ),
                          const SizedBox(width: 8),
                          Icon(Icons.edit_outlined,
                              size: 14,
                              color: Colors.white.withValues(alpha: .35)),
                        ]),
                      ),
                    ),
            ),
            if (_busy)
              const Padding(
                padding: EdgeInsets.all(12),
                child: SizedBox(
                    width: 14,
                    height: 14,
                    child: CircularProgressIndicator(strokeWidth: 2)),
              )
            else
              IconButton(
                onPressed: widget.onClose,
                icon: const Icon(Icons.close, size: 20),
              ),
          ],
        ),
      );

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

  Widget _chip(String s, {bool warn = false}) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
        decoration: BoxDecoration(
          color: Colors.white.withValues(alpha: .06),
          borderRadius: BorderRadius.circular(5),
        ),
        child: Text(s,
            style: TextStyle(
                fontSize: 11.5,
                color: warn ? const Color(0xFFE0A458) : null)),
      );
}
