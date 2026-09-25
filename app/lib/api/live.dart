import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';

import '../model/world.dart';

/// The live wire: `/ws`.
///
/// Five event types, and the app must tolerate not knowing one — the
/// environment is plugin-driven and a plugin may start sending something this
/// build has never heard of. An unknown type is ignored, never fatal.
///
/// Reconnects on its own. The operator leaves this open all day and a dropped
/// socket that needs a restart to notice is the same as no socket at all.
class Live {
  Live(this.url);

  Uri url;

  final _agents = <String, AgentState>{};
  final _controller = StreamController<LiveEvent>.broadcast();

  WebSocket? _socket;
  bool _closed = false;
  Duration _backoff = const Duration(seconds: 1);

  Stream<LiveEvent> get events => _controller.stream;
  Map<String, AgentState> get agents => Map.unmodifiable(_agents);

  Future<void> connect() async {
    if (_closed) return;
    try {
      final s = await WebSocket.connect(url.toString());
      _socket = s;
      _backoff = const Duration(seconds: 1);
      _controller.add(const LiveEvent.connected());
      s.listen(
        _onFrame,
        onDone: _reconnect,
        onError: (Object _) => _reconnect(),
        cancelOnError: true,
      );
    } catch (_) {
      _reconnect();
    }
  }

  void _reconnect() {
    _socket = null;
    if (_closed) return;
    _controller.add(const LiveEvent.disconnected());
    Timer(_backoff, connect);
    // Backs off to half a minute. A server that is down stays down for a
    // while, and hammering it changes nothing.
    final next = _backoff * 2;
    _backoff = next > const Duration(seconds: 30)
        ? const Duration(seconds: 30)
        : next;
  }

  /// Feed one frame in, as the socket would. For tests: the wire format is
  /// the contract between this app and the server, and it is exactly where a
  /// key can be misspelt and nothing complain.
  @visibleForTesting
  void deliver(String frame) => _onFrame(frame);

  void _onFrame(dynamic raw) {
    late final Map<String, dynamic> msg;
    try {
      msg = jsonDecode(raw as String) as Map<String, dynamic>;
    } catch (_) {
      return; // not ours to interpret
    }
    switch (msg['type']) {
      case 'snapshot':
        _agents.clear();
        for (final a in (msg['agents'] ?? []) as List) {
          final s = AgentState.fromJson(a as Map<String, dynamic>);
          _agents[s.id] = s;
        }
        _controller.add(const LiveEvent.agentsChanged());
      case 'agent_update':
        final s = AgentState.fromJson(msg['agent'] as Map<String, dynamic>);
        _agents[s.id] = s;
        _controller.add(const LiveEvent.agentsChanged());
      case 'agent_removed':
        // `agent_id`, not `id`. The server has always sent the first and this
        // read the second, so a retired worker's sprite never left the map —
        // the sweep retires one after every finished record, so the room
        // slowly filled with agents that were not there.
        _agents.remove(msg['agent_id']);
        _controller.add(const LiveEvent.agentsChanged());
      case 'agent_talk':
        _controller.add(LiveEvent.talk(
          msg['from'] as String? ?? '',
          msg['to'] as String? ?? '',
          msg['label'] as String? ?? '',
        ));
      case 'approvals_updated':
        _controller.add(const LiveEvent.approvalsChanged());
      case 'rooms_changed':
      case 'plugins_changed':
        // A plugin was installed or removed on the server. Every client is
        // told, not just the one that pressed the button: the map it is
        // drawing has rooms that no longer exist, or is missing a castle.
        _controller.add(const LiveEvent.worldChanged());
      default:
        // A type this build does not know. The environment gains behaviour by
        // gaining plugins, so this is expected, not an error.
        break;
    }
  }

  Future<void> close() async {
    _closed = true;
    await _socket?.close();
    await _controller.close();
  }
}

enum LiveKind {
  connected,
  disconnected,
  agentsChanged,
  approvalsChanged,
  worldChanged,
  talk,
}

class LiveEvent {
  const LiveEvent(this.kind, {this.from = '', this.to = '', this.label = ''});
  const LiveEvent.connected() : this(LiveKind.connected);
  const LiveEvent.disconnected() : this(LiveKind.disconnected);
  const LiveEvent.agentsChanged() : this(LiveKind.agentsChanged);
  const LiveEvent.approvalsChanged() : this(LiveKind.approvalsChanged);
  const LiveEvent.worldChanged() : this(LiveKind.worldChanged);
  const LiveEvent.talk(String from, String to, String label)
      : this(LiveKind.talk, from: from, to: to, label: label);

  final LiveKind kind;
  final String from;
  final String to;
  final String label;
}
