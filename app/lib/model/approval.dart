/// A decision waiting for the operator.
///
/// The environment raises the card and holds it; what the decision MEANS
/// belongs to whichever plugin declared the gate. The app is in the same
/// position: it can render any card from its payload, and gives the two that
/// reach a stranger — a page going on a public URL, an email going to someone
/// who did not ask — a bespoke layout, because a JSON dump is not good enough
/// when that is the question.
class Approval {
  Approval(this.raw);

  final Map<String, dynamic> raw;

  String get id => raw['id'] as String;
  String get kind => (raw['kind'] ?? '') as String;
  String get roomId => (raw['room_id'] ?? '') as String;
  String get summary => (raw['summary'] ?? '') as String;
  String get requestedBy => (raw['requesting_agent'] ?? '') as String;
  double get ts => (raw['ts'] ?? 0).toDouble();

  Map<String, dynamic> get payload =>
      (raw['payload'] as Map?)?.cast<String, dynamic>() ?? const {};

  String? get recordId => payload['lead_id'] as String?;
  String get business => (payload['business'] ?? '') as String;

  /// The plugin's own sentence about what saying yes does. Worth showing
  /// prominently: it is the only part written for the person deciding.
  String get meaning => (payload['what_this_means'] ?? '') as String;

  /// Cards that only report something. Dismissing is the whole interaction —
  /// there is no handler behind them.
  bool get informational =>
      const {'agent_crashed', 'reply_received', 'rerun_halted'}.contains(kind);
}
