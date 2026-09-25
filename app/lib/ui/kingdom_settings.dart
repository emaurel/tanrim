import 'package:flutter/material.dart';

import '../api/client.dart';
import 'settings_tab.dart';

/// Settings that belong to the whole estate rather than one castle.
///
/// Plugins are deliberately NOT here: installing one is a different kind of
/// act from tuning one, and it already has a home in the global settings.
class KingdomSettings extends StatefulWidget {
  const KingdomSettings({super.key, required this.api});

  final Api api;

  @override
  State<KingdomSettings> createState() => _KingdomSettingsState();
}

class _KingdomSettingsState extends State<KingdomSettings> {
  Map<String, dynamic>? _pipeline;
  int? _defaultWorkers;
  String _problem = '';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final p = await widget.api.get('/pipeline') as Map;
      final w = await widget.api.get('/rooms/workers') as Map;
      if (!mounted) return;
      setState(() {
        _pipeline = p.cast<String, dynamic>();
        _defaultWorkers = (w['default'] as num?)?.toInt();
        _problem = '';
      });
    } catch (e) {
      if (mounted) setState(() => _problem = e is ApiError ? e.message : '$e');
    }
  }

  Future<void> _gate(String stage, bool on) async {
    try {
      await widget.api.post('/pipeline/gate', {'stage': stage, 'on': on});
      await _load();
    } on ApiError catch (e) {
      if (mounted) setState(() => _problem = e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final p = _pipeline;
    if (p == null) {
      return _problem.isEmpty
          ? const Center(
              child: SizedBox(
                  width: 18,
                  height: 18,
                  child: CircularProgressIndicator(strokeWidth: 2)))
          : Padding(
              padding: const EdgeInsets.all(16),
              child: Text(_problem,
                  style: const TextStyle(
                      fontSize: 12, color: Color(0xFFE0A458))));
    }

    // Each step already carries whether its stage is gated and why it cannot
    // be ungated, so there is nothing to join here — one row per distinct
    // stage, in the order the pipeline declares them rather than alphabetical,
    // because that order is the shape of the work.
    final steps = [
      for (final s in (p['steps'] as List?) ?? const [])
        (s as Map).cast<String, dynamic>()
    ];
    final seen = <String>{};
    final rows = [
      for (final s in steps)
        if (seen.add('${s['stage']}')) s
    ];

    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 20),
      children: [
        if (_problem.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: Text(_problem,
                style: const TextStyle(
                    fontSize: 12, color: Color(0xFFE0A458))),
          ),
        SettingsSection(
          title: 'Gates',
          note: 'A gated stage stops and waits for you instead of dispatching. '
              'Some cannot be switched off — anything that reaches a stranger '
              'or spends money must never depend on a checkbox.',
          children: [
            for (final s in rows)
              _gateRow('${s['stage']}', s['gated'] == true,
                  s['permanent'] == true
                      ? '${s['permanent_reason'] ?? 'always gated'}'
                      : ''),
          ],
        ),
        SettingsSection(
          title: 'Crew',
          note: 'The default cap for every room that is not a singleton. A '
              'room with its own setting keeps it.',
          children: [
            EditableField(
              label: 'Workers per room',
              value: '${_defaultWorkers ?? 1}',
              numeric: true,
              onSubmit: (v) async {
                final n = int.tryParse(v);
                if (n == null || n < 1) return 'a whole number, 1 or more';
                try {
                  await widget.api.send('PUT', '/rooms/workers', {'default': n});
                  await _load();
                  return '';
                } on ApiError catch (e) {
                  return e.message;
                }
              },
            ),
          ],
        ),
      ],
    );
  }

  Widget _gateRow(String stage, bool on, String permanent) => Padding(
        padding: const EdgeInsets.only(bottom: 2),
        child: Row(children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(stage, style: const TextStyle(fontSize: 12.5)),
                if (permanent.isNotEmpty)
                  Text(permanent,
                      style: const TextStyle(
                          fontSize: 11, color: Colors.white38, height: 1.3)),
              ],
            ),
          ),
          Switch(
            value: on || permanent.isNotEmpty,
            // A permanent gate is shown ON and cannot be moved: the server
            // refuses it anyway, and a switch that springs back teaches
            // nothing about why.
            onChanged: permanent.isNotEmpty ? null : (v) => _gate(stage, v),
          ),
        ]),
      );
}
