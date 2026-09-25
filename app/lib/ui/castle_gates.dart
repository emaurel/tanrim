import 'package:flutter/material.dart';

import '../api/client.dart';
import '../model/castle.dart';
import 'settings_tab.dart';

/// Which steps stop and ask you, for one castle and one kind of work.
///
/// Per castle and per kind because a gate is a judgement about a particular
/// pipeline in a particular place: wanting to check every build for one agency
/// says nothing about a second agency, and a prospect at `published` is waiting
/// for something a port at `published` is not.
class CastleGates extends StatefulWidget {
  const CastleGates({super.key, required this.api, required this.castle});

  final Api api;
  final Castle castle;

  @override
  State<CastleGates> createState() => _CastleGatesState();
}

class _CastleGatesState extends State<CastleGates> {
  /// The pipelines this castle's plugin declared, answered by `/pipeline`.
  List<String> _kinds = const [];
  String _kind = '';
  List<Map<String, dynamic>> _steps = const [];
  bool _loading = true;
  String _problem = '';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final adopted = _kind;
    setState(() => _loading = true);
    try {
      final d = await widget.api.get(
          '/pipeline?castle_id=${widget.castle.id}&kind=$_kind') as Map;
      if (!mounted) return;
      final kinds = [for (final k in (d['kinds'] as List? ?? const [])) '$k'];
      // A stage can be worked by more than one role on one pipeline; the tab
      // asks about the STAGE, so the first row for it is the one to show.
      final seen = <String>{};
      setState(() {
        _kinds = kinds;
        // The first load asks with no kind, which is how the kinds arrive.
        // Adopting one then means re-asking, so that the steps are this
        // pipeline's rather than every pipeline's merged.
        if (_kind.isEmpty && kinds.isNotEmpty) _kind = kinds.first;
        _steps = [
          for (final s in (d['steps'] as List? ?? const []))
            if (seen.add('${(s as Map)['stage']}'))
              (s).cast<String, dynamic>()
        ];
        _loading = false;
        _problem = '';
      });
      // Re-ask now that there is a kind, or the list is still the merged one.
      if (adopted != _kind) return _load();
    } catch (e) {
      if (mounted) {
        setState(() {
          _loading = false;
          _problem = e is ApiError ? e.message : '$e';
        });
      }
    }
  }

  Future<void> _toggle(String stage, bool on) async {
    try {
      await widget.api.post('/pipeline/gate', {
        'stage': stage,
        'on': on,
        'castle_id': widget.castle.id,
        'kind': _kind,
      });
      await _load();
    } on ApiError catch (e) {
      if (mounted) setState(() => _problem = e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) {
      return const Center(
          child: SizedBox(
              width: 18,
              height: 18,
              child: CircularProgressIndicator(strokeWidth: 2)));
    }
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
        if (_kinds.length > 1)
          Padding(
            padding: const EdgeInsets.only(bottom: 14),
            child: Wrap(
              spacing: 6,
              children: [
                for (final k in _kinds)
                  ChoiceChip(
                    label: Text(k, style: const TextStyle(fontSize: 11.5)),
                    selected: _kind == k,
                    visualDensity: VisualDensity.compact,
                    onSelected: (_) {
                      setState(() => _kind = k);
                      _load();
                    },
                  ),
              ],
            ),
          ),
        SettingsSection(
          title: 'Stops for ${_kind.isEmpty ? 'this work' : _kind}',
          note: 'A gated step raises a card and waits instead of dispatching. '
              'The question is asked BEFORE the room runs, which is why a gate '
              'belongs to the step rather than to one of the arrows out of it.',
          children: [
            if (_steps.isEmpty)
              const Text('no steps for this kind',
                  style: TextStyle(fontSize: 12, color: Colors.white38))
            else
              for (final s in _steps) _row(s),
          ],
        ),
      ],
    );
  }

  Widget _row(Map<String, dynamic> s) {
    final stage = '${s['stage']}';
    final permanent = s['permanent'] == true;
    final global = s['global'] == true;
    final waiting = (s['waiting'] as num?)?.toInt() ?? 0;

    final why = permanent
        ? '${s['permanent_reason'] ?? 'always gated'}'
        : global
            ? 'ticked globally, before gates were per castle — untick it in '
                'the Kingdom window'
            : '';

    return Padding(
      padding: const EdgeInsets.only(bottom: 4),
      child: Row(children: [
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(children: [
                Text(stage, style: const TextStyle(fontSize: 12.5)),
                if (waiting > 0) ...[
                  const SizedBox(width: 6),
                  Text('$waiting waiting',
                      style: const TextStyle(
                          fontSize: 11, color: Colors.white38)),
                ],
              ]),
              Text(
                why.isNotEmpty ? why : '${s['role'] ?? ''} · ${s['room_name'] ?? ''}',
                style: TextStyle(
                    fontSize: 11,
                    height: 1.3,
                    color: why.isNotEmpty
                        ? const Color(0xFFE0A458)
                        : Colors.white38),
              ),
            ],
          ),
        ),
        Switch(
          value: s['gated'] == true,
          // Neither a permanent gate nor a global one can be moved from here.
          // A switch that springs back, or that looks like it worked and
          // changed nothing, teaches worse than one that will not move.
          onChanged:
              (permanent || global) ? null : (v) => _toggle(stage, v),
        ),
      ]),
    );
  }
}
