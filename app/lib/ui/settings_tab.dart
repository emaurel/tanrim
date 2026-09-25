import 'package:flutter/material.dart';

/// The pieces every window's Settings tab is built from.
///
/// One file rather than four copies: a castle, a room, a record and the
/// kingdom all edit different things, but they all present a labelled field, a
/// set of things to tick, and an action you cannot take back — and those three
/// should look and behave the same wherever you meet them.
class SettingsSection extends StatelessWidget {
  const SettingsSection({
    super.key,
    required this.title,
    required this.children,
    this.note = '',
  });

  final String title;
  final String note;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: 20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(title.toUpperCase(),
                style: const TextStyle(
                    fontSize: 10.5,
                    letterSpacing: 1.1,
                    fontWeight: FontWeight.w700,
                    color: Colors.white38)),
            if (note.isNotEmpty) ...[
              const SizedBox(height: 4),
              Text(note,
                  style: const TextStyle(
                      fontSize: 11.5, color: Colors.white38, height: 1.45)),
            ],
            const SizedBox(height: 8),
            ...children,
          ],
        ),
      );
}

/// A field you edit and then commit, rather than one that saves per keystroke.
///
/// Explicit because every one of these is a write to the server: a name that
/// PATCHed on each character would send eleven requests to type "Web agency".
class EditableField extends StatefulWidget {
  const EditableField({
    super.key,
    required this.label,
    required this.value,
    required this.onSubmit,
    this.hint = '',
    this.numeric = false,
  });

  final String label;
  final String value;
  final String hint;
  final bool numeric;

  /// Returns a problem to show, or empty when it worked.
  final Future<String> Function(String) onSubmit;

  @override
  State<EditableField> createState() => _EditableFieldState();
}

class _EditableFieldState extends State<EditableField> {
  late final TextEditingController _c =
      TextEditingController(text: widget.value);
  String _problem = '';
  bool _busy = false;

  @override
  void didUpdateWidget(EditableField old) {
    super.didUpdateWidget(old);
    // Only when the field is not being edited, or a refresh arriving mid-type
    // would take the cursor with it.
    if (old.value != widget.value && _c.text == old.value) {
      _c.text = widget.value;
    }
  }

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  bool get _changed => _c.text.trim() != widget.value.trim();

  Future<void> _save() async {
    setState(() {
      _busy = true;
      _problem = '';
    });
    final problem = await widget.onSubmit(_c.text.trim());
    if (!mounted) return;
    setState(() {
      _busy = false;
      _problem = problem;
    });
  }

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: 10),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(children: [
              Expanded(
                child: TextField(
                  controller: _c,
                  keyboardType:
                      widget.numeric ? TextInputType.number : TextInputType.text,
                  style: const TextStyle(fontSize: 13),
                  decoration: InputDecoration(
                    labelText: widget.label,
                    hintText: widget.hint,
                    isDense: true,
                  ),
                  onChanged: (_) => setState(() {}),
                  onSubmitted: (_) => _changed ? _save() : null,
                ),
              ),
              const SizedBox(width: 8),
              if (_busy)
                const SizedBox(
                    width: 14,
                    height: 14,
                    child: CircularProgressIndicator(strokeWidth: 2))
              else
                TextButton(
                  onPressed: _changed ? _save : null,
                  child: const Text('Save', style: TextStyle(fontSize: 12)),
                ),
            ]),
            if (_problem.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 2),
                child: Text(_problem,
                    style: const TextStyle(
                        fontSize: 11.5, color: Color(0xFFE0A458))),
              ),
          ],
        ),
      );
}

/// Tick which of a set of things apply. Commits on each change, because a
/// checkbox that needed a Save button would be the only one in the window.
class ChipPicker extends StatelessWidget {
  const ChipPicker({
    super.key,
    required this.options,
    required this.selected,
    required this.onChanged,
    this.empty = 'nothing installed',
    this.describe,
  });

  final List<String> options;
  final Set<String> selected;
  final void Function(Set<String>) onChanged;
  final String empty;
  final String? Function(String)? describe;

  @override
  Widget build(BuildContext context) {
    if (options.isEmpty) {
      return Text(empty,
          style: const TextStyle(fontSize: 12, color: Colors.white38));
    }
    return Wrap(
      spacing: 6,
      runSpacing: 6,
      children: [
        for (final o in options)
          Tooltip(
            message: describe?.call(o) ?? o,
            child: FilterChip(
              label: Text(o, style: const TextStyle(fontSize: 11.5)),
              selected: selected.contains(o),
              visualDensity: VisualDensity.compact,
              onSelected: (on) => onChanged(
                  {...selected}..removeWhere((x) => !on && x == o)
                    ..addAll(on ? {o} : const {})),
            ),
          ),
      ],
    );
  }
}

/// Something you cannot take back. Always behind a confirmation that says what
/// happens, because "are you sure?" tells a person nothing they did not know.
class DangerButton extends StatelessWidget {
  const DangerButton({
    super.key,
    required this.label,
    required this.title,
    required this.explain,
    required this.onConfirmed,
  });

  final String label;
  final String title;
  final String explain;
  final Future<void> Function() onConfirmed;

  @override
  Widget build(BuildContext context) => OutlinedButton.icon(
        icon: const Icon(Icons.warning_amber_rounded, size: 16),
        label: Text(label),
        style: OutlinedButton.styleFrom(foregroundColor: Colors.red.shade300),
        onPressed: () async {
          final go = await showDialog<bool>(
            context: context,
            builder: (ctx) => AlertDialog(
              title: Text(title),
              content: Text(explain, style: const TextStyle(fontSize: 13)),
              actions: [
                TextButton(
                    onPressed: () => Navigator.pop(ctx, false),
                    child: const Text('Cancel')),
                FilledButton(
                  onPressed: () => Navigator.pop(ctx, true),
                  style: FilledButton.styleFrom(
                      backgroundColor: Colors.red.shade400),
                  child: Text(label),
                ),
              ],
            ),
          );
          if (go == true) await onConfirmed();
        },
      );
}

/// A fact the window shows but nobody may edit here.
///
/// Benches and room geometry are the routing table — which stages a room works
/// is what makes those stages reachable at all — so they are shown and not
/// offered, rather than hidden as though they did not exist.
class ReadOnlyRow extends StatelessWidget {
  const ReadOnlyRow({super.key, required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: 5),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          SizedBox(
            width: 110,
            child: Text(label,
                style: const TextStyle(fontSize: 12, color: Colors.white38)),
          ),
          Expanded(
              child: Text(value, style: const TextStyle(fontSize: 12.5))),
        ]),
      );
}
