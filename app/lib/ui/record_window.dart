import 'package:flutter/material.dart';

import '../api/client.dart';
import 'blocks.dart';

/// One record, as its plugin chose to show it.
///
/// The window fetches `/records/{id}/view` and draws whatever blocks come
/// back. It never learns what a record IS: a dossier with cited prices, a job
/// posting and a port survey all arrive as the same eight shapes, and a plugin
/// that declares no view at all gets one inferred from its JSON — so this
/// opens for every kind of work, including ones written after this app was
/// built.
class RecordWindow extends StatefulWidget {
  const RecordWindow({
    super.key,
    required this.api,
    required this.recordId,
    this.onOpenRoom,
  });

  final Api api;
  final String recordId;
  final void Function(String roomId)? onOpenRoom;

  @override
  State<RecordWindow> createState() => _RecordWindowState();
}

class _RecordWindowState extends State<RecordWindow> {
  Map<String, dynamic>? _view;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void didUpdateWidget(RecordWindow old) {
    super.didUpdateWidget(old);
    if (old.recordId != widget.recordId) {
      setState(() {
        _view = null;
        _error = null;
      });
      _load();
    }
  }

  Future<void> _load() async {
    try {
      final d = await widget.api.get('/records/${widget.recordId}/view');
      if (mounted) setState(() => _view = (d as Map).cast<String, dynamic>());
    } catch (e) {
      if (mounted) {
        setState(() => _error = e is ApiError ? e.message : '$e');
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_error != null) {
      return Padding(
        padding: const EdgeInsets.all(16),
        child: Text(_error!,
            style: const TextStyle(color: Color(0xFFE0A458), fontSize: 12)),
      );
    }
    final view = _view;
    if (view == null) {
      return const Center(
        child: SizedBox(
            width: 18,
            height: 18,
            child: CircularProgressIndicator(strokeWidth: 2)),
      );
    }

    final blocks = ((view['blocks'] ?? []) as List)
        .map((b) => (b as Map).cast<String, dynamic>())
        .toList();

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _header(view),
        Expanded(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(16, 4, 16, 20),
            children: [
              if (blocks.isEmpty)
                const Text('nothing recorded yet',
                    style: TextStyle(color: Colors.white38, fontSize: 12)),
              Blocks(blocks: blocks, onOpenRoom: widget.onOpenRoom),
            ],
          ),
        ),
      ],
    );
  }

  /// The machinery: what kind of work this is and where it has got to.
  ///
  /// Shown here rather than left to the plugin, because every record has a
  /// kind and a stage whatever else it has — and a view that could omit them
  /// would be a record you cannot place.
  Widget _header(Map<String, dynamic> view) => Padding(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 10),
        child: Wrap(spacing: 6, runSpacing: 6, children: [
          _chip('${view['kind'] ?? ''}'),
          _chip('${view['stage'] ?? ''}', accent: true),
        ]),
      );

  Widget _chip(String s, {bool accent = false}) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
        decoration: BoxDecoration(
          color: accent
              ? const Color(0xFF8ECAE6).withValues(alpha: .18)
              : Colors.white.withValues(alpha: .06),
          borderRadius: BorderRadius.circular(5),
        ),
        child: Text(s,
            style: TextStyle(
                fontSize: 11.5,
                color: accent ? const Color(0xFF8ECAE6) : null)),
      );
}
