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
  });

  final List<WorkRecord> records;
  final List<String> stages;
  final List<String> deadStages;
  final Map<String, int> counts;
  final void Function(WorkRecord) onTapRecord;
  final String? selectedId;

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
    this.selectedId,
  });

  final String stage;
  final List<WorkRecord> rows;
  final bool dimmed;
  final void Function(WorkRecord) onTapRecord;
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
              ],
            ),
          ),
        ),
        if (_open)
          for (final r in widget.rows)
            _RecordRow(
              record: r,
              selected: r.id == widget.selectedId,
              onTap: () => widget.onTapRecord(r),
            ),
        const SizedBox(height: 6),
      ],
    );
  }
}

class _RecordRow extends StatelessWidget {
  const _RecordRow(
      {required this.record, required this.selected, required this.onTap});

  final WorkRecord record;
  final bool selected;
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
