/// One unit of work, as a board row.
///
/// The environment calls it a record; this plugin calls it a lead. The app
/// uses the generic word and reads the wire keys the environment actually
/// sends — `lead_id` and friends are deliberately unchanged on the wire, so
/// the translation happens here and nowhere else.
class WorkRecord {
  WorkRecord(this.raw);

  final Map<String, dynamic> raw;

  String get id => raw['id'] as String;
  String get stage => (raw['stage'] ?? '') as String;
  String get kind => (raw['kind'] ?? '') as String;
  String get name => (raw['name'] ?? raw['id']) as String;

  double get updated => (raw['updated_ts'] ?? raw['ts'] ?? 0).toDouble();

  /// The fields a plugin declared worth putting on a row. The app does not
  /// know what any of them MEAN — it shows what it was given, which is what
  /// keeps a new plugin's records readable with no app change.
  Map<String, String> get summary {
    const skip = {
      'id', 'ts', 'updated_ts', 'stage', 'kind', 'name', 'history_len',
      'last', 'source',
    };
    final out = <String, String>{};
    for (final e in raw.entries) {
      if (skip.contains(e.key)) continue;
      final v = e.value;
      if (v == null || v == '' || v == false) continue;
      if (v is Map || v is List) continue;
      out[e.key] = '$v';
    }
    return out;
  }

  /// What happened last, which is what a row is really for.
  String get lastNote {
    final last = raw['last'];
    if (last is! Map) return '';
    final agent = last['agent'] ?? '';
    final note = (last['note'] ?? '').toString();
    return note.isEmpty ? '$agent' : '$agent · $note';
  }
}

/// How long ago, in the shortest honest form.
String ago(double ts) {
  if (ts <= 0) return '';
  final d = DateTime.now()
      .difference(DateTime.fromMillisecondsSinceEpoch((ts * 1000).round()));
  if (d.inMinutes < 1) return 'just now';
  if (d.inMinutes < 60) return '${d.inMinutes}m';
  if (d.inHours < 24) return '${d.inHours}h';
  return '${d.inDays}d';
}
