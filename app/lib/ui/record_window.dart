import 'package:flutter/material.dart';

import 'dart:async';

import '../api/client.dart';
import '../api/live.dart';
import 'blocks.dart';
import 'settings_tab.dart';

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
    this.working = false,
    this.stages = const [],
    this.onDeleted,
    this.live,
  });

  final Api api;
  final String recordId;
  final void Function(String roomId)? onOpenRoom;

  /// An agent is busy on this record right now. Drives the dot and which of
  /// Start / Stop is offered.
  final bool working;

  /// Lines from the run in flight on this record, as they are said.
  ///
  /// A stream rather than a poll: a build talks for eleven minutes, and asking
  /// every second for a list that is usually unchanged is a request a second
  /// for the length of the run.
  final Stream<LiveEvent>? live;

  /// Every stage this record's pipeline declares, for the hand-move control.
  /// Comes from the server, because a plugin this build has never seen adds
  /// stages nothing here could name.
  final List<String> stages;

  /// Closed by the window that owns it, since a deleted record has nothing
  /// left to show.
  final VoidCallback? onDeleted;

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
    if (widget.working) _loadRun();
    _watchRun();
  }

  @override
  void dispose() {
    _sub?.cancel();
    super.dispose();
  }

  @override
  void didUpdateWidget(RecordWindow old) {
    super.didUpdateWidget(old);
    if (old.recordId != widget.recordId) {
      setState(() {
        _view = null;
        _error = null;
        _run = const [];
        _runWorkers = const [];
      });
      _load();
      _watchRun();
      if (widget.working) _loadRun();
    }
    // A run STARTING while the window is open: catch up on whatever it said
    // between being dispatched and this rebuild.
    if (!old.working && widget.working) _loadRun();
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

    // The history is its own tab, not the last card. It is the one part of a
    // record that always exists and always grows, so left in line it ends up
    // being the thing you scroll past to reach anything else.
    final history =
        blocks.where((b) => b['block'] == 'timeline').toList();
    final rest = blocks.where((b) => b['block'] != 'timeline').toList();

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _header(view),
        if (_showing != 'settings') _controls(view),
        _tabs(history),
        Expanded(
          child: _showing == 'settings'
              ? _settings(view)
              : _showing == 'run'
                  ? _currentRun()
                  : _body(history, rest),
        ),
      ],
    );
  }

  String? _tab;

  /// Which tab is showing.
  ///
  /// Resolved rather than stored, because the run tab COMES AND GOES: a run
  /// ends while you are reading it, and a stored `_tab` would leave the window
  /// showing a tab that is no longer there. Nothing to show falls back to the
  /// details.
  String get _showing {
    final t = _tab;
    if (t == 'run' && !widget.working) return 'details';
    if (t != null) return t;
    // A record you opened BECAUSE something is happening to it opens on what
    // is happening.
    return widget.working ? 'run' : 'details';
  }

  /// What the agent working this record has said, this run.
  ///
  /// Live only, and dropped when the run ends — which is what the server does
  /// too. A finished run leaves its history entry and its event; keeping a
  /// third copy is only a way for the three to disagree.
  List<Map<String, dynamic>> _run = const [];
  List<Map<String, dynamic>> _runWorkers = const [];
  StreamSubscription<LiveEvent>? _sub;

  void _watchRun() {
    _sub?.cancel();
    _sub = widget.live?.listen((e) {
      if (e.kind != LiveKind.runLine || e.from != widget.recordId) return;
      final line = e.line;
      if (line == null || !mounted) return;
      setState(() => _run = [..._run, line]);
    });
  }

  /// Everything said before this window opened.
  ///
  /// A run that has been going ten minutes has a story already; opening the
  /// window part way through and seeing only what happens NEXT would answer
  /// the question a sentence at a time.
  Future<void> _loadRun() async {
    try {
      final d = await widget.api.get('/records/${widget.recordId}/run') as Map;
      if (!mounted) return;
      setState(() {
        _run = [
          for (final l in (d['lines'] as List? ?? const []))
            (l as Map).cast<String, dynamic>()
        ];
        _runWorkers = [
          for (final w in (d['workers'] as List? ?? const []))
            (w as Map).cast<String, dynamic>()
        ];
      });
    } catch (_) {
      // Nothing to show is the normal answer here; it is not an error state.
    }
  }

  /// Built lazily, one top-level block at a time.
  ///
  /// `ListView(children: [...])` constructs every child up front, and a real
  /// dossier is six hundred rows and cells — which is the whole of the stall
  /// when a record opened. `ListView.builder` builds the ones on screen.
  ///
  /// One `SelectionArea` around the lot, so the text is still selectable
  /// without every value carrying its own selection machinery.
  Widget _body(List<Map<String, dynamic>> history,
      List<Map<String, dynamic>> rest) {
    final showing = _showing == 'history' ? history : rest;
    if (_showing != 'history' && rest.isEmpty) {
      return const Padding(
        padding: EdgeInsets.all(16),
        child: Text('nothing recorded yet',
            style: TextStyle(color: Colors.white38, fontSize: 12)),
      );
    }
    return SelectionArea(
      child: ListView.builder(
        key: ValueKey(_showing),
        padding: const EdgeInsets.fromLTRB(16, 10, 16, 20),
        itemCount: showing.length,
        itemBuilder: (_, i) => Blocks(
          blocks: [showing[i]],
          onOpenRoom: widget.onOpenRoom,
        ),
      ),
    );
  }

  Widget _settings(Map<String, dynamic> view) => ListView(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 20),
        children: [
          SettingsSection(
            title: 'Stage',
            note: 'The pipeline moves a record on its own. This is the escape '
                'hatch for when it is wrong and no card exists to say so — the '
                'reason is written into the history, where it is the only '
                'account of why this moved.',
            children: [
              ReadOnlyRow(label: 'Now at', value: '${view['stage'] ?? ''}'),
              ReadOnlyRow(label: 'Kind', value: '${view['kind'] ?? ''}'),
              const SizedBox(height: 8),
              OutlinedButton.icon(
                onPressed: _busy ? null : _move,
                icon: const Icon(Icons.alt_route_rounded, size: 16),
                label: const Text('Move it by hand'),
              ),
            ],
          ),
          SettingsSection(
            title: 'Delete',
            note: 'Whatever is working it is stopped first, and any card '
                'waiting on it is resolved — both are about a record that will '
                'not exist.',
            children: [
              DangerButton(
                label: 'Delete this record',
                title: 'Delete ${view['name'] ?? 'this record'}?',
                explain: 'Its history, its dossier and everything any agent '
                    'produced for it go with it. This cannot be undone.\n\n'
                    'Files already built for it stay on disk.',
                onConfirmed: () async {
                  await _act(() async {
                    await widget.api.send(
                        'DELETE', '/records/${widget.recordId}');
                    widget.onDeleted?.call();
                    return 'deleted';
                  });
                },
              ),
            ],
          ),
        ],
      );

  /// What the agent on this record is doing, as it does it.
  ///
  /// Newest LAST and scrolled to the bottom, like a log: the question is what
  /// it is doing now, and the answer is at the end of what it has said.
  Widget _currentRun() {
    if (_run.isEmpty) {
      return const Padding(
        padding: EdgeInsets.all(16),
        child: Text('working — nothing said yet',
            style: TextStyle(color: Colors.white38, fontSize: 12)),
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (_runWorkers.isNotEmpty)
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 2),
            child: Text(
              _runWorkers
                  .map((w) => '${w['id']}'
                      '${w['workbench'] == null ? '' : ' · ${w['workbench']}'}')
                  .join(', '),
              style: const TextStyle(fontSize: 11.5, color: Colors.white38),
            ),
          ),
        Expanded(
          child: SelectionArea(
            child: ListView.builder(
              key: const ValueKey('run'),
              reverse: true,
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 12),
              itemCount: _run.length,
              // Reversed, so it sticks to the newest line without anyone
              // having to drive a scroll controller as lines arrive.
              itemBuilder: (_, i) => _runLine(_run[_run.length - 1 - i]),
            ),
          ),
        ),
      ],
    );
  }

  Widget _runLine(Map<String, dynamic> line) {
    final tool = line['kind'] == 'tool';
    return Padding(
      padding: const EdgeInsets.only(bottom: 7),
      child: Text(
        '${line['text']}',
        style: TextStyle(
          fontSize: tool ? 11.5 : 12.5,
          height: 1.4,
          // A tool call is what it DID; prose is what it thinks. Telling them
          // apart at a glance is most of the value of watching at all.
          color: tool ? const Color(0xFF8FB8D9) : Colors.white70,
          fontFamily: tool ? 'monospace' : null,
        ),
      ),
    );
  }

  Widget _tabs(List<Map<String, dynamic>> history) {
    final steps =
        ((history.isEmpty ? const [] : history.first['steps'] ?? const [])
            as List).length;
    return SizedBox(
      height: 32,
      child: ListView(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: 12),
        children: [
          // FIRST, and only while something is running. A tab that is empty
          // most of the time trains you to skip it; the whole value of this
          // one is that its presence means work is happening right now.
          if (widget.working) ...[
            _tabButton('run', 'Current run'),
            const SizedBox(width: 6),
          ],
          _tabButton('details', 'Details'),
          const SizedBox(width: 6),
          _tabButton('history', 'History ($steps)'),
          const SizedBox(width: 6),
          _tabButton('settings', 'Settings'),
        ],
      ),
    );
  }

  Widget _tabButton(String id, String label) {
    final on = _showing == id;
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

  /// The machinery: what kind of work this is and where it has got to.
  ///
  /// Shown here rather than left to the plugin, because every record has a
  /// kind and a stage whatever else it has — and a view that could omit them
  /// would be a record you cannot place.
  Widget _header(Map<String, dynamic> view) => Padding(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
        child: Wrap(
          spacing: 6,
          runSpacing: 6,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            if (widget.working) ...[
              const _Dot(),
              const Text('working',
                  style: TextStyle(fontSize: 11.5, color: Color(0xFF6BD68A))),
            ],
            _chip('${view['kind'] ?? ''}'),
            _chip('${view['stage'] ?? ''}', accent: true),
          ],
        ),
      );

  /// Start, stop, and move it by hand.
  ///
  /// All three existed as routes months before anything could reach them: the
  /// retired web client had the buttons and the app never grew them, so the
  /// only way to start a stalled record was curl.
  Widget _controls(Map<String, dynamic> view) => Padding(
        padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
        child: Row(children: [
          if (widget.working)
            _button('Stop', Icons.stop_circle_outlined, _stop,
                tone: const Color(0xFFE0A458))
          else
            _button('Start', Icons.play_arrow_rounded, _start),
          const SizedBox(width: 8),
          _button('Move…', Icons.alt_route_rounded, _move),
          const Spacer(),
          if (_busy)
            const SizedBox(
                width: 14,
                height: 14,
                child: CircularProgressIndicator(strokeWidth: 2)),
        ]),
      );

  bool _busy = false;

  Widget _button(String label, IconData icon, Future<void> Function() run,
          {Color? tone}) =>
      OutlinedButton.icon(
        onPressed: _busy ? null : () => run(),
        icon: Icon(icon, size: 16),
        label: Text(label, style: const TextStyle(fontSize: 12)),
        style: OutlinedButton.styleFrom(
          foregroundColor: tone,
          visualDensity: VisualDensity.compact,
        ),
      );

  /// Run whatever the record's stage says comes next.
  ///
  /// The server picks the room — the pipeline already knows which one works
  /// this stage, and a client that guessed would be a second router to keep
  /// in step with the manifests.
  Future<void> _start() => _act(() async {
        final out = await widget.api.post(
            '/leads/${widget.recordId}/run-next');
        return 'started ${(out as Map)['started'] ?? ''}'.trim();
      });

  Future<void> _stop() => _act(() async {
        final out = await widget.api
            .post('/leads/${widget.recordId}/stop', {'reason': 'stopped from the panel'});
        final m = (out as Map);
        // `ok: false` with a sentence, not an HTTP error: nothing running is a
        // normal answer to "stop", not a failure.
        if (m['ok'] == false) return '${m['error']}';
        return 'stopped ${(m['stopped'] as List?)?.join(', ') ?? ''}'.trim();
      });

  Future<void> _move() async {
    final choice = await showDialog<_Move>(
      context: context,
      builder: (_) => _MoveDialog(
        stages: widget.stages,
        current: '${(_view ?? const {})['stage'] ?? ''}',
      ),
    );
    if (choice == null) return;
    await _act(() async {
      try {
        await widget.api.post('/leads/${widget.recordId}/stage',
            {'stage': choice.stage, 'reason': choice.reason});
      } on ApiError catch (e) {
        // A 409 is the rework guard ADVISING, with a sentence explaining what
        // the move would mean. Showing it and offering to go on is the whole
        // point of it being advice rather than a refusal.
        if (e.status != 409 || !mounted) rethrow;
        final go = await _confirm(e.message);
        if (!go) return 'not moved';
        await widget.api.post('/leads/${widget.recordId}/stage',
            {'stage': choice.stage, 'reason': choice.reason, 'force': true});
      }
      return 'moved to ${choice.stage}';
    });
  }

  Future<bool> _confirm(String message) async =>
      await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: const Text('Move it anyway?'),
          content: SingleChildScrollView(
              child: Text(message, style: const TextStyle(fontSize: 13))),
          actions: [
            TextButton(
                onPressed: () => Navigator.pop(ctx, false),
                child: const Text('Cancel')),
            FilledButton(
                onPressed: () => Navigator.pop(ctx, true),
                child: const Text('Move anyway')),
          ],
        ),
      ) ??
      false;

  /// Run one action, show what it said, and reload.
  ///
  /// A refusal is not an error here — half of them are deliberate, and the
  /// sentence the server sends back is the useful part.
  Future<void> _act(Future<String> Function() run) async {
    setState(() => _busy = true);
    String message;
    try {
      message = await run();
    } on ApiError catch (e) {
      message = e.message;
    } catch (e) {
      message = '$e';
    }
    if (!mounted) return;
    setState(() => _busy = false);
    if (message.isNotEmpty) {
      ScaffoldMessenger.maybeOf(context)?.showSnackBar(
          SnackBar(content: Text(message), duration: const Duration(seconds: 4)));
    }
    await _load();
  }

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

