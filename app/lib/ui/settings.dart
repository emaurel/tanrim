import 'dart:async';

import 'package:flutter/material.dart';

import '../server/process.dart';
import 'menu.dart';

/// The settings menu.
///
/// Built from [MenuTab]s so a new tab is one entry in this list — and so the
/// next menu (plugins, castles, a server picker) reuses the same shell rather
/// than inventing its own.
class Settings {
  const Settings({
    required this.server,
    required this.onConnect,
    required this.connected,
    required this.status,
    required this.rooms,
    required this.plugins,
    required this.stages,
    required this.kinds,
    required this.onReload,
    required this.repo,
    required this.process,
    required this.onStartServer,
    required this.onStopServer,
    required this.catalog,
    required this.onInstall,
    required this.onSetEnabled,
    required this.onRemove,
    required this.onRefreshPlugins,
  });

  final TextEditingController server;
  final VoidCallback onConnect;
  final bool connected;
  final String status;
  final int rooms;

  /// What `/plugins` reported, one entry per installed plugin.
  final List<Map<String, dynamic>> plugins;
  final List<String> stages;
  final List<String> kinds;

  /// Re-read `plugins/` on the server. Returns what happened, for the panel
  /// to show — including a refusal, which is a normal outcome rather than an
  /// error: a reload while an agent is running is declined on purpose.
  final Future<String> Function() onReload;

  /// Where the checkout is, so the app can run the server itself rather than
  /// waiting for somebody to do it in a terminal.
  final TextEditingController repo;
  final ServerProcess process;
  final Future<String> Function() onStartServer;
  final Future<String> Function() onStopServer;

  /// Every plugin ON DISK, running or not — `/plugins/catalog`. The installed
  /// list cannot describe one that is switched off, and something you cannot
  /// see is something you cannot switch back on.
  final List<Map<String, dynamic>> catalog;
  final Future<String> Function(String source) onInstall;
  final Future<String> Function(String id, bool enabled) onSetEnabled;
  final Future<String> Function(String id, bool force) onRemove;

  /// Refetch `(catalog, installed)` after something changed. The panel is a
  /// modal built from a snapshot, so without this it would keep showing the
  /// world as it was when it opened.
  final Future<(List<Map<String, dynamic>>, List<Map<String, dynamic>>)>
      Function() onRefreshPlugins;

  Future<void> open(BuildContext context, {String? tab}) => MenuPanel.show(
        context,
        title: 'Settings',
        initialTab: tab,
        tabs: [
          MenuTab(
            id: 'connection',
            title: 'Server',
            icon: Icons.dns_outlined,
            build: _connection,
          ),
          MenuTab(
            id: 'plugins',
            title: 'Plugins',
            icon: Icons.extension_outlined,
            badge: plugins.length,
            build: _plugins,
          ),
          MenuTab(
            id: 'pipeline',
            title: 'Pipeline',
            icon: Icons.account_tree_outlined,
            build: _pipeline,
          ),
          MenuTab(
            id: 'about',
            title: 'About',
            icon: Icons.info_outline,
            build: _about,
          ),
        ],
      );

