import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';

import '../model/record.dart';

/// Draws the blocks a record's view is made of.
///
/// Text is plain `Text` inside one `SelectionArea` at the top, not a
/// `SelectableText` per value. A real dossier draws six hundred of them, and
/// each `SelectableText` carries its own selection machinery — that alone was
/// most of the second-long stall when a record opened.
///
/// The app knows this vocabulary and nothing about what any record MEANS — a
/// dossier with cited prices, a job posting, a port survey all arrive as the
/// same eight shapes. A plugin cannot ship rendering code into a compiled
/// binary, so everything it sends is data.
///
/// Two rules run through all of it:
///
/// **Every string is plain text.** A record holds a business's own copy and a
/// customer's email, which nobody here wrote. Nothing is parsed as markup, and
/// only a value the SERVER marked as a url is clickable — and only http(s).
///
/// **A block this build has never seen is drawn, not dropped.** It falls
/// through to `raw`, the same rule the live socket follows for an unknown
/// frame. Nothing a plugin sends should be able to make part of a record
/// invisible.
class Blocks extends StatelessWidget {
  const Blocks({super.key, required this.blocks, this.onOpenRoom});

  final List<Map<String, dynamic>> blocks;

  /// Open a room from a timeline step.
  final void Function(String roomId)? onOpenRoom;

  @override
  Widget build(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [for (final b in blocks) _block(context, b)],
      );

  Widget _block(BuildContext context, Map<String, dynamic> b) {
    final title = '${b['title'] ?? ''}';
    return switch ('${b['block']}') {
      'section' => _section(context, b, title),
      'text' => _text(b),
      'fields' => _titled(title, _fields(b)),
      'list' => _titled(title, _list(b)),
      'table' => _titled(title, _table(b)),
      'images' => _titled(title, _images(b)),
      'timeline' => _titled(title, _timeline(b)),
      // Closed by default. A `raw` block is the long tail — whatever had no
      // shape worth giving it — and a dossier that opens on a wall of JSON
      // buries the parts that did.
      _ => _titled(title.isEmpty ? 'Raw' : title, _raw(b['value']),
          open: false),
    };
  }

  /// A titled block, folded away by its heading.
  ///
  /// An untitled one has nothing to click and nothing to label it with once
  /// closed, so it stays as it is — a paragraph is not a card.
  Widget _titled(String title, Widget child, {bool open = true}) {
    if (title.isEmpty) {
      return Padding(
        padding: const EdgeInsets.only(bottom: 16),
        child: child,
      );
    }
    return Collapsible(
      title: title,
      initiallyOpen: open,
      child: child,
    );
  }

