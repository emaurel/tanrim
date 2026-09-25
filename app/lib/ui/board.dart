import 'package:flutter/material.dart';

import '../model/record.dart';

/// Every record, by stage.
///
/// The stage list and its order come from the server — the environment is
/// plugin-driven and a second plugin adds stages this build has never heard
/// of, so nothing here names one. The columns are whatever `/leads` says
/// they are.
class Board extends StatelessWidget {
  const Board({
    super.key,
    required this.records,
    required this.stages,
    required this.deadStages,
    required this.counts,
    required this.onTapRecord,
    this.selectedId,
    this.working = const {},
  });

  final List<WorkRecord> records;
  final List<String> stages;
  final List<String> deadStages;
  final Map<String, int> counts;
  final void Function(WorkRecord) onTapRecord;
  final String? selectedId;

  /// Record ids with an agent busy on them right now.
  ///
  /// Derived from the agents the server already streams — each carries the
  /// record it was spawned for and whether it is busy — rather than asking
  /// for a new field. A record is "being worked on" if somebody is standing
  /// at a bench holding it.
  final Set<String> working;

  @override
  Widget build(BuildContext context) {
    // Live stages first in pipeline order, then the endings. A board that
    // sorts `lost` next to `sourced` buries the work still worth doing.
    final order = [...stages, ...deadStages];
    final byStage = <String, List<WorkRecord>>{};
    for (final r in records) {
      byStage.putIfAbsent(r.stage, () => []).add(r);
    }
    // A stage the server did not list but a record is sitting at: show it
    // rather than dropping the record silently.
    for (final s in byStage.keys) {
      if (!order.contains(s)) order.add(s);
    }
    for (final list in byStage.values) {
      list.sort((a, b) => b.updated.compareTo(a.updated));
    }

    final live = order.where((s) => (byStage[s] ?? []).isNotEmpty).toList();
    if (live.isEmpty) {
      return const Center(
        child: Text('nothing in the pipeline',
            style: TextStyle(color: Colors.white38)),
      );
    }

    return ListView.builder(
      padding: const EdgeInsets.all(12),
      itemCount: live.length,
      itemBuilder: (context, i) {
        final stage = live[i];
        final rows = byStage[stage]!;
        final dead = deadStages.contains(stage);
        return _StageGroup(
          stage: stage,
          rows: rows,
          dimmed: dead,
          onTapRecord: onTapRecord,
          selectedId: selectedId,
          working: working,
        );
      },
    );
  }
}

class _StageGroup extends StatefulWidget {
  const _StageGroup({
    required this.stage,
    required this.rows,
    required this.dimmed,
    required this.onTapRecord,
    required this.working,
    this.selectedId,
  });

  final String stage;
  final List<WorkRecord> rows;
  final bool dimmed;
  final void Function(WorkRecord) onTapRecord;
  final Set<String> working;
  final String? selectedId;

  @override
  State<_StageGroup> createState() => _StageGroupState();
}

class _StageGroupState extends State<_StageGroup> {
  /// Closed to start with, endings included.
  ///
  /// A castle's work is a dozen stages and seventy records; opened, the board
  /// is a long scroll and the SHAPE of the pipeline — where the work has piled
  /// up — is the thing you came to see and the thing you cannot see.
  bool _open = false;

  /// How many of this stage's records have somebody on them.
  int get _busy =>
      widget.rows.where((r) => widget.working.contains(r.id)).length;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        InkWell(
          onTap: () => setState(() => _open = !_open),
          child: Padding(
            padding: const EdgeInsets.symmetric(vertical: 8),
            child: Row(
              children: [
                Icon(_open ? Icons.expand_more : Icons.chevron_right,
                    size: 18, color: Colors.white38),
                const SizedBox(width: 4),
                Text(
                  widget.stage,
                  style: TextStyle(
                    fontWeight: FontWeight.w700,
                    letterSpacing: 0.4,
                    color: widget.dimmed ? Colors.white38 : Colors.white,
                  ),
                ),
                const SizedBox(width: 8),
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 7, vertical: 1),
                  decoration: BoxDecoration(
                    color: Colors.white12,
                    borderRadius: BorderRadius.circular(9),
                  ),
                  child: Text('${widget.rows.length}',
                      style: const TextStyle(
                          fontSize: 11, color: Colors.white70)),
                ),
                // Folded, a stage is one line — so the dot has to survive on
                // it, or "something is running" is a thing you can only learn
                // by opening every group.
                if (_busy > 0) ...[
                  const SizedBox(width: 8),
                  const _WorkingDot(),
                  if (_busy > 1) ...[
                    const SizedBox(width: 4),
                    Text('$_busy',
                        style: const TextStyle(
                            fontSize: 11, color: Color(0xFF6BD68A))),
                  ],
                ],
              ],
            ),
          ),
        ),
        if (_open)
          for (final r in widget.rows)
            _RecordRow(
              record: r,
              selected: r.id == widget.selectedId,
              working: widget.working.contains(r.id),
              onTap: () => widget.onTapRecord(r),
            ),
        const SizedBox(height: 6),
      ],
    );
  }
}

class _RecordRow extends StatelessWidget {
  const _RecordRow(
      {required this.record,
      required this.selected,
      required this.working,
      required this.onTap});

  final WorkRecord record;
  final bool selected;
  final bool working;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final where = record.raw['city'] ?? record.raw['address'] ?? '';
    return InkWell(
      onTap: onTap,
      child: Container(
        margin: const EdgeInsets.only(bottom: 4, left: 22),
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
        decoration: BoxDecoration(
          color: selected ? Colors.white12 : Colors.white.withValues(alpha: .03),
          borderRadius: BorderRadius.circular(6),
          border: Border.all(
              color: selected ? Colors.white54 : Colors.transparent),
        ),
        child: Row(
          children: [
            if (working) ...[
              const _WorkingDot(),
              const SizedBox(width: 8),
            ],
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(record.name,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(fontWeight: FontWeight.w600)),
                  if ('$where'.isNotEmpty)
                    Text('$where',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                            fontSize: 11.5, color: Colors.white38)),
                ],
              ),
            ),
            const SizedBox(width: 8),
            Text(ago(record.updated),
                style: const TextStyle(fontSize: 11, color: Colors.white30)),
          ],
        ),
      ),
    );
  }
}


/// Somebody is working this record right now.
///
/// A dot rather than a spinner: a run takes minutes, and an animation that
/// long reads as a page that has not finished loading. Green because the only
/// other coloured mark on a record is the red approval badge, and those two
/// mean opposite things — one is "this is moving", the other "this is stuck
/// waiting for you".
class _WorkingDot extends StatelessWidget {
  const _WorkingDot();

  @override
  Widget build(BuildContext context) => Tooltip(
        message: 'an agent is working on this',
        child: Container(
          width: 8,
          height: 8,
          decoration: const BoxDecoration(
            color: Color(0xFF6BD68A),
            shape: BoxShape.circle,
          ),
        ),
      );
}
