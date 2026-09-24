import 'package:flutter/material.dart';

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

  Future<void> open(BuildContext context, {String? tab}) => MenuPanel.show(
        context,
        title: 'Settings',
        initialTab: tab,
        tabs: [
          MenuTab(
            id: 'connection',
            title: 'Connection',
            icon: Icons.lan_outlined,
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
            title: 'Server',
            note: 'Where the environment is. One day this will be one castle '
                'among several; for now it is the machine running uvicorn.',
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

  /// Which plugin each one hangs off, if any.
  ///
  /// An extension declares what it `requires`, and that is the edge — the same
  /// declaration that orders the boot. `website_recreation` requires
  /// `web_agency`, adds benches to two of its rooms and declares none of its
  /// own, so it belongs INSIDE that castle rather than beside it. Flat, the
  /// list said "3 plugins" while the map showed two castles, and nothing
  /// explained the difference.
  String? _parentOf(Map<String, dynamic> p) {
    final ids = {for (final q in plugins) q['id'] as String};
    for (final need in (p['requires'] as List?) ?? const []) {
      if (ids.contains(need)) return need as String;
    }
    return null;
  }

  Widget _plugins(BuildContext context) {
    final children = <Widget>[];
    for (final p in plugins) {
      if (_parentOf(p) != null) continue;          // drawn under its parent
      children.add(_plugin(p));
      for (final q in plugins) {
        if (_parentOf(q) == p['id']) {
          children.add(Padding(
            padding: const EdgeInsets.only(left: 18, bottom: 8),
            child: _plugin(q, extension: true),
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
              'its own, so it has no castle of its own either.',
          children: [
            if (plugins.isEmpty)
              const Text('nothing installed',
                  style: TextStyle(color: Colors.white38)),
            ...children,
          ],
        ),
        MenuSection(
          title: 'Adding one',
          note: 'Installing a plugin is putting a directory in plugins/. '
              'Reload re-reads that directory without restarting the server, '
              'so a plugin appearing or disappearing takes effect now and the '
              'map keeps its sprites where they were. A plugin whose CODE '
              'changed still needs a restart: Python caches modules, so the '
              'old one is what would be used again.',
          children: [_ReloadButton(onReload: onReload)],
        ),
      ],
    );
  }

  Widget _plugin(Map<String, dynamic> p, {bool extension = false}) {
    final rooms = (p['rooms'] as List?) ?? const [];
    final patches = (p['patches'] as List?) ?? const [];
    final agentPatches = (p['agent_patches'] as List?) ?? const [];
    return Container(
      margin: EdgeInsets.only(bottom: extension ? 0 : 8),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: extension ? .025 : .04),
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
            // Flexible, not bare Text. An indented extension with a long
            // name overflowed the row by 37px, and the id is the part that can
            // afford to be cut.
            Flexible(
              child: Text('${p['name'] ?? p['id']}',
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontWeight: FontWeight.w700)),
            ),
            const SizedBox(width: 8),
            Flexible(
              child: Text('${p['id']}',
                  overflow: TextOverflow.ellipsis,
                  style:
                      const TextStyle(fontSize: 11, color: Colors.white38)),
            ),
          ]),
          if ('${p['description'] ?? ''}'.isNotEmpty) ...[
            const SizedBox(height: 4),
            Text('${p['description']}',
                style: const TextStyle(
                    fontSize: 12, color: Colors.white54, height: 1.4)),
          ],
          const SizedBox(height: 8),
          Wrap(spacing: 6, runSpacing: 6, children: [
            if (rooms.isNotEmpty) _chip('${rooms.length} rooms'),
            // A plugin that only patches is an EXTENSION — it lives inside
            // another's castle rather than beside it.
            if (rooms.isEmpty && (patches.isNotEmpty || agentPatches.isNotEmpty))
              _chip('no castle of its own'),
            if (patches.isNotEmpty) _chip('rooms: ${patches.join(", ")}'),
            if (agentPatches.isNotEmpty)
              _chip('agents: ${agentPatches.join(", ")}'),
            for (final k in (p['pipelines'] as List?) ?? const [])
              _chip('$k'),
            if ((p['tools'] as List?)?.isNotEmpty ?? false)
              _chip('${(p['tools'] as List).length} tools'),
            if ((p['gates'] as List?)?.isNotEmpty ?? false)
              _chip('${(p['gates'] as List).length} gates'),
          ]),
        ],
      ),
    );
  }

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