class _Dot extends StatelessWidget {
  const _Dot();

  @override
  Widget build(BuildContext context) => Container(
        width: 8,
        height: 8,
        decoration: const BoxDecoration(
            color: Color(0xFF6BD68A), shape: BoxShape.circle),
      );
}

/// A hand-move: where to, and why.
class _Move {
  const _Move(this.stage, this.reason);
  final String stage;
  final String reason;
}

/// The reason is not optional decoration.
///
/// `advance_record` writes it into the record's history, which is the only
/// place "who moved this, and why" is answerable from later. A hand-move with
/// no note is a stage that changed for reasons nobody can reconstruct.
class _MoveDialog extends StatefulWidget {
  const _MoveDialog({required this.stages, required this.current});

  final List<String> stages;
  final String current;

  @override
  State<_MoveDialog> createState() => _MoveDialogState();
}

class _MoveDialogState extends State<_MoveDialog> {
  String? _stage;
  final _reason = TextEditingController();

  @override
  void dispose() {
    _reason.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final options =
        widget.stages.where((s) => s != widget.current).toList();
    return AlertDialog(
      title: const Text('Move this record'),
      content: SizedBox(
        width: 360,
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          if (options.isEmpty)
            const Text('the server listed no stages to move to',
                style: TextStyle(fontSize: 12, color: Colors.white54))
          else
            DropdownButtonFormField<String>(
              initialValue: _stage,
              isExpanded: true,
              decoration: const InputDecoration(labelText: 'to'),
              items: [
                for (final s in options)
                  DropdownMenuItem(value: s, child: Text(s)),
              ],
              onChanged: (v) => setState(() => _stage = v),
            ),
          const SizedBox(height: 12),
          TextField(
            controller: _reason,
            maxLines: 2,
            decoration: const InputDecoration(
              labelText: 'why',
              helperText: 'goes into the history, where it is the only record '
                  'of why this moved',
            ),
            onChanged: (_) => setState(() {}),
          ),
        ]),
      ),
      actions: [
        TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Cancel')),
        FilledButton(
          onPressed: _stage == null || _reason.text.trim().isEmpty
              ? null
              : () => Navigator.pop(
                  context, _Move(_stage!, _reason.text.trim())),
          child: const Text('Move'),
        ),
      ],
    );
  }
}