  Widget _section(BuildContext context, Map<String, dynamic> b, String title) {
    final note = '${b['note'] ?? ''}';
    final children = ((b['children'] ?? []) as List)
        .map((c) => (c as Map).cast<String, dynamic>())
        .toList();
    return Collapsible(
      title: title,
      framed: true,
      note: note,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [for (final c in children) _block(context, c)],
      ),
    );
  }

  Widget _text(Map<String, dynamic> b) {
    final tone = '${b['tone'] ?? 'normal'}';
    return Padding(
      padding: const EdgeInsets.only(bottom: 14),
      child: Text(
        '${b['body'] ?? ''}',
        style: TextStyle(
          fontSize: 12.5,
          height: 1.5,
          color: switch (tone) {
            'warn' => const Color(0xFFE0A458),
            'quiet' => Colors.white38,
            _ => Colors.white70,
          },
        ),
      ),
    );
  }

  Widget _fields(Map<String, dynamic> b) {
    final rows = ((b['rows'] ?? []) as List)
        .map((r) => (r as Map).cast<String, dynamic>())
        .toList();
    return Column(children: [for (final r in rows) _row(r)]);
  }

  Widget _row(Map<String, dynamic> r) {
    final conflict = r['conflict'] == true;
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        SizedBox(
          width: 130,
          child: Text('${r['label'] ?? ''}',
              style: const TextStyle(fontSize: 12, color: Colors.white38)),
        ),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              _value(r['value'], '${r['format'] ?? 'text'}',
                  conflict: conflict),
              if ('${r['note'] ?? ''}'.isNotEmpty)
                Text('${r['note']}',
                    style: const TextStyle(
                        fontSize: 11, color: Colors.white30, height: 1.35)),
            ],
          ),
        ),
        _cite(r['source'], conflict: conflict),
      ]),
    );
  }

  /// One value, formatted the way the server said to read it.
  Widget _value(Object? value, String format, {bool conflict = false}) {
    final text = value == null ? '—' : '$value';
    final style = TextStyle(
      fontSize: 12.5,
      height: 1.4,
      color: conflict ? const Color(0xFFE0A458) : Colors.white,
    );
    return switch (format) {
      'url' => _link(text),
      'colour' => Row(mainAxisSize: MainAxisSize.min, children: [
          Container(
            width: 13,
            height: 13,
            margin: const EdgeInsets.only(right: 7),
            decoration: BoxDecoration(
              color: _colour(text),
              borderRadius: BorderRadius.circular(3),
              border: Border.all(color: Colors.white24),
            ),
          ),
          Text(text, style: style),
        ]),
      'money' => Text(_money(value), style: style),
      'bytes' => Text(_bytes(value), style: style),
      'datetime' => Text(
          value is num ? ago(value.toDouble()) : text, style: style),
      'percent' => Text(value is num ? '${(value * 100).round()}%' : text,
          style: style),
      _ => Text(text, style: style),
    };
  }

  /// The citation marker.
  ///
  /// The web agency's rule is that every fact carries a source URL and
  /// anything uncited goes in `unverified`. Marking the cited ones is what
  /// makes that visible — and the ABSENCE of a marker is the other half of
  /// it, so an uncited row keeps the same space rather than closing up.
  Widget _cite(Object? source, {bool conflict = false}) {
    if (conflict) {
      return const Tooltip(
        message: 'sources disagree about this',
        child: Padding(
          padding: EdgeInsets.only(left: 8),
          child: Icon(Icons.call_split, size: 13, color: Color(0xFFE0A458)),
        ),
      );
    }
    final url = source is String ? source : '';
    if (url.isEmpty) {
      return const SizedBox(width: 21);
    }
    return Tooltip(
      message: url,
      child: IconButton(
        onPressed: () => _openUrl(url),
        iconSize: 13,
        padding: const EdgeInsets.only(left: 8),
        constraints: const BoxConstraints(),
        icon: Icon(Icons.link, color: Colors.white.withValues(alpha: .45)),
      ),
    );
  }

  Widget _list(Map<String, dynamic> b) {
    final items = ((b['items'] ?? []) as List)
        .map((i) => (i as Map).cast<String, dynamic>())
        .toList();
    if ('${b['style']}' == 'chips') {
      return Wrap(spacing: 6, runSpacing: 6, children: [
        for (final i in items)
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
            decoration: BoxDecoration(
              color: Colors.white.withValues(alpha: .06),
              borderRadius: BorderRadius.circular(5),
            ),
            child: Text('${i['text'] ?? ''}',
                style: const TextStyle(fontSize: 11.5)),
          ),
      ]);
    }
    final checks = '${b['style']}' == 'checks';
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (final i in items)
          Padding(
            padding: const EdgeInsets.only(bottom: 5),
            child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Padding(
                padding: const EdgeInsets.only(top: 2, right: 8),
                child: checks
                    ? Icon(
                        i['done'] == true
                            ? Icons.check_box_outlined
                            : Icons.check_box_outline_blank,
                        size: 14,
                        color: Colors.white38)
                    : Text('·',
                        style: TextStyle(
                            color: Colors.white.withValues(alpha: .35))),
              ),
              Expanded(
                child: Text('${i['text'] ?? ''}',
                    style: const TextStyle(fontSize: 12.5, height: 1.45)),
              ),
              _cite(i['source']),
            ]),
          ),
      ],
    );
  }

  Widget _table(Map<String, dynamic> b) {
    final columns =
        ((b['columns'] ?? []) as List).map((c) => '$c').toList();
    final rows = ((b['rows'] ?? []) as List)
        .map((r) => (r as Map).cast<String, dynamic>())
        .toList();
    return Container(
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: .03),
        borderRadius: BorderRadius.circular(6),
      ),
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
      child: Column(children: [
        Row(children: [
          for (final c in columns)
            Expanded(
              child: Text(c.toUpperCase(),
                  style: const TextStyle(
                      fontSize: 9.5,
                      letterSpacing: 0.8,
                      fontWeight: FontWeight.w700,
                      color: Colors.white30)),
            ),
          const SizedBox(width: 21),
        ]),
        const Divider(height: 12),
        for (final r in rows)
          Padding(
            padding: const EdgeInsets.only(bottom: 6),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                for (final cell in ((r['cells'] ?? []) as List))
                  Expanded(
                    child: Text(cell == null ? '—' : '$cell',
                        style: TextStyle(
                            fontSize: 12,
                            height: 1.35,
                            color: cell == null
                                ? Colors.white24
                                : Colors.white70)),
                  ),
                _cite(r['source']),
              ],
            ),
          ),
      ]),
    );
  }

  Widget _images(Map<String, dynamic> b) {
    final items = ((b['items'] ?? []) as List)
        .map((i) => (i as Map).cast<String, dynamic>())
        .toList();
    return Wrap(spacing: 8, runSpacing: 8, children: [
      for (final i in items)
        SizedBox(
          width: 130,
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            ClipRRect(
              borderRadius: BorderRadius.circular(5),
              child: Image.network(
                '${i['url']}',
                height: 88,
                width: 130,
                fit: BoxFit.cover,
                // A record's images come from wherever the plugin serves
                // them, which may be gone. A broken one says so instead of
                // throwing a red box into the middle of a dossier.
                errorBuilder: (_, _, _) => Container(
                  height: 88,
                  color: Colors.white10,
                  alignment: Alignment.center,
                  child: const Icon(Icons.broken_image_outlined,
                      size: 18, color: Colors.white24),
                ),
              ),
            ),
            if ('${i['caption'] ?? ''}'.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text('${i['caption']}',
                    style: const TextStyle(
                        fontSize: 10.5, color: Colors.white38)),
              ),
          ]),
        ),
    ]);
  }

  /// Where the record has been. Built by the server from the state machine's
  /// own history, for every kind, so a plugin cannot forget it.
  Widget _timeline(Map<String, dynamic> b) {
    final steps = ((b['steps'] ?? []) as List)
        .map((s) => (s as Map).cast<String, dynamic>())
        .toList();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (var i = 0; i < steps.length; i++) _step(steps[i], i == 0),
      ],
    );
  }

  /// One step. `latest` is the newest — the list arrives newest first, so
  /// that is the top one and the one worth marking.
  Widget _step(Map<String, dynamic> s, bool latest) {
    final wrote = (s['wrote'] as List?)?.map((w) => '$w').toList();
    final room = '${s['room'] ?? ''}';
    final roomName = '${s['room_name'] ?? ''}';
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Padding(
          padding: const EdgeInsets.only(top: 4, right: 10),
          child: Container(
            width: 7,
            height: 7,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: latest
                  ? const Color(0xFF8FD9A6)
                  : Colors.white.withValues(alpha: .25),
            ),
          ),
        ),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(children: [
                Flexible(
                  child: Text(
                    '${s['from_stage'] ?? '—'} → ${s['stage'] ?? ''}',
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                        fontSize: 12.5, fontWeight: FontWeight.w600),
                  ),
                ),
                if (s['by_hand'] == true) ...[
                  const SizedBox(width: 6),
                  const Tooltip(
                    message: 'moved by hand, off the transition table',
                    child: Icon(Icons.pan_tool_alt_outlined,
                        size: 12, color: Color(0xFFE0A458)),
                  ),
                ],
              ]),
              const SizedBox(height: 2),
              Wrap(spacing: 6, crossAxisAlignment: WrapCrossAlignment.center,
                  children: [
                Text('${s['agent'] ?? ''}',
                    style:
                        const TextStyle(fontSize: 11, color: Colors.white38)),
                if (roomName.isNotEmpty)
                  InkWell(
                    onTap: onOpenRoom == null ? null : () => onOpenRoom!(room),
                    child: Text('· $roomName',
                        style: TextStyle(
                            fontSize: 11,
                            color: onOpenRoom == null
                                ? Colors.white38
                                : const Color(0xFF8ECAE6))),
                  ),
                if (s['ts'] is num)
                  Text('· ${ago((s['ts'] as num).toDouble())}',
                      style: const TextStyle(
                          fontSize: 11, color: Colors.white30)),
              ]),
              if ('${s['note'] ?? ''}'.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 3),
                  child: Text('${s['note']}',
                      style: const TextStyle(
                          fontSize: 11.5, color: Colors.white54, height: 1.4)),
                ),
              // What this step PRODUCED. Absent on steps taken before the
              // ledger recorded it, and left out rather than claimed empty.
              if (wrote != null && wrote.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 4),
                  child: Wrap(spacing: 5, runSpacing: 5, children: [
                    for (final w in wrote)
                      Container(
                        padding: const EdgeInsets.symmetric(
                            horizontal: 6, vertical: 1),
                        decoration: BoxDecoration(
                          color: const Color(0xFF8FD9A6).withValues(alpha: .14),
                          borderRadius: BorderRadius.circular(4),
                        ),
                        child: Text(w,
                            style: const TextStyle(
                                fontSize: 10, color: Color(0xFF8FD9A6))),
                      ),
                  ]),
                ),
            ],
          ),
        ),
      ]),
    );
  }

  Widget _raw(Object? value) => Container(
        width: double.infinity,
        padding: const EdgeInsets.all(10),
        decoration: BoxDecoration(
          color: Colors.black.withValues(alpha: .3),
          borderRadius: BorderRadius.circular(6),
        ),
        child: Text(
          _pretty(value),
          style: const TextStyle(
              fontSize: 11, height: 1.45, fontFamily: 'monospace',
              color: Colors.white60),
        ),
      );

  Widget _link(String url) => InkWell(
        onTap: () => _openUrl(url),
        child: Text(url,
            style: const TextStyle(
                fontSize: 12.5, color: Color(0xFF8ECAE6), height: 1.4)),
      );
}

