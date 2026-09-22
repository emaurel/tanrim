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

  Widget _plugins(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          MenuSection(
            title: 'Installed',
            note: 'Everything on the map arrives from these. An install with '
                'none has no rooms and nothing to do, which is the correct '
                'empty state rather than an error.',
            children: [
              if (plugins.isEmpty)
                const Text('nothing installed',
                    style: TextStyle(color: Colors.white38)),
              for (final p in plugins) _plugin(p),
            ],
          ),
          const MenuSection(
            title: 'Adding one',
            note: 'Installing a plugin is putting a directory in plugins/ and '
                'restarting the server. Doing it from here needs the app to '
                'manage the server process, which it does not yet.',
            children: [],
          ),
        ],
      );

  Widget _plugin(Map<String, dynamic> p) {
    final rooms = (p['rooms'] as List?) ?? const [];
    final patches = (p['patches'] as List?) ?? const [];
    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: .04),
        borderRadius: BorderRadius.circular(7),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(children: [
            Text('${p['name'] ?? p['id']}',
                style: const TextStyle(fontWeight: FontWeight.w700)),
            const SizedBox(width: 8),
            Text('${p['id']}',
                style:
                    const TextStyle(fontSize: 11, color: Colors.white38)),
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
            if (patches.isNotEmpty) _chip('extends ${patches.join(", ")}'),
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
