import 'dart:convert';

import 'package:flutter/material.dart';

/// The controls a plugin declared, drawn from the small fixed vocabulary.
///
/// One renderer, two callers: a start's form and a castle's options. They send
/// what they collect to different places, but what the controls ARE is the
/// same question — `text`, `longtext`, `toggle`, `number`, `choice`, `list`,
/// `file`, `json` — and two copies of these rules would be two copies to keep
/// in step. The degradations in particular are easy to get subtly wrong: a
/// `list` with no options is typed comma-separated and must still ARRIVE as a
/// list, and an unknown kind has to fall through to a text box rather than be
/// dropped.
///
/// **An unknown kind is drawn as a text box, never skipped.** The environment
/// gains behaviour by gaining plugins, and a plugin built against a newer
/// vocabulary than this binary must still be usable — degrading to something
/// you can type in always leaves it so.
///
/// Values are emitted on every change rather than read out at submit, so a
/// parent with a Save button holds the current answer without reaching inside.
class DeclaredForm extends StatefulWidget {
  const DeclaredForm({
    super.key,
    required this.inputs,
    required this.onChanged,
    this.initial = const {},
    this.keyPrefix = 'field',
  });

  /// As the server sends them: `{id, label, kind, hint, required, default,
  /// options, minimum, maximum}`.
  final List<Map<String, dynamic>> inputs;

  /// What is already stored, which beats each field's `default`.
  final Map<String, dynamic> initial;

  final ValueChanged<Map<String, dynamic>> onChanged;

  /// Prefixes every control's `ValueKey`, so a test can name one.
  final String keyPrefix;

  @override
  State<DeclaredForm> createState() => _DeclaredFormState();
}

class _DeclaredFormState extends State<DeclaredForm> {
  final Map<String, dynamic> _values = {};
  final Map<String, TextEditingController> _text = {};

  @override
  void initState() {
    super.initState();
    _build();
  }

  @override
  void didUpdateWidget(DeclaredForm old) {
    super.didUpdateWidget(old);
    // The declaration and the stored values both arrive over HTTP, so the
    // first build is often an empty form that fills in a moment later.
    if (old.inputs != widget.inputs || old.initial != widget.initial) {
      for (final c in _text.values) {
        c.dispose();
      }
      _text.clear();
      _values.clear();
      _build();
    }
  }

  void _build() {
    for (final i in widget.inputs) {
      final id = '${i['id']}';
      final kind = '${i['kind'] ?? 'text'}';
      final options = (i['options'] as List? ?? const []);
      final stored = widget.initial.containsKey(id) ? widget.initial[id] : null;
      final start = stored ?? i['default'];

      if (kind == 'toggle') {
        _values[id] = start == true;
      } else if (kind == 'list' && options.isNotEmpty) {
        _values[id] = [for (final v in (start as List? ?? const [])) '$v'];
      } else if (start != null) {
        _values[id] = start;
      }

      // A controller for every field that will RENDER as a text box — which
      // includes a `list` with no options to pick from, and any kind this
      // binary does not recognise. Deciding by kind alone left those two
      // drawn but not wired: you could type into them and nothing was sent.
      final typed = kind != 'toggle' &&
          kind != 'choice' &&
          !(kind == 'list' && options.isNotEmpty);
      if (typed) {
        _text[id] = TextEditingController(text: _asText(kind, start))
          ..addListener(_emit);
      }
    }
  }

  /// What a stored value looks like in a box.
  ///
  /// A `json` option is stored as the value it describes — the plugin reads a
  /// list, not a string it has to parse every run — so it has to be printed
  /// back out to be edited, and indented, because a platform list on one line
  /// is not something anyone will edit twice.
  String _asText(String kind, Object? value) {
    if (value == null) return '';
    if (kind == 'json') {
      try {
        return const JsonEncoder.withIndent('  ').convert(value);
      } catch (_) {
        return '$value';
      }
    }
    if (value is List) return value.join(', ');
    return '$value';
  }

  @override
  void dispose() {
    for (final c in _text.values) {
      c.dispose();
    }
    super.dispose();
  }

  /// Everything answered, in the shape the plugin declared.
  Map<String, dynamic> get values {
    final out = <String, dynamic>{..._values};
    final freeLists = {
      for (final i in widget.inputs)
        if ('${i['kind']}' == 'list' &&
            (i['options'] as List? ?? const []).isEmpty)
          '${i['id']}',
    };
    _text.forEach((id, c) {
      final typed = c.text.trim();
      if (typed.isEmpty) {
        // Blank and unanswered are the same thing — both fall back to the
        // plugin's default — so only one of them is ever sent.
        out.remove(id);
        return;
      }
      // A `list` with no options is typed comma-separated and must still
      // ARRIVE as a list: the plugin declared the kind, so sending a bare
      // string would make the wire disagree with the declaration for exactly
      // the fields nobody tested.
      out[id] = freeLists.contains(id)
          ? [for (final p in typed.split(',')) p.trim()]
              .where((p) => p.isNotEmpty)
              .toList()
          : typed;
    });
    return out;
  }

  void _emit() => widget.onChanged(values);

  void _set(String id, Object? value) {
    setState(() => _values[id] = value);
    _emit();
  }

  @override
  Widget build(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [for (final i in widget.inputs) _field(i)],
      );

  Widget _field(Map<String, dynamic> i) {
    final id = '${i['id']}';
    final label = '${i['label'] ?? id}';
    final hint = '${i['hint'] ?? ''}';
    final required = i['required'] == true;
    final options = [for (final o in (i['options'] as List? ?? const [])) '$o'];
    final key = ValueKey('${widget.keyPrefix}-$id');

    switch ('${i['kind'] ?? 'text'}') {
      case 'toggle':
        return Padding(
          padding: const EdgeInsets.only(bottom: 6),
          child: Row(children: [
            Switch(
              key: key,
              value: _values[id] == true,
              onChanged: (v) => _set(id, v),
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
                onSelected: (_) => _set(id, o),
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
                onSelected: (on) {
                  final next = [...picked];
                  on ? next.add(o) : next.remove(o);
                  _set(id, next);
                },
              ),
          ]),
        );
    }

    final kind = '${i['kind']}';
    final tall = kind == 'longtext' || kind == 'json';
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
        key: key,
        controller: _text[id],
        maxLines: tall ? (kind == 'json' ? 8 : 3) : 1,
        keyboardType: kind == 'number' ? TextInputType.number : null,
        style: TextStyle(
            fontSize: 12.5,
            fontFamily: kind == 'json' ? 'monospace' : null),
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
