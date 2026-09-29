import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../api/client.dart';

/// The pipeline, drawn from what the server says it is.
///
/// Not a picture anybody maintains. `docs/pipeline.html` was a hand-drawn
/// diagram that went stale the week a stage was added and then sat there
/// describing a machine that no longer existed — the failure a generated
/// drawing cannot have. Every node, edge and label here comes from
/// `/pipeline`, which is built from the same transition table the transport
/// enforces, so a diagram that disagrees with the code is not possible.
class PipelineGraph extends StatefulWidget {
  const PipelineGraph({super.key, required this.api, required this.castleId});

  final Api api;
  final String castleId;

  @override
  State<PipelineGraph> createState() => _PipelineGraphState();
}

class _PipelineGraphState extends State<PipelineGraph> {
  List<String> _kinds = const [];
  String _kind = '';
  _Graph? _graph;
  bool _loading = true;
  String _problem = '';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final adopted = _kind;
    try {
      final d = await widget.api
          .get('/pipeline?castle_id=${widget.castleId}&kind=$_kind') as Map;
      if (!mounted) return;
      final kinds = [for (final k in (d['kinds'] as List? ?? const [])) '$k'];
      setState(() {
        _kinds = kinds;
        if (_kind.isEmpty && kinds.isNotEmpty) _kind = kinds.first;
        _graph = _Graph.from(d);
        _loading = false;
        _problem = '';
      });
      // The first ask has no kind, which is how the kinds arrive. Adopting one
      // means asking again, or the graph is every pipeline's steps at once.
      if (adopted != _kind) return _load();
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
      return const Center(
          child: SizedBox(
              width: 18, height: 18,
              child: CircularProgressIndicator(strokeWidth: 2)));
    }
    if (_problem.isNotEmpty) {
      return Padding(
        padding: const EdgeInsets.all(16),
        child: Text(_problem,
            style: const TextStyle(fontSize: 12, color: Color(0xFFE0A458))),
      );
    }
    final g = _graph;
    if (g == null || g.nodes.isEmpty) {
      return const Padding(
        padding: EdgeInsets.all(16),
        child: Text('this pipeline declares no steps',
            style: TextStyle(fontSize: 12, color: Colors.white38)),
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (_kinds.length > 1)
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
            child: Wrap(spacing: 6, children: [
              for (final k in _kinds)
                ChoiceChip(
                  label: Text(k, style: const TextStyle(fontSize: 11.5)),
                  selected: _kind == k,
                  visualDensity: VisualDensity.compact,
                  onSelected: (_) {
                    setState(() => _kind = k);
                    _load();
                  },
                ),
            ]),
          ),
        Expanded(
          // Both ways, because a pipeline is taller than a panel and a branch
          // is wider than one.
          child: InteractiveViewer(
            constrained: false,
            minScale: 0.4,
            maxScale: 2.5,
            boundaryMargin: const EdgeInsets.all(80),
            child: CustomPaint(
              size: g.canvas,
              painter: _GraphPainter(g),
            ),
          ),
        ),
      ],
    );
  }
}

// ---------------------------------------------------------------------------
// The shape

class _Node {
  _Node(this.stage);

  final String stage;
  String role = '';
  String room = '';
  int waiting = 0;
  bool gated = false;
  bool permanent = false;
  bool dead = false;
  int depth = 0;
  Offset at = Offset.zero;
}

class _Edge {
  _Edge(this.from, this.to, this.kind, this.role);

  final String from;
  final String to;
  final String kind;
  final String role;
}

const _boxW = 168.0;
const _boxH = 46.0;
const _gapX = 26.0;
const _gapY = 74.0;

class _Graph {
  _Graph(this.nodes, this.edges, this.canvas);

  final Map<String, _Node> nodes;
  final List<_Edge> edges;
  final Size canvas;

