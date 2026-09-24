import 'package:flutter/material.dart';

import '../model/castle.dart';

/// Choose what to build on a piece of empty land.
///
/// Only plugins that declare rooms of their own are offered: an extension
/// lives inside the castle of what it extends and cannot have one, which is
/// the same rule the plugins panel draws its hierarchy by.
Future<String?> askWhatToBuild(
  BuildContext context, {
  required List<Map<String, dynamic>> buildable,
  required int ring,
  required int slot,
}) =>
    showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Build here'),
        content: SizedBox(
          width: 420,
          child: buildable.isEmpty
              ? const Text('No plugin with rooms of its own is installed, so '
                  'there is nothing to build. An extension lives inside the '
                  'castle it extends.')
              : Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('Ring $ring, plot $slot',
                        style: const TextStyle(
                            fontSize: 12, color: Colors.white54)),
                    const SizedBox(height: 12),
                    for (final p in buildable)
                      Card(
                        margin: const EdgeInsets.only(bottom: 8),
                        child: ListTile(
                          title: Text('${p['name'] ?? p['id']}'),
                          subtitle: Text(
                            [
                              if ('${p['description'] ?? ''}'.isNotEmpty)
                                '${p['description']}',
                              // How many already stand, because the second of
                              // something is a different decision from the
                              // first.
                              (p['built'] ?? 0) == 0
                                  ? 'none built yet'
                                  : '${p['built']} already built',
                            ].join('\n'),
                            style: const TextStyle(fontSize: 12),
                          ),
                          isThreeLine:
                              '${p['description'] ?? ''}'.isNotEmpty,
                          onTap: () =>
                              Navigator.pop(ctx, p['id'] as String),
                        ),
                      ),
                  ],
                ),
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx),
              child: const Text('Cancel')),
        ],
      ),
    );

/// Razing is not undoable, so it asks — and says what survives, because
/// "your leads are safe" is the thing you actually want to know.
Future<bool> confirmRaze(BuildContext context, Castle castle) async =>
    await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('Raze ${castle.name}?'),
        content: Text(
          castle.records == 0
              ? 'It holds no records. Its rooms come off the map.'
              : 'Its ${castle.records} record(s) are KEPT — they are the work, '
                  'and razing a place should not delete what was done there. '
                  'They stop appearing in any queue, and come back if a castle '
                  'is rebuilt here for the same plugin.',
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: const Text('Cancel')),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: Colors.red.shade700),
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Raze'),
          ),
        ],
      ),
    ) ??
    false;