// -- helpers ----------------------------------------------------------------

/// Opens a link, and only a link.
///
/// `http` and `https` alone, and the url is REBUILT from the parsed parts
/// rather than passed through. A record carries text nobody here wrote — a
/// business's own copy, a customer's email — and a `file:` or `javascript:`
/// url reaching a launcher is the one way a rendered record could do
/// something rather than say something.
///
/// `xdg-open` rather than a package: this app is Linux-only for now, the
/// whole project has one dependency on purpose, and handing an argument to a
/// known binary is less surface than a plugin with native code on three
/// platforms.
Future<void> _openUrl(String url) async {
  final uri = Uri.tryParse(url);
  if (uri == null) return;
  if (uri.scheme != 'http' && uri.scheme != 'https') return;
  if (uri.host.isEmpty) return;
  try {
    await Process.run('xdg-open', [uri.toString()]);
  } catch (_) {
    // No opener on this machine. The url is selectable either way.
  }
}

Color _colour(String hex) {
  var h = hex.replaceFirst('#', '').trim();
  if (h.length == 3) {
    h = h.split('').map((c) => '$c$c').join();
  }
  if (h.length == 6) h = 'ff$h';
  return Color(int.tryParse(h, radix: 16) ?? 0xFF888888);
}

String _money(Object? v) =>
    v is num ? '\$${v.toStringAsFixed(2)}' : '$v';