  Widget _connection(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          MenuSection(
            title: 'The environment',
            note: 'The checkout holding backend/tanrim and .venv. Given one, '
                'the app can run the server itself — and will stop it again '
                'when the app closes, so a forgotten process is not left '
                'holding the port.',
            children: [
              TextField(
                controller: repo,
                style: const TextStyle(fontSize: 13),
                decoration: const InputDecoration(
                  isDense: true,
                  border: OutlineInputBorder(),
                  labelText: 'Path to agent_environment',
                  hintText: '/home/you/fun/agent_environment',
                ),
              ),
              const SizedBox(height: 12),
              _ServerControls(
                process: process,
                onStart: onStartServer,
                onStop: onStopServer,
              ),
            ],
          ),
          MenuSection(
            title: 'Connect to',
            note: 'Usually the one above. Point it elsewhere to watch a '
                'server running on another machine — the app only ever reads '
                'and writes over HTTP.',
            children: [
              Row(children: [
                Expanded(
                  child: TextField(
                    controller: server,
                    style: const TextStyle(fontSize: 13),
                    decoration: const InputDecoration(
                      isDense: true,
                      border: OutlineInputBorder(),
                      hintText: 'http://127.0.0.1:8765',
                    ),
                    onSubmitted: (_) => onConnect(),
                  ),
                ),
                const SizedBox(width: 10),
                FilledButton.tonal(
                    onPressed: onConnect, child: const Text('Connect')),
              ]),
              const SizedBox(height: 12),
              Row(children: [
                Icon(Icons.circle,
                    size: 10,
                    color: connected
                        ? const Color(0xFF63C77B)
                        : Colors.orange),
                const SizedBox(width: 8),
                Text(status,
                    style: const TextStyle(color: Colors.white60)),
              ]),
            ],
          ),
        ],
      );

  /// The plugins tab, which owns its own copy of what is installed.
  ///
  /// Its own widget because this panel is a modal built from a SNAPSHOT: when
  /// the app refetched the catalog after a switch was thrown, the open panel
  /// never heard about it and the switch stayed where it was, showing the
  /// opposite of what had just happened.
  Widget _plugins(BuildContext context) => _PluginsTab(
        catalog: catalog,
        plugins: plugins,
        onRefresh: onRefreshPlugins,
        onSetEnabled: onSetEnabled,
        onRemove: onRemove,
        onInstall: onInstall,
        onReload: onReload,
      );

  Widget _pipeline(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          MenuSection(
            title: 'Kinds of work',
            note: 'One pipeline per kind. A plugin declares its own.',
            children: [
              Wrap(
                  spacing: 6,
                  runSpacing: 6,
                  children: [for (final k in kinds) _chip(k)]),
            ],
          ),
          MenuSection(
            title: 'Stages',
            note: 'In the order the server gives them. Nothing in this app '
                'names a stage — they all arrive from whatever is installed.',
            children: [
              Wrap(
                  spacing: 6,
                  runSpacing: 6,
                  children: [for (final s in stages) _chip(s)]),
            ],
          ),
        ],
      );

  Widget _about(BuildContext context) => const Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          MenuSection(
            title: 'Tanrim',
            note: 'An agent-operated workshop, drawn as a place. Each plugin '
                'is a castle; each room is a step; each sprite is a worker. '
                'Two operator gates stand between an agent and anything that '
                'reaches a stranger.',
            children: [],
          ),
          MenuSection(
            title: 'The map',
            note: 'Scroll to zoom, drag to pan. Zoom out far enough and the '
                'rooms give way to the castles they belong to; click one to '
                'travel to it.',
            children: [],
          ),
        ],
      );

  Widget _chip(String s) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
        decoration: BoxDecoration(
          color: Colors.white.withValues(alpha: .06),
          borderRadius: BorderRadius.circular(5),
        ),
        child: Text(s, style: const TextStyle(fontSize: 11.5)),
      );
}

/// The reload button, and what the server said.
///
/// Its own widget because the panel it sits in is a plain builder, and because
/// the interesting part is the ANSWER: a refusal ("3 agent runs in flight") and
/// a plugin that would not boot are both normal outcomes worth reading, not
/// errors to swallow. A reload that silently did nothing would be worse than
/// no button.
class _ReloadButton extends StatefulWidget {
  const _ReloadButton({required this.onReload});

  final Future<String> Function() onReload;

  @override
  State<_ReloadButton> createState() => _ReloadButtonState();
}

class _ReloadButtonState extends State<_ReloadButton> {
  bool _busy = false;
  String? _said;

  Future<void> _go() async {
    setState(() {
      _busy = true;
      _said = null;
    });
    String out;
    try {
      out = await widget.onReload();
    } catch (e) {
      out = '$e';
    }
    if (!mounted) return;
    setState(() {
      _busy = false;
      _said = out;
    });
  }

