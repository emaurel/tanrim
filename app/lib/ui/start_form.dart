import 'package:flutter/material.dart';

import '../api/client.dart';

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
  final Map<String, dynamic> _values = {};
  final Map<String, TextEditingController> _text = {};
  bool _busy = false;
  String _problem = '';
  String _done = '';

  List<Map<String, dynamic>> get _inputs => [
        for (final i in (widget.start['inputs'] as List? ?? const []))
          (i as Map).cast<String, dynamic>()
      ];

  @override
  void initState() {
    super.initState();
    for (final i in _inputs) {
      final id = '${i['id']}';
      final kind = '${i['kind'] ?? 'text'}';
      final options = (i['options'] as List? ?? const []);
      if (kind == 'toggle') {
        _values[id] = i['default'] == true;
      } else if (kind == 'list') {
        _values[id] = <String>[];
      } else if (i['default'] != null) {
        _values[id] = i['default'];
      }
      // A controller for every field that will RENDER as a text box — which
      // includes a `list` with no options to pick from, and any kind this
      // binary does not recognise. Deciding by kind alone left those two
      // drawn but not wired: you could type into them and nothing was sent.
      final typed = kind != 'toggle' &&
          kind != 'choice' &&
          !(kind == 'list' && options.isNotEmpty);
      if (typed) {
        _text[id] = TextEditingController(text: '${i['default'] ?? ''}');
      }
    }
  }

  @override
  void dispose() {
    for (final c in _text.values) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _go() async {
    setState(() {
      _busy = true;
      _problem = '';
      _done = '';
    });
    final values = <String, dynamic>{..._values};
    final freeLists = {
      for (final i in _inputs)
        if ('${i['kind']}' == 'list') '${i['id']}',
    };
    _text.forEach((id, c) {
      final typed = c.text.trim();
      if (typed.isEmpty) return;
      // A `list` with no options is typed comma-separated, and must still
      // ARRIVE as a list — the plugin declared the kind, so sending a bare
      // string would make the wire disagree with the declaration for exactly
      // the fields nobody tested.
      values[id] = freeLists.contains(id)
          ? [for (final p in typed.split(',')) p.trim()]
              .where((p) => p.isNotEmpty)
              .toList()
          : typed;
    });
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
          for (final i in _inputs) _field(i),
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

  /// One control, from the fixed vocabulary.
  ///
  /// An unknown kind falls through to a text box rather than being dropped.
  /// The environment gains behaviour by gaining plugins, and a plugin built
  /// against a newer vocabulary than this binary must still be startable —
  /// degrading to something you can type in always leaves it usable.
  Widget _field(Map<String, dynamic> i) {
    final id = '${i['id']}';
    final label = '${i['label'] ?? id}';
    final hint = '${i['hint'] ?? ''}';
    final required = i['required'] == true;
    final options = [for (final o in (i['options'] as List? ?? const [])) '$o'];

    switch ('${i['kind'] ?? 'text'}') {
      case 'toggle':
        return Padding(
          padding: const EdgeInsets.only(bottom: 6),
          child: Row(children: [
            Switch(
              key: ValueKey('start-$id'),
              value: _values[id] == true,
              onChanged: (v) => setState(() => _values[id] = v),
            ),
            const SizedBox(width: 8),
            Expanded(child: _label(label, hint, required)),
          ]),
        );

      case 'choice':
        return _wrapped(
          label,
          hint,
          required,
          Wrap(spacing: 6, runSpacing: 4, children: [
            for (final o in options)
              ChoiceChip(
                label: Text(o, style: const TextStyle(fontSize: 11.5)),
                selected: _values[id] == o,
                visualDensity: VisualDensity.compact,
                onSelected: (_) => setState(() => _values[id] = o),
              ),
          ]),
        );

      case 'list':
        // With options it is a multi-select; without, free entries typed
        // comma-separated — the same field either way, so a plugin that has
        // no closed set still gets a control.
        if (options.isEmpty) break;
        final picked = (_values[id] as List?)?.cast<String>() ?? const [];
        return _wrapped(
          label,
          hint,
          required,
          Wrap(spacing: 6, runSpacing: 4, children: [
            for (final o in options)
              FilterChip(
                label: Text(o, style: const TextStyle(fontSize: 11.5)),
                selected: picked.contains(o),
                visualDensity: VisualDensity.compact,
                onSelected: (on) => setState(() {
                  final next = [...picked];
                  on ? next.add(o) : next.remove(o);
                  _values[id] = next;
                }),
              ),
          ]),
        );
    }

    final longtext = '${i['kind']}' == 'longtext';
    final number = '${i['kind']}' == 'number';
    // `file` lands here too: a path you type or paste. The app carries almost
    // no dependencies by choice, and a picker is one — worth adding when a
    // plugin actually declares a file, not before.
    return _wrapped(
      label,
      hint,
      required,
      TextField(
        // Keyed by the field the plugin declared, so a test (and a screen
        // reader) can name the control rather than counting them.
        key: ValueKey('start-$id'),
        controller: _text[id],
        maxLines: longtext ? 3 : 1,
        keyboardType: number ? TextInputType.number : null,
        style: const TextStyle(fontSize: 12.5),
        decoration: InputDecoration(
          isDense: true,
          hintText: hint.isEmpty ? null : hint,
          hintStyle: const TextStyle(fontSize: 11.5, color: Colors.white24),
          border: const OutlineInputBorder(),
          contentPadding:
              const EdgeInsets.symmetric(horizontal: 8, vertical: 8),
        ),
      ),
      showHint: false,
    );
  }

  Widget _label(String label, String hint, bool required) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(children: [
            Text(label, style: const TextStyle(fontSize: 12)),
            if (required)
              const Text(' *',
                  style: TextStyle(fontSize: 12, color: Color(0xFFE0A458))),
          ]),
          if (hint.isNotEmpty)
            Text(hint,
                style: const TextStyle(
                    fontSize: 11, height: 1.3, color: Colors.white30)),
        ],
      );

  /// `showHint` is false for a text field, which puts the hint inside itself;
  /// every other control has nowhere to put one, and losing it loses the line
  /// that explains what leaving the field blank MEANS.
  Widget _wrapped(String label, String hint, bool required, Widget control,
          {bool showHint = true}) =>
      Padding(
        padding: const EdgeInsets.only(bottom: 10),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(children: [
              Text(label, style: const TextStyle(fontSize: 12)),
              if (required)
                const Text(' *',
                    style: TextStyle(fontSize: 12, color: Color(0xFFE0A458))),
            ]),
            if (showHint && hint.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 2),
                child: Text(hint,
                    style: const TextStyle(
                        fontSize: 11, height: 1.3, color: Colors.white30)),
              ),
            const SizedBox(height: 5),
            control,
          ],
        ),
      );
}
