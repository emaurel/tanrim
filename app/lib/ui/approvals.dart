import 'package:flutter/material.dart';

import '../api/client.dart';
import '../model/approval.dart';
import '../model/record.dart';

/// The operator's queue.
///
/// Every gate in this system exists so a person decides something
/// irreversible, so the card has to carry enough to decide ON — and refusing
/// has to be as easy as agreeing, with somewhere to say why. A rejection with
/// no reason produces a rebuild identical to the one that was rejected.
class Approvals extends StatelessWidget {
  const Approvals({
    super.key,
    required this.api,
    required this.approvals,
    required this.onResolved,
    this.onOpenRecord,
  });

  final Api api;
  final List<Approval> approvals;
  final VoidCallback onResolved;
  final void Function(String recordId)? onOpenRecord;

  @override
  Widget build(BuildContext context) {
    if (approvals.isEmpty) {
      return const Center(
        child: Padding(
          padding: EdgeInsets.all(24),
          child: Text('nothing waiting on you',
              style: TextStyle(color: Colors.white38)),
        ),
      );
    }
    // Oldest first: a card that has been sitting for two days is the one to
    // look at, not the one that arrived while you were reading.
    final sorted = [...approvals]..sort((a, b) => a.ts.compareTo(b.ts));
    return ListView.builder(
      padding: const EdgeInsets.all(12),
      itemCount: sorted.length,
      itemBuilder: (_, i) => ApprovalCard(
        api: api,
        approval: sorted[i],
        onResolved: onResolved,
        onOpenRecord: onOpenRecord,
      ),
    );
  }
}

class ApprovalCard extends StatefulWidget {
  const ApprovalCard({
    super.key,
    required this.api,
    required this.approval,
    required this.onResolved,
    this.onOpenRecord,
  });

  final Api api;
  final Approval approval;
  final VoidCallback onResolved;
  final void Function(String recordId)? onOpenRecord;

  @override
  State<ApprovalCard> createState() => _ApprovalCardState();
}

class _ApprovalCardState extends State<ApprovalCard> {
  final _reason = TextEditingController();
  bool _busy = false;
  bool _expanded = false;
  String? _error;

  @override
  void dispose() {
    _reason.dispose();
    super.dispose();
  }