  @override
  Widget build(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(children: [
            FilledButton.tonalIcon(
              onPressed: _busy ? null : _go,
              icon: _busy
                  ? const SizedBox(
                      width: 13,
                      height: 13,
                      child: CircularProgressIndicator(strokeWidth: 2))
                  : const Icon(Icons.refresh, size: 16),
              label: Text(_busy ? 'Reading plugins/…' : 'Reload plugins'),
            ),
          ]),
          if (_said != null) ...[
            const SizedBox(height: 10),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(10),
              decoration: BoxDecoration(
                color: Colors.white.withValues(alpha: .04),
                borderRadius: BorderRadius.circular(6),
              ),
              child: Text(_said!,
                  style: const TextStyle(
                      fontSize: 12, color: Colors.white70, height: 1.45)),
            ),
          ],
        ],
      );
}

/// Start and stop the server, and show what it said.
///
/// The log matters more than the buttons. A server that exits two seconds
/// after starting — a missing venv, a taken port, a plugin that will not
/// import — explains itself on stderr and is then gone, and without the last
/// lines here the app could only report that it is not running.
class _ServerControls extends StatefulWidget {
  const _ServerControls({
    required this.process,
    required this.onStart,
    required this.onStop,
  });

  final ServerProcess process;
  final Future<String> Function() onStart;
  final Future<String> Function() onStop;

  @override
  State<_ServerControls> createState() => _ServerControlsState();
}

class _ServerControlsState extends State<_ServerControls> {
  StreamSubscription<void>? _sub;
  bool _busy = false;
  String? _said;

  @override
  void initState() {
    super.initState();
    _sub = widget.process.changes.listen((_) {
      if (mounted) setState(() {});
    });
    widget.process.refresh();
  }

  @override
  void dispose() {
    _sub?.cancel();
    super.dispose();
  }

  Future<void> _run(Future<String> Function() action) async {
    setState(() {
      _busy = true;
      _said = null;
    });
    String out;
    try {
      out = await action();
    } catch (e) {
      out = '$e';
    }
    if (!mounted) return;
    setState(() {
      _busy = false;
      _said = out.isEmpty ? null : out;
    });
  }

  @override
  Widget build(BuildContext context) {
    final p = widget.process;
    final problem = p.checkRepo();
    final (String label, Color colour) = p.running
        ? ('running — started by this app', const Color(0xFF63C77B))
        : p.adopted
            ? ('running — started elsewhere', const Color(0xFFE0A458))
            : ('not running', Colors.white38);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(children: [
          Icon(Icons.circle, size: 10, color: colour),
          const SizedBox(width: 8),
          Expanded(
            child: Text(label,
                style: const TextStyle(color: Colors.white60, fontSize: 12.5)),
          ),
        ]),
        const SizedBox(height: 10),
        Wrap(spacing: 8, runSpacing: 8, children: [
          FilledButton.tonalIcon(
            onPressed: (_busy || p.running) ? null : () => _run(widget.onStart),
            icon: _busy
                ? const SizedBox(
                    width: 13, height: 13,
                    child: CircularProgressIndicator(strokeWidth: 2))
                : const Icon(Icons.play_arrow, size: 17),
            label: const Text('Start'),
          ),
          OutlinedButton.icon(
            // Deliberately not offered for a server this app did not start:
            // it would look like Stop working while taking down something
            // else, with its own flags and its own log.
            onPressed: (_busy || !p.running) ? null : () => _run(widget.onStop),
            icon: const Icon(Icons.stop, size: 17),
            label: const Text('Stop'),
          ),
        ]),
        if (problem != null && !p.running && !p.adopted) ...[
          const SizedBox(height: 10),
          Text(problem,
              style: const TextStyle(fontSize: 12, color: Color(0xFFE0A458))),
        ],
        if (_said != null) ...[
          const SizedBox(height: 10),
          Text(_said!, style: const TextStyle(fontSize: 12, color: Colors.white70)),
        ],
        if (p.log.isNotEmpty) ...[
          const SizedBox(height: 12),
          Container(
            width: double.infinity,
            constraints: const BoxConstraints(maxHeight: 190),
            padding: const EdgeInsets.all(10),
            decoration: BoxDecoration(
              color: Colors.black.withValues(alpha: .35),
              borderRadius: BorderRadius.circular(6),
            ),
            child: SingleChildScrollView(
              reverse: true,
              child: SelectableText(
                p.log.join('\n'),
                style: const TextStyle(
                    fontSize: 11, height: 1.5, fontFamily: 'monospace',
                    color: Colors.white60),
              ),
            ),
          ),
        ],
      ],
    );
  }
}

