import 'dart:async';

/// Run every step, independently, and report which ones failed.
///
/// The app used to load its six things inside one `try`. The first failure
/// cancelled every load after it — and the records were loaded LAST, so almost
/// any hiccup left the board with no work in it. Worse, each loader also
/// swallowed its own failure, so what you saw was an EMPTY list; and in this
/// environment an empty list is a legitimate state — an install with no
/// plugins is the documented correct empty state. A failed `/plugins` was
/// therefore indistinguishable from a server with none.
///
/// So: every step runs whatever the others do, and what did not load is named
/// rather than shown as nothing.
///
/// `attempts` above 1 retries a failing step, which is worth one go when the
/// app has just started the server itself and connected the instant the port
/// answered.
Future<Set<String>> runSteps(
  Map<String, Future<void> Function()> steps, {
  int attempts = 1,
  Future<void> Function(Duration)? wait,
  void Function(String name, Object error)? onError,
}) async {
  final failed = <String>{};
  for (final entry in steps.entries) {
    for (var attempt = 1; attempt <= attempts; attempt++) {
      try {
        await entry.value();
        break;
      } catch (e) {
        if (attempt < attempts) {
          await (wait ?? _sleep)(const Duration(milliseconds: 600));
          continue;
        }
        failed.add(entry.key);
        onError?.call(entry.key, e);
      }
    }
  }
  return failed;
}

Future<void> _sleep(Duration d) => Future<void>.delayed(d);