String _bytes(Object? v) {
  if (v is! num) return '$v';
  final n = v.toDouble();
  if (n < 1024) return '${n.round()} KB';
  return '${(n / 1024).toStringAsFixed(1)} MB';
}

String _pretty(Object? value) {
  try {
    return const JsonEncoder.withIndent('  ').convert(value);
  } catch (_) {
    return '$value';
  }
}

/// A card that folds away by its heading.
///
/// Its own widget, and stateful, so opening one does not rebuild the rest of
/// a record — a dossier is a few hundred rows and a chevron should not cost
/// a full pass over them.
class Collapsible extends StatefulWidget {
  const Collapsible({
    super.key,
    required this.title,
    required this.child,
    this.note = '',
    this.initiallyOpen = true,
    this.framed = false,
  });

  final String title;
  final Widget child;
  final String note;
  final bool initiallyOpen;

  /// Sections draw a box around themselves; a bare block does not.
  final bool framed;

  @override
  State<Collapsible> createState() => _CollapsibleState();
}

class _CollapsibleState extends State<Collapsible> {
  late bool _open = widget.initiallyOpen;

  @override
  Widget build(BuildContext context) {
    final head = InkWell(
      onTap: () => setState(() => _open = !_open),
      borderRadius: BorderRadius.circular(4),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 3),
        child: Row(children: [
          Icon(_open ? Icons.expand_more : Icons.chevron_right,
              size: 15, color: Colors.white.withValues(alpha: .4)),
          const SizedBox(width: 4),
          Flexible(
            child: Text(
              widget.framed ? widget.title : widget.title.toUpperCase(),
              overflow: TextOverflow.ellipsis,
              style: widget.framed
                  ? const TextStyle(fontSize: 13, fontWeight: FontWeight.w700)
                  : const TextStyle(
                      fontSize: 10.5,
                      letterSpacing: 1.1,
                      fontWeight: FontWeight.w700,
                      color: Colors.white38),
            ),
          ),
        ]),
      ),
    );

    final body = Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        head,
        if (widget.note.isNotEmpty && _open) ...[
          const SizedBox(height: 3),
          Text(widget.note,
              style: const TextStyle(
                  fontSize: 11.5, color: Colors.white38, height: 1.4)),
        ],
        if (_open) ...[
          const SizedBox(height: 8),
          widget.child,
        ],
      ],
    );

    if (!widget.framed) {
      return Padding(
        padding: EdgeInsets.only(bottom: _open ? 16 : 6),
        child: body,
      );
    }
    return Container(
      margin: EdgeInsets.only(bottom: _open ? 14 : 6),
      padding: EdgeInsets.fromLTRB(12, 8, 12, _open ? 2 : 8),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: .025),
        borderRadius: BorderRadius.circular(7),
        border: Border(
            left: BorderSide(
                color: Colors.white.withValues(alpha: .12), width: 2)),
      ),
      child: body,
    );
  }
}
