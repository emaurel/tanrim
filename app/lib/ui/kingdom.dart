import 'package:flutter/material.dart';

import '../api/client.dart';
import 'kingdom_settings.dart';

import '../model/castle.dart';

/// Every castle you have, grouped by what they are instances of.
///
/// This replaced the board as the thing you open first. A single board mixed
/// every castle's work into one list, which was wrong the moment there was
/// more than one castle: two agencies' leads are not one queue, and nothing
/// in the list said which was which. Work belongs to a castle, so it is
/// reached through one.
class Kingdom extends StatefulWidget {
  const Kingdom({
    super.key,
    required this.castles,
    required this.badges,
    required this.onOpen,
    this.api,
  });

  final List<Castle> castles;

  /// castle id -> pending approvals in it.
  final Map<String, int> badges;

  final void Function(Castle) onOpen;

  /// For the Settings tab. Absent before the app has connected, and the tab
  /// is absent with it rather than showing controls that cannot write.
  final Api? api;

  @override
  State<Kingdom> createState() => _KingdomState();
}

class _KingdomState extends State<Kingdom> {
  String _tab = 'castles';

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (widget.api != null)
          SizedBox(
            height: 34,
            child: Row(children: [
              const SizedBox(width: 12),
              _tabButton('castles', 'Castles'),
              const SizedBox(width: 6),
              _tabButton('settings', 'Settings'),
            ]),
          ),
        Expanded(
          child: _tab == 'settings'
              ? KingdomSettings(api: widget.api!)
              : _castles(context),
        ),
      ],
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

  Widget _castles(BuildContext context) {
    final byPlugin = <String, List<Castle>>{};
    for (final c in widget.castles) {
      byPlugin.putIfAbsent(c.pluginName, () => []).add(c);
    }

    return ListView(
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 16),
      children: [
        if (widget.castles.isEmpty)
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
        const Text(
          'Click any outlined plot on the map to build another.',
          style: TextStyle(fontSize: 11.5, color: Colors.white30),
        ),
      ],
    );
  }

  Widget _row(Castle c) {
    final waiting = widget.badges[c.id] ?? 0;
    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      color: Colors.white.withValues(alpha: .04),
      elevation: 0,
      child: InkWell(
        onTap: () => widget.onOpen(c),
        borderRadius: BorderRadius.circular(6),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(12, 10, 10, 10),
          child: Row(children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(children: [
                    // Working or idle, at a glance and without reading.
                    Tooltip(
                      message: c.working
                          ? 'working — a run is in flight'
                          : 'idle',
                      child: Container(
                        width: 8,
                        height: 8,
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          color: c.working
                              ? const Color(0xFF63C77B)
                              : Colors.white.withValues(alpha: .28),
                        ),
                      ),
                    ),
                    const SizedBox(width: 8),
                    Flexible(
                      child: Text(c.name,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(
                              fontSize: 13.5, fontWeight: FontWeight.w600)),
                    ),
                    // Beside the name, where you read it, rather than off at
                    // the other end of the row.
                    if (waiting > 0) ...[
                      const SizedBox(width: 8),
                      Container(
                        padding: const EdgeInsets.symmetric(
                            horizontal: 7, vertical: 2),
                        decoration: BoxDecoration(
                          color: const Color(0xFFE23D3D),
                          borderRadius: BorderRadius.circular(9),
                        ),
                        child: Text('$waiting',
                            style: const TextStyle(
                                fontSize: 11, fontWeight: FontWeight.bold)),
                      ),
                    ],
                  ]),
                  const SizedBox(height: 4),
                  Text(
                    c.installed
                        ? '${c.rooms.length} rooms · ${c.records} records'
                            '${c.working ? " · ${c.status}" : ""}'
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
            const SizedBox(width: 6),
            Icon(Icons.chevron_right,
                size: 17, color: Colors.white.withValues(alpha: .3)),
          ]),
        ),
      ),
    );
  }
}