  static _Graph from(Map<dynamic, dynamic> d) {
    final dead = {for (final s in (d['dead_stages'] as List? ?? const [])) '$s'};
    final nodes = <String, _Node>{};
    final edges = <_Edge>[];

    _Node node(String stage) => nodes.putIfAbsent(stage, () {
          final n = _Node(stage);
          n.dead = dead.contains(stage);
          return n;
        });

    for (final raw in (d['steps'] as List? ?? const [])) {
      final s = (raw as Map).cast<String, dynamic>();
      final n = node('${s['stage']}');
      // A stage can be worked by more than one role — `prepared` is the
      // postman's forward and the operator's rejection. The agent is the one
      // worth naming on the box; the operator's edge shows on the arrow.
      if (n.role.isEmpty || '${s['role']}' != 'operator') {
        n.role = '${s['role'] ?? ''}';
        n.room = '${s['room_name'] ?? ''}';
      }
      n.waiting = math.max(n.waiting, (s['waiting'] as num?)?.toInt() ?? 0);
      n.gated = n.gated || s['gated'] == true;
      n.permanent = n.permanent || s['permanent'] == true;
      for (final o in (s['outcomes'] as List? ?? const [])) {
        final to = '${(o as Map)['to']}';
        node(to);
        edges.add(_Edge('${s['stage']}', to, '${o['kind']}',
            '${s['role'] ?? ''}'));
      }
    }

    _layer(nodes, [for (final x in (d['stages'] as List? ?? const [])) '$x']);
    final canvas = _place(nodes);
    return _Graph(nodes, edges, canvas);
  }
}

/// Depth from the DECLARED stage order, not from the edges.
///
/// The obvious thing is a longest path from the entry, and it does not work
/// here: the graph has cycles. A rejection sends a record back to an earlier
/// stage, which is a real edge and the whole reason rework exists — and a
/// longest-path relaxation over a cycle ping-pongs, each pass pushing both
/// ends one row further down. The first version of this put one box at the top
/// and the rest somewhere past the bottom of the canvas.
///
/// `/pipeline` already answers with `stages` in the order the plugin declared
/// them, which is the order the pipeline is written in and the order a reader
/// expects. Using it means the drawing agrees with the declaration by
/// construction, and a cycle is just an edge that points upwards.
void _layer(Map<String, _Node> nodes, List<String> declared) {
  final rank = {for (var i = 0; i < declared.length; i++) declared[i]: i};
  final live = nodes.values.where((n) => !n.dead).toList()
    ..sort((a, b) => (rank[a.stage] ?? declared.length + a.stage.hashCode % 97)
        .compareTo(rank[b.stage] ?? declared.length + b.stage.hashCode % 97));
  var depth = 0;
  for (final n in live) {
    n.depth = depth++;
  }
  // Terminal stages share ONE row, immediately under the last live stage.
  // `passed_over` and `closed` are reachable from most of the pipeline and are
  // not steps in it — a row each stretches the drawing, and computing their
  // row from the deepest node left two empty rows above them, because a stage
  // the declaration does not list had already been pushed past the end.
  for (final n in nodes.values) {
    if (n.dead) n.depth = depth;
  }
}

Size _place(Map<String, _Node> nodes) {
  final rows = <int, List<_Node>>{};
  for (final n in nodes.values) {
    rows.putIfAbsent(n.depth, () => []).add(n);
  }
  var widest = 1;
  for (final r in rows.values) {
    widest = math.max(widest, r.length);
    r.sort((a, b) => a.stage.compareTo(b.stage));
  }
  final width = widest * (_boxW + _gapX) + _gapX;
  for (final entry in rows.entries) {
    final r = entry.value;
    final span = r.length * (_boxW + _gapX) - _gapX;
    var x = (width - span) / 2;
    for (final n in r) {
      n.at = Offset(x, 24 + entry.key * (_boxH + _gapY));
      x += _boxW + _gapX;
    }
  }
  // The last row's box, not the gap after it — a trailing ${_gapY}px of
  // nothing is a scroll that goes somewhere empty.
  final depth = rows.keys.fold(0, math.max);
  return Size(width, 24 + depth * (_boxH + _gapY) + _boxH + 24);
}

// ---------------------------------------------------------------------------
// The drawing

class _GraphPainter extends CustomPainter {
  _GraphPainter(this.g);

  final _Graph g;

  static const _ink = Color(0xFFE7EAF0);
  static const _forward = Color(0xFF6BD68A);
  static const _reject = Color(0xFFE0A458);
  static const _quiet = Color(0xFF5A6070);

  @override
  void paint(Canvas canvas, Size size) {
    for (final e in g.edges) {
      _arrow(canvas, e);
    }
    for (final n in g.nodes.values) {
      _box(canvas, n);
    }
  }