/// Clone a plugin repository into `plugins/`.
class _InstallField extends StatefulWidget {
  const _InstallField({required this.onInstall});

  final Future<String> Function(String source) onInstall;

  @override
  State<_InstallField> createState() => _InstallFieldState();
}

class _InstallFieldState extends State<_InstallField> {
  final _source = TextEditingController();
  bool _busy = false;
  String? _said;

  @override
  void dispose() {
    _source.dispose();
    super.dispose();
  }

  Future<void> _go() async {
    final src = _source.text.trim();
    if (src.isEmpty) return;
    setState(() {
      _busy = true;
      _said = null;
    });
    String out;
    try {
      out = await widget.onInstall(src);
    } catch (e) {
      out = '$e';
    }
    if (!mounted) return;
    setState(() {
      _busy = false;
      _said = out;
      if (out.startsWith('Installed')) _source.clear();
    });
  }

  @override
  Widget build(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(children: [
            Expanded(
              child: TextField(
                controller: _source,
                style: const TextStyle(fontSize: 13),
                decoration: const InputDecoration(
                  isDense: true,
                  border: OutlineInputBorder(),
                  hintText: 'git@github.com:you/your-plugin.git',
                ),
                onSubmitted: (_) => _busy ? null : _go(),
              ),
            ),
            const SizedBox(width: 10),
            FilledButton.tonal(
              onPressed: _busy ? null : _go,
              child: Text(_busy ? 'Cloning…' : 'Clone'),
            ),
          ]),
          if (_said != null) ...[
            const SizedBox(height: 10),
            Text(_said!,
                style: const TextStyle(
                    fontSize: 12, color: Colors.white70, height: 1.45)),
          ],
        ],
      );
}

/// The plugins tab.
///
/// Stateful because the panel around it is a modal built from a snapshot of
/// the app's state. Throwing a switch changed the server and the app's own
/// lists, and the open panel — holding the values it was constructed with —
/// went on drawing the switch where it had been. So this keeps its own copy
/// and refetches after every change.
///
/// The cards come from the CATALOG rather than from what is running, because a
/// disabled plugin is not running and has to be visible to be switched back
/// on. What is running supplies the detail; a disabled one has a card with a
/// switch and not much else, which is the honest amount to know about
/// something that has not been imported.
class _PluginsTab extends StatefulWidget {
  const _PluginsTab({
    required this.catalog,
    required this.plugins,
    required this.onRefresh,
    required this.onSetEnabled,
    required this.onRemove,
    required this.onInstall,
    required this.onReload,
  });

  final List<Map<String, dynamic>> catalog;
  final List<Map<String, dynamic>> plugins;
  final Future<(List<Map<String, dynamic>>, List<Map<String, dynamic>>)>
      Function() onRefresh;
  final Future<String> Function(String id, bool enabled) onSetEnabled;
  final Future<String> Function(String id, bool force) onRemove;
  final Future<String> Function(String source) onInstall;
  final Future<String> Function() onReload;

  @override
  State<_PluginsTab> createState() => _PluginsTabState();
}

class _PluginsTabState extends State<_PluginsTab> {
  late List<Map<String, dynamic>> _catalog = widget.catalog;
  late List<Map<String, dynamic>> _plugins = widget.plugins;
  String? _busy;

  Future<void> _refresh() async {
    final (catalog, plugins) = await widget.onRefresh();
    if (!mounted) return;
    setState(() {
      _catalog = catalog;
      _plugins = plugins;
    });
  }

  Future<void> _act(String id, Future<String> Function() action) async {
    setState(() => _busy = id);
    try {
      await action();
    } catch (_) {
      // The refresh below is what tells the truth either way: if it did not
      // work, the switch comes back showing what is actually on disk.
    }
    await _refresh();
    if (mounted) setState(() => _busy = null);
  }

  /// What `/plugins` says about this directory, if it is running.
  Map<String, dynamic>? _detail(String id) {
    for (final p in _plugins) {
      if (p['id'] == id) return p;
    }
    return null;
  }

