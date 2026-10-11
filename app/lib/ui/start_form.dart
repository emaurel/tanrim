import 'package:flutter/material.dart';

import '../api/client.dart';
import 'input_form.dart';

/// How work ENTERS a pipeline, drawn from what the plugin declared.
///
/// The one thing the stage transport cannot derive. Everything else about
/// moving work follows from a record's stage — the room whose benches declare
/// it gets dispatched — but the FIRST record has no stage to be found at, so
/// the room that would make one is dispatched by nothing. Both sourcing rooms
/// in this repo were reachable only by curl.
///
/// The form is built from `inputs` rather than being a bare button because a
/// bare button serves exactly one of the three openings here: Nova REFUSES an
/// empty prompt, Scout takes an optional set of boards, and a commission is
/// six fields typed by hand.
class StartForms extends StatefulWidget {
  const StartForms({
    super.key,
    required this.api,
    required this.castleId,
    this.onStarted,
    this.only,
  });

  final Api api;
  final String castleId;
  final VoidCallback? onStarted;

  /// Show only the starts that run in this room, by scoped room id. The castle
  /// window shows all of them; a room window shows its own.
  final String? only;

  @override
  State<StartForms> createState() => _StartFormsState();
}

class _StartFormsState extends State<StartForms> {
  List<Map<String, dynamic>> _starts = const [];
  bool _loading = true;
  String _problem = '';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final d = await widget.api.get('/starts?castle_id=${widget.castleId}')
          as Map;
      if (!mounted) return;
      setState(() {
        _starts = [
          for (final s in (d['starts'] as List? ?? const []))
            if (widget.only == null || (s as Map)['room'] == widget.only)
              (s as Map).cast<String, dynamic>(),
        ];
        _loading = false;
      });
    } catch (e) {
      if (mounted) {
        setState(() {
          _loading = false;
          _problem = e is ApiError ? e.message : '$e';
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) {
      return const Padding(
        padding: EdgeInsets.all(20),
        child: Center(
            child: SizedBox(
                width: 18,
                height: 18,
                child: CircularProgressIndicator(strokeWidth: 2))),
      );
    }
    if (_problem.isNotEmpty) {
      return Padding(
        padding: const EdgeInsets.all(16),
        child: Text(_problem,
            style: const TextStyle(fontSize: 12, color: Color(0xFFE0A458))),
      );
    }
    if (_starts.isEmpty) {
      return const Padding(
        padding: EdgeInsets.all(16),
        child: Text(
          'Nothing here opens work. Records arrive from elsewhere — a route, '
          'or another castle.',
          style: TextStyle(fontSize: 12, height: 1.4, color: Colors.white38),
        ),
      );
    }
    // A Column, not a ListView. This is embedded inside the room panel's own
    // scroll view, and a nested vertical viewport is given unbounded height —
    // it does not degrade, it throws. The castle tab supplies the scrolling.
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (final s in _starts)
          _StartCard(
            api: widget.api,
            castleId: widget.castleId,
            start: s,
            onStarted: widget.onStarted,
          ),
      ],
    );
  }
}

class _StartCard extends StatefulWidget {
  const _StartCard({
    required this.api,
    required this.castleId,
    required this.start,
    this.onStarted,
  });

  final Api api;
  final String castleId;
  final Map<String, dynamic> start;
  final VoidCallback? onStarted;

  @override
  State<_StartCard> createState() => _StartCardState();
}

class _StartCardState extends State<_StartCard> {
  /// Whatever the form currently holds. `DeclaredForm` pushes it on every
  /// change rather than being read out at submit, so this card never reaches
  /// inside the renderer it now shares with the castle options panel.
  Map<String, dynamic> _values = {};
  bool _busy = false;
  String _problem = '';
  String _done = '';

  List<Map<String, dynamic>> get _inputs => [
        for (final i in (widget.start['inputs'] as List? ?? const []))
          (i as Map).cast<String, dynamic>()
      ];

  Future<void> _go() async {
    setState(() {
      _busy = true;
      _problem = '';
      _done = '';
    });
    final values = <String, dynamic>{..._values};
    try {
      final out = await widget.api.post('/starts/${widget.start['id']}',
          {'castle_id': widget.castleId, 'values': values});
      // A start that declines answers `ok: false` and a sentence rather than
      // throwing — half the refusals in this environment are deliberate, and
      // every one of them was invisible until a panel read it.
      if (out is Map && out['ok'] == false) {
        setState(() => _problem = '${out['error'] ?? 'refused'}');
      } else {
        setState(() => _done = 'started');
      }
      widget.onStarted?.call();
    } on ApiError catch (e) {
      setState(() => _problem = e.message);
    } catch (e) {
      setState(() => _problem = '$e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final note = '${widget.start['note'] ?? ''}';
    return Container(
      margin: const EdgeInsets.only(bottom: 14),
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: .04),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: Colors.white.withValues(alpha: .07)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('${widget.start['label']}',
              style: const TextStyle(
                  fontSize: 13.5, fontWeight: FontWeight.w600)),
          if (note.isNotEmpty) ...[
            const SizedBox(height: 4),
            Text(note,
                style: const TextStyle(
                    fontSize: 11.5, height: 1.35, color: Colors.white38)),
          ],
          const SizedBox(height: 12),
          DeclaredForm(
            inputs: _inputs,
            keyPrefix: 'start',
            onChanged: (v) => _values = v,
          ),
          const SizedBox(height: 4),
          Row(children: [
            FilledButton(
              onPressed: _busy ? null : _go,
              child: Text(_busy ? 'starting…' : '${widget.start['label']}'),
            ),
            const SizedBox(width: 10),
            if (_problem.isNotEmpty)
              Expanded(
                child: Text(_problem,
                    style: const TextStyle(
                        fontSize: 11.5, color: Color(0xFFE0A458))),
              )
            else if (_done.isNotEmpty)
              Text(_done,
                  style: const TextStyle(
                      fontSize: 11.5, color: Color(0xFF9BC53D))),
          ]),
        ],
      ),
    );
  }
}
