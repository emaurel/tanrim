import 'package:flutter/material.dart';

import '../model/castle.dart';

/// Every castle you have, grouped by what they are instances of.
///
/// This replaced the board as the thing you open first. A single board mixed
/// every castle's work into one list, which was wrong the moment there was
/// more than one castle: two agencies' leads are not one queue, and nothing
/// in the list said which was which. Work belongs to a castle, so it is
/// reached through one.
class Kingdom extends StatelessWidget {
  const Kingdom({
    super.key,
    required this.castles,
    required this.badges,
    required this.onOpen,
    required this.onBuild,
  });

  final List<Castle> castles;

  /// castle id -> pending approvals in it.
  final Map<String, int> badges;

  final void Function(Castle) onOpen;
  final VoidCallback onBuild;

  @override
  Widget build(BuildContext context) {
    final byPlugin = <String, List<Castle>>{};
    for (final c in castles) {
      byPlugin.putIfAbsent(c.pluginName, () => []).add(c);
    }

    return ListView(
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 16),
      children: [
        if (castles.isEmpty)
          const Padding(
            padding: EdgeInsets.symmetric(vertical: 20),
            child: Text(
              'No castles yet. Click any outlined plot on the map to build '
              'one.',
              style: TextStyle(color: Colors.white38, height: 1.5),
            ),
          ),
        for (final entry in byPlugin.entries) ...[
          Padding(
            padding: const EdgeInsets.only(top: 6, bottom: 8),
            child: Text(entry.key.toUpperCase(),
                style: const TextStyle(
                    fontSize: 10.5,
                    letterSpacing: 0.9,
                    fontWeight: FontWeight.w700,
                    color: Colors.white38)),
          ),
          for (final c in entry.value) _row(c),
          const SizedBox(height: 10),
        ],
        const SizedBox(height: 6),
        OutlinedButton.icon(
          onPressed: onBuild,
          icon: const Icon(Icons.add, size: 16),
          label: const Text('Build a castle'),
        ),
        const SizedBox(height: 8),
        const Text(
          'Or click any outlined plot on the map.',
          style: TextStyle(fontSize: 11.5, color: Colors.white30),
        ),
      ],
    );
  }

  Widget _row(Castle c) {
    final waiting = badges[c.id] ?? 0;
    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      color: Colors.white.withValues(alpha: .04),
      elevation: 0,
      child: InkWell(
        onTap: () => onOpen(c),
        borderRadius: BorderRadius.circular(6),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(12, 10, 10, 10),
          child: Row(children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(c.name,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                          fontSize: 13.5, fontWeight: FontWeight.w600)),
                  const SizedBox(height: 3),
                  Text(
                    c.installed
                        ? '${c.rooms.length} rooms · ${c.records} records '
                            '· ring ${c.ring}, plot ${c.slot}'
                        : 'plugin not installed',
                    style: TextStyle(
                        fontSize: 11.5,
                        color: c.installed
                            ? Colors.white38
                            : const Color(0xFFE0A458)),
                  ),
                ],
              ),
            ),
            if (waiting > 0)
              Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                decoration: BoxDecoration(
                  color: const Color(0xFFE23D3D),
                  borderRadius: BorderRadius.circular(10),
                ),
                child: Text('$waiting',
                    style: const TextStyle(
                        fontSize: 11.5, fontWeight: FontWeight.bold)),
              ),
            const SizedBox(width: 6),
            Icon(Icons.chevron_right,
                size: 17, color: Colors.white.withValues(alpha: .3)),
          ]),
        ),
      ),
    );
  }
}