  Future<void> _resolve(String decision) async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await widget.api.post('/approvals/${widget.approval.id}', {
        'decision': decision,
        'reason': _reason.text.trim(),
      });
      widget.onResolved();
    } catch (e) {
      // The environment refuses a decision it cannot carry out while the card
      // is still PENDING, and says why in a sentence — so the card is still
      // there to try again. Showing the sentence is the whole point.
      setState(() => _error = e is ApiError ? e.message : '$e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final a = widget.approval;
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      decoration: BoxDecoration(
        color: const Color(0xFF1C2029),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: _accent(a.kind).withValues(alpha: .35)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _head(a),
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 0, 12, 12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                ..._body(a),
                if (a.meaning.isNotEmpty) _meaning(a.meaning),
                if (_error != null) _errorBox(_error!),
                const SizedBox(height: 10),
                _actions(a),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _head(Approval a) {
    return InkWell(
      onTap: a.recordId == null || widget.onOpenRecord == null
          ? null
          : () => widget.onOpenRecord!(a.recordId!),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(12, 11, 12, 8),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Container(
              margin: const EdgeInsets.only(top: 3),
              width: 8,
              height: 8,
              decoration: BoxDecoration(
                  color: _accent(a.kind), shape: BoxShape.circle),
            ),
            const SizedBox(width: 9),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(a.summary,
                      style: const TextStyle(
                          fontWeight: FontWeight.w600, height: 1.3)),
                  const SizedBox(height: 2),
                  Text('${a.kind} · ${a.roomId} · ${ago(a.ts)}',
                      style: const TextStyle(
                          fontSize: 11, color: Colors.white38)),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  /// The layout for THIS kind of card. Two gates get a bespoke one; everything
  /// else is rendered from its payload, which is what lets a plugin add a gate
  /// without an app change.
  List<Widget> _body(Approval a) => switch (a.kind) {
        'send_outreach' || 'send_followup' => _email(a),
        'publish_site' => _publish(a),
        _ => _generic(a),
      };

  // -- the two that reach a stranger ---------------------------------------

  List<Widget> _email(Approval a) {
    final p = a.payload;
    final quote = p['quote'] as Map?;
    return [
      _kv('To', '${p['business'] ?? ''}  <${p['to'] ?? '?'}>'),
      _kv('Subject', '${p['subject'] ?? ''}'),
      if (quote != null)
        _kv('Quote', '${quote['amount']} ${quote['currency'] ?? ''}'),
      if (p['preview_url'] != null) _kv('Preview', '${p['preview_url']}'),
      if (p['is_final'] == true)
        _kv('Note', 'the last one — nothing follows this'),
      const SizedBox(height: 8),
      // The body in full, scrollable. This is the artifact a stranger reads,
      // and approving without reading it is the thing the gate exists to stop.
      Container(
        constraints: const BoxConstraints(maxHeight: 260),
        width: double.infinity,
        padding: const EdgeInsets.all(10),
        decoration: BoxDecoration(
          color: Colors.black26,
          borderRadius: BorderRadius.circular(6),
        ),
        child: SingleChildScrollView(
          child: SelectableText(
            '${p['body'] ?? ''}',
            style: const TextStyle(fontSize: 12.5, height: 1.45),
          ),
        ),
      ),
    ];
  }

  List<Widget> _publish(Approval a) {
    final p = a.payload;
    final problems = (p['qa_problems'] as List?) ?? const [];
    return [
      if (p['city'] != null) _kv('Business', '${p['business']} · ${p['city']}'),
      if (p['staging_url'] != null) _kv('Staging', '${p['staging_url']}'),
      if (p['qa_summary'] != null) ...[
        const SizedBox(height: 6),
        Text('${p['qa_summary']}',
            style: const TextStyle(
                fontSize: 12.5, color: Colors.white70, height: 1.4)),
      ],
      if (problems.isNotEmpty) ...[
        const SizedBox(height: 10),
        // What QA still objects to. The gate asks "may this go out", and the
        // open problems are most of the answer.
        Text('${problems.length} open problem'
            '${problems.length == 1 ? "" : "s"}',
            style: const TextStyle(
                fontSize: 11,
                letterSpacing: 1.1,
                color: Colors.white38,
                fontWeight: FontWeight.w700)),
        const SizedBox(height: 4),
        for (final q in problems.take(_expanded ? 20 : 3))
          _problem(q as Map),
        if (problems.length > 3)
          TextButton(
            onPressed: () => setState(() => _expanded = !_expanded),
            child: Text(_expanded
                ? 'fewer'
                : 'all ${problems.length}'),
          ),
      ],
    ];
  }

  Widget _problem(Map q) {
    final severity = '${q['severity'] ?? ''}';
    final colour = severity == 'critical'
        ? const Color(0xFFE05A5A)
        : severity == 'major'
            ? const Color(0xFFE0A458)
            : Colors.white38;
    return Padding(
      padding: const EdgeInsets.only(bottom: 5),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.only(top: 5),
            child: Icon(Icons.circle, size: 7, color: colour),
          ),
          const SizedBox(width: 7),
          Expanded(
            child: Text('${q['problem'] ?? q}',
                style: const TextStyle(fontSize: 12, height: 1.35)),
          ),
        ],
      ),
    );
  }

  // -- anything else -------------------------------------------------------

  /// Rendered from the payload, because the app cannot know what a plugin's
  /// gate is about — and a card it refuses to draw is a decision the operator
  /// cannot make.
  List<Widget> _generic(Approval a) {
    const hide = {'lead_id', 'business', 'what_this_means', 'traceback'};
    final rows = <Widget>[];
    for (final e in a.payload.entries) {
      if (hide.contains(e.key)) continue;
      final v = e.value;
      if (v == null || v == '') continue;
      if (v is Map || v is List) continue;
      rows.add(_kv(e.key, '$v'));
    }
    final trace = a.payload['traceback'];
    if (trace is String && trace.isNotEmpty) {
      rows.add(const SizedBox(height: 8));
      rows.add(Container(
        constraints: const BoxConstraints(maxHeight: 150),
        width: double.infinity,
        padding: const EdgeInsets.all(8),
        decoration: BoxDecoration(
            color: Colors.black38, borderRadius: BorderRadius.circular(6)),
        child: SingleChildScrollView(
          child: SelectableText(
            // The tail is the useful part of a traceback, minus Python's
            // caret lines — they point at a column nobody can see here and
            // they cost two of the eight lines worth showing.
            tracebackTail(trace),
            style: const TextStyle(
                fontSize: 10.5, height: 1.4, fontFeatures: []),
          ),
        ),
      ));
    }
    return rows;
  }

  // -- bits ----------------------------------------------------------------

  Widget _meaning(String s) => Container(
        margin: const EdgeInsets.only(top: 10),
        padding: const EdgeInsets.all(9),
        decoration: BoxDecoration(
          color: Colors.white.withValues(alpha: .04),
          borderRadius: BorderRadius.circular(6),
        ),
        child: Text(s,
            style: const TextStyle(
                fontSize: 11.5, color: Colors.white60, height: 1.4)),
      );

  Widget _errorBox(String s) => Container(
        margin: const EdgeInsets.only(top: 10),
        padding: const EdgeInsets.all(9),
        decoration: BoxDecoration(
          color: const Color(0xFF3A1F22),
          borderRadius: BorderRadius.circular(6),
        ),
        child:
            Text(s, style: const TextStyle(fontSize: 12, color: Color(0xFFFFB4AE))),
      );

  Widget _actions(Approval a) {
    if (_busy) {
      return const Padding(
        padding: EdgeInsets.symmetric(vertical: 6),
        child: SizedBox(
            width: 16, height: 16,
            child: CircularProgressIndicator(strokeWidth: 2)),
      );
    }
    if (a.informational) {
      return Align(
        alignment: Alignment.centerRight,
        child: FilledButton.tonal(
          onPressed: () => _resolve('ignored'),
          child: const Text('Dismiss'),
        ),
      );
    }
    return Column(
      children: [
        TextField(
          controller: _reason,
          style: const TextStyle(fontSize: 12.5),
          decoration: const InputDecoration(
            isDense: true,
            border: OutlineInputBorder(),
            hintText: 'why — the brief for the next attempt',
            hintStyle: TextStyle(fontSize: 12),
          ),
          minLines: 1,
          maxLines: 3,
        ),
        const SizedBox(height: 8),
        // A Wrap, not a Row: three buttons overflow a 400px panel, and this
        // has to survive a phone as well — the gates only buy anything if you
        // can act on them away from the desk.
        Wrap(
          alignment: WrapAlignment.end,
          spacing: 8,
          runSpacing: 6,
          children: [
            TextButton(
              onPressed: () => _resolve('ignored'),
              child: const Text('Dismiss'),
            ),
            // Reject sits left of approve, and needs no confirmation: saying
            // no should never be the harder button.
            OutlinedButton(
              onPressed: () => _resolve('rejected'),
              child: const Text('Reject'),
            ),
            FilledButton(
              onPressed: () => _resolve('approved'),
              child: const Text('Approve'),
            ),
          ],
        ),
      ],
    );
  }

  Widget _kv(String k, String v) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 2),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            SizedBox(
              width: 74,
              child: Text(k,
                  style: const TextStyle(
                      fontSize: 11.5, color: Colors.white38)),
            ),
            Expanded(
              child: SelectableText(v,
                  style: const TextStyle(fontSize: 12.5, height: 1.35)),
            ),
          ],
        ),
      );
}

/// The last few meaningful lines of a Python traceback.
///
/// Lines that are only `^^^^` markers are dropped: they underline a column in
/// a source line the card is not showing, so they are noise that crowds out
/// the frames that say where it broke.
String tracebackTail(String trace, {int lines = 8}) {
  final kept = trace
      .trimRight()
      .split('\n')
      .where((l) => l.trim().isNotEmpty)
      .where((l) => !RegExp(r'^\s*[\^~]+\s*$').hasMatch(l))
      .toList();
  return kept.length <= lines
      ? kept.join('\n')
      : kept.sublist(kept.length - lines).join('\n');
}

Color _accent(String kind) => switch (kind) {
      'send_outreach' || 'send_followup' => const Color(0xFF2A9D8F),
      'publish_site' => const Color(0xFFE0A458),
      'agent_crashed' => const Color(0xFFE05A5A),
      'client_approved' || 'handover' => const Color(0xFF7AD7D7),
      _ => const Color(0xFF8891A6),
    };
