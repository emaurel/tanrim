import 'package:flutter/material.dart';

import '../api/client.dart';
import '../model/castle.dart';
import 'input_form.dart';

/// What makes this castle different from another of the same plugin.
///
/// A castle is one running instance, and the point of having two is that they
/// do the same work on different subjects — two agencies, two repositories,
/// two mailboxes. Everything else a plugin declares is answered once at boot
/// with no castle in the signature, so without these there was nowhere to say
/// "this castle is THAT repository", and specialising a generic plugin meant
/// installing a second copy of it with the paths edited in.
///
/// The panel ships no fields of its own: the plugin declares them and this
/// draws whatever came back, through the same renderer a start's form uses.
class CastleOptions extends StatefulWidget {
  const CastleOptions({super.key, required this.api, required this.castle});

  final Api api;
  final Castle castle;

  @override
  State<CastleOptions> createState() => _CastleOptionsState();
}

class _CastleOptionsState extends State<CastleOptions> {
  List<Map<String, dynamic>> _declared = const [];
  Map<String, dynamic> _stored = const {};
  Map<String, dynamic> _values = {};
  bool _loading = true;
  bool _saving = false;
  String _problem = '';
  String _done = '';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final d = await widget.api.get('/castles/${widget.castle.id}/options')
          as Map;
      if (!mounted) return;
      setState(() {
        _declared = [
          for (final f in (d['declared'] as List? ?? const []))
            (f as Map).cast<String, dynamic>()
        ];
        _stored = (d['options'] as Map? ?? const {}).cast<String, dynamic>();
        _values = {..._stored};
        _loading = false;
      });
    } on ApiError catch (e) {
      if (mounted) setState(() => (_problem = e.message, _loading = false).$1);
    } catch (e) {
      if (mounted) setState(() => (_problem = '$e', _loading = false).$1);
    }
  }

  Future<void> _save() async {
    setState(() {
      _saving = true;
      _problem = '';
      _done = '';
    });
    try {
      final out = await widget.api
          .send('PUT', '/castles/${widget.castle.id}/options',
              {'options': _values}) as Map;
      // A refusal answers `ok: false` and a sentence rather than throwing, and
      // every problem with the form comes back at once — thirty fields
      // answered one mistake at a time is thirty round trips.
      if (out['ok'] == false) {
        setState(() => _problem = '${out['error'] ?? 'refused'}');
      } else {
        setState(() {
          _stored = (out['options'] as Map? ?? const {})
              .cast<String, dynamic>();
          _done = 'saved';
        });
      }
    } on ApiError catch (e) {
      setState(() => _problem = e.message);
    } catch (e) {
      setState(() => _problem = '$e');
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) {
      return const Padding(
        padding: EdgeInsets.all(24),
        child: Center(child: Text('loading…',
            style: TextStyle(color: Colors.white38, fontSize: 12))),
      );
    }
    if (_declared.isEmpty) {
      // Not an error, and the common case: most plugins have nothing that
      // differs between two of their castles.
      return Padding(
        padding: const EdgeInsets.all(20),
        child: Text(
          '${widget.castle.pluginName} has nothing that differs between one '
          'of its castles and another, so there is nothing to set here.',
          style: const TextStyle(
              fontSize: 12, height: 1.4, color: Colors.white38),
        ),
      );
    }
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 20),
      children: [
        Text('What ${widget.castle.name} works on',
            style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
        const SizedBox(height: 4),
        Text(
          'Declared by ${widget.castle.pluginName}, stored here and never '
          'written back to it. Another castle of the same plugin has its own.',
          style: const TextStyle(
              fontSize: 11.5, height: 1.35, color: Colors.white38),
        ),
        const SizedBox(height: 14),
        DeclaredForm(
          inputs: _declared,
          initial: _stored,
          keyPrefix: 'option',
          onChanged: (v) => _values = v,
        ),
        const SizedBox(height: 6),
        Row(children: [
          FilledButton(
            onPressed: _saving ? null : _save,
            child: Text(_saving ? 'saving…' : 'Save'),
          ),
          const SizedBox(width: 12),
          if (_problem.isNotEmpty)
            Expanded(
              child: Text(_problem,
                  style: const TextStyle(
                      fontSize: 11.5, height: 1.35, color: Color(0xFFE0A458))),
            )
          else if (_done.isNotEmpty)
            Text(_done,
                style: const TextStyle(
                    fontSize: 11.5, color: Color(0xFF9BC53D))),
        ]),
      ],
    );
  }
}