  /// Which plugin each one hangs off, if any.
  ///
  /// An extension declares what it `requires`, and that is the edge — the same
  /// declaration that orders the boot. `website_recreation` requires
  /// `web_agency`, adds benches to two of its rooms and declares none of its
  /// own, so it belongs INSIDE that castle rather than beside it. Flat, the
  /// list said "3 plugins" while the map showed two castles, and nothing
  /// explained the difference.
  String? _parentOf(String id) {
    final detail = _detail(id);
    if (detail == null) return null;          // not running: nothing to nest by
    final ids = {for (final e in _catalog) e['id'] as String};
    for (final need in (detail['requires'] as List?) ?? const []) {
      if (ids.contains(need)) return need as String;
    }
    return null;
  }

  @override
  Widget build(BuildContext context) {
    final cards = <Widget>[];
    for (final entry in _catalog) {
      final id = entry['id'] as String;
      if (_parentOf(id) != null) continue;          // drawn under its parent
      cards.add(_card(entry));
      for (final other in _catalog) {
        if (_parentOf(other['id'] as String) == id) {
          cards.add(Padding(
            padding: const EdgeInsets.only(left: 18),
            child: _card(other, extension: true),
          ));
        }
      }
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        MenuSection(
          title: 'Installed',
          note: 'Everything on the map arrives from these. An install with '
              'none has no rooms and nothing to do, which is the correct '
              'empty state rather than an error. An indented one is an '
              'extension: it patches another plugin rather than standing on '
              'its own, so it has no castle of its own either. Switching one '
              'off leaves it exactly where it is.',
          children: [
            if (_catalog.isEmpty)
              const Text('nothing installed',
                  style: TextStyle(color: Colors.white38)),
            ...cards,
          ],
        ),
        MenuSection(
          title: 'Adding one',
          note: 'Installing a plugin is putting a directory in plugins/, so '
              'this clones a repository into it and reloads. A plugin whose '
              'CODE changed still needs a restart: Python caches modules, so '
              'the old one is what would be used again.',
          children: [
            _InstallField(onInstall: (src) async {
              final out = await widget.onInstall(src);
              await _refresh();
              return out;
            }),
            const SizedBox(height: 14),
            _ReloadButton(onReload: () async {
              final out = await widget.onReload();
              await _refresh();
              return out;
            }),
          ],
        ),
      ],
    );
  }

  /// Deleting is the one thing here that cannot be undone, so it asks — and
  /// the question names what would be lost rather than saying "are you sure".
  Future<void> _confirmRemove(String id, List<String> lost) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('Delete $id?'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (lost.isEmpty)
              const Text('Git can restore everything in this directory.')
            else ...[
              const Text('These are not in version control anywhere, so this '
                  'is the only copy:'),
              const SizedBox(height: 8),
              for (final f in lost.take(8))
                Text('  $f', style: const TextStyle(fontFamily: 'monospace')),
              if (lost.length > 8) Text('  …and ${lost.length - 8} more'),
            ],
          ],
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: const Text('Cancel')),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: Colors.red.shade700),
            onPressed: () => Navigator.pop(ctx, true),
            child: Text(lost.isEmpty ? 'Delete' : 'Delete anyway'),
          ),
        ],
      ),
    );
    if (ok == true) {
      await _act(id, () => widget.onRemove(id, lost.isNotEmpty));
    }
  }

  Widget _card(Map<String, dynamic> entry, {bool extension = false}) {
    final id = entry['id'] as String;
    final enabled = entry['enabled'] == true;
    final lost = ((entry['unrecoverable'] as List?) ?? const []).cast<String>();
    final git = '${entry['git'] ?? ''}';
    final busy = _busy == id;

    final p = _detail(id) ?? const <String, dynamic>{};
    final rooms = (p['rooms'] as List?) ?? const [];
    final patches = (p['patches'] as List?) ?? const [];
    final agentPatches = (p['agent_patches'] as List?) ?? const [];
    final title = '${p['name'] ?? id}';

    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.fromLTRB(12, 12, 12, 6),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: enabled ? .04 : .02),
        borderRadius: BorderRadius.circular(7),
        border: extension
            ? Border(
                left: BorderSide(
                    color: Colors.white.withValues(alpha: .16), width: 2))
            : null,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(children: [
            if (extension) ...[
              Icon(Icons.subdirectory_arrow_right,
                  size: 13, color: Colors.white.withValues(alpha: .35)),
              const SizedBox(width: 6),
            ],
            // Flexible, not bare Text. An indented extension with a long name
            // overflowed the row by 37px, and the id is the part that can
            // afford to be cut.
            Flexible(
              child: Text(title,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                      fontWeight: FontWeight.w700,
                      color: enabled ? Colors.white : Colors.white54)),
            ),
            // Only when it adds something. A plugin that is switched off has
            // not been imported, so there is no name to show but the id — and
            // "job_hunt  job_hunt" reads as a rendering fault.
            if (title != id) ...[
              const SizedBox(width: 8),
              Flexible(
                child: Text(id,
                    overflow: TextOverflow.ellipsis,
                    style:
                        const TextStyle(fontSize: 11, color: Colors.white38)),
              ),
            ],
          ]),
          if ('${p['description'] ?? ''}'.isNotEmpty) ...[
            const SizedBox(height: 4),
            Text('${p['description']}',
                style: const TextStyle(
                    fontSize: 12, color: Colors.white54, height: 1.4)),
          ],
          if (!enabled) ...[
            const SizedBox(height: 4),
            Text(
                '${entry['reason'] ?? ''}'.isEmpty
                    ? 'switched off — still on disk'
                    : 'switched off — ${entry['reason']}',
                style: const TextStyle(fontSize: 12, color: Color(0xFFE0A458))),
          ],
          if (git.isNotEmpty) ...[
            const SizedBox(height: 4),
            Text(git,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontSize: 11, color: Colors.white30)),
          ],
          const SizedBox(height: 8),
          // The chips and the controls share the last row: the controls belong
          // ON the card they act on, and at its end, where you look last.
          Row(children: [
            Expanded(
              child: Wrap(spacing: 6, runSpacing: 6, children: [
                if (rooms.isNotEmpty) _chipOf('${rooms.length} rooms'),
                if (rooms.isEmpty &&
                    (patches.isNotEmpty || agentPatches.isNotEmpty))
                  _chipOf('no castle of its own'),
                if (patches.isNotEmpty) _chipOf('rooms: ${patches.join(", ")}'),
                if (agentPatches.isNotEmpty)
                  _chipOf('agents: ${agentPatches.join(", ")}'),
                for (final k in (p['pipelines'] as List?) ?? const [])
                  _chipOf('$k'),
                if ((p['tools'] as List?)?.isNotEmpty ?? false)
                  _chipOf('${(p['tools'] as List).length} tools'),
                if ((p['gates'] as List?)?.isNotEmpty ?? false)
                  _chipOf('${(p['gates'] as List).length} gates'),
              ]),
            ),
            if (busy)
              const Padding(
                padding: EdgeInsets.symmetric(horizontal: 14, vertical: 8),
                child: SizedBox(
                    width: 14, height: 14,
                    child: CircularProgressIndicator(strokeWidth: 2)),
              )
            else ...[
              Switch(
                value: enabled,
                onChanged: (v) =>
                    _act(id, () => widget.onSetEnabled(id, v)),
              ),
              IconButton(
                tooltip: lost.isEmpty
                    ? 'Delete this plugin'
                    : 'Delete — ${lost.length} file(s) here exist nowhere else',
                icon: Icon(Icons.delete_outline,
                    size: 18,
                    color: lost.isEmpty ? Colors.white38 : Colors.orange),
                onPressed: () => _confirmRemove(id, lost),
              ),
            ],
          ]),
        ],
      ),
    );
  }

  Widget _chipOf(String s) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
        decoration: BoxDecoration(
          color: Colors.white.withValues(alpha: .06),
          borderRadius: BorderRadius.circular(5),
        ),
        child: Text(s, style: const TextStyle(fontSize: 11.5)),
      );
}