  void _arrow(Canvas canvas, _Edge e) {
    final from = g.nodes[e.from]!;
    final to = g.nodes[e.to]!;
    final back = to.depth <= from.depth;
    final colour = e.kind == 'forward'
        ? _forward
        : back || e.kind == 'reject'
            ? _reject
            : _quiet;
    final paint = Paint()
      ..color = colour.withValues(alpha: back ? .55 : .75)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.4;

    final a = Offset(from.at.dx + _boxW / 2, from.at.dy + _boxH);
    final b = Offset(to.at.dx + _boxW / 2, to.at.dy);
    final path = Path();
    if (back) {
      // Out to the side and up, so a rework loop is not a line lying on top of
      // the forward path it undoes.
      final side = math.max(from.at.dx, to.at.dx) + _boxW + 22;
      final up = Offset(to.at.dx + _boxW, to.at.dy + _boxH / 2);
      path.moveTo(from.at.dx + _boxW, from.at.dy + _boxH / 2);
      path.cubicTo(side, from.at.dy + _boxH / 2, side, up.dy, up.dx, up.dy);
      canvas.drawPath(path, paint);
      _head(canvas, up, const Offset(-1, 0), colour);
      return;
    }
    path.moveTo(a.dx, a.dy);
    path.cubicTo(a.dx, a.dy + _gapY / 2, b.dx, b.dy - _gapY / 2, b.dx, b.dy);
    canvas.drawPath(path, paint);
    _head(canvas, b, const Offset(0, 1), colour);
  }

  void _head(Canvas canvas, Offset tip, Offset dir, Color colour) {
    const s = 5.0;
    final p = Path();
    if (dir.dy != 0) {
      p.moveTo(tip.dx, tip.dy);
      p.lineTo(tip.dx - s, tip.dy - s * 1.4);
      p.lineTo(tip.dx + s, tip.dy - s * 1.4);
    } else {
      p.moveTo(tip.dx, tip.dy);
      p.lineTo(tip.dx + s * 1.4, tip.dy - s);
      p.lineTo(tip.dx + s * 1.4, tip.dy + s);
    }
    p.close();
    canvas.drawPath(p, Paint()..color = colour.withValues(alpha: .9));
  }

  void _box(Canvas canvas, _Node n) {
    final rect = Rect.fromLTWH(n.at.dx, n.at.dy, _boxW, _boxH);
    final rrect = RRect.fromRectAndRadius(rect, const Radius.circular(7));
    canvas.drawRRect(
        rrect,
        Paint()
          ..color = n.dead
              ? const Color(0xFF1B1E26)
              : n.waiting > 0
                  ? const Color(0xFF232A33)
                  : const Color(0xFF1E222B));
    canvas.drawRRect(
        rrect,
        Paint()
          ..color = n.waiting > 0 ? _forward.withValues(alpha: .45)
              : Colors.white.withValues(alpha: n.dead ? .07 : .12)
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1.2);

    _text(canvas, n.stage, Offset(n.at.dx + 10, n.at.dy + 7),
        size: 12.5, weight: FontWeight.w600,
        colour: n.dead ? _quiet : _ink);

    final under = n.dead
        ? 'ends here'
        : [
            if (n.role.isNotEmpty && n.role != 'operator') n.role
            else if (n.role == 'operator') 'you',
            if (n.gated) n.permanent ? 'gated · always' : 'gated',
          ].join(' · ');
    if (under.isNotEmpty) {
      _text(canvas, under, Offset(n.at.dx + 10, n.at.dy + 25),
          size: 10.5, colour: _quiet);
    }
    if (n.waiting > 0) {
      final label = '${n.waiting}';
      _text(canvas, label, Offset(n.at.dx + _boxW - 12 - label.length * 7,
              n.at.dy + 15),
          size: 12, weight: FontWeight.w700, colour: _forward);
    }
  }

  void _text(Canvas canvas, String s, Offset at,
      {double size = 12,
      FontWeight weight = FontWeight.normal,
      Color colour = _ink}) {
    final tp = TextPainter(
      text: TextSpan(
          text: s,
          style: TextStyle(fontSize: size, color: colour, fontWeight: weight)),
      textDirection: TextDirection.ltr,
      maxLines: 1,
      ellipsis: '…',
    )..layout(maxWidth: _boxW - 20);
    tp.paint(canvas, at);
  }

  @override
  bool shouldRepaint(_GraphPainter old) => old.g != g;
}
