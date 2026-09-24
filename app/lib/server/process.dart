import 'dart:async';
import 'dart:convert';
import 'dart:io';

/// The environment's server, as a process this app can start and stop.
///
/// Until now the app connected to a URL and somebody else was responsible for
/// there being something at it — which is fine while that somebody is you in a
/// terminal, and not fine as the only way in.
///
/// Two things it is careful about, both learned the same way:
///
/// **It does not stop a server it did not start.** One may already be running,
/// begun from a shell with its own flags and its own log, and killing it from
/// here would look like the app's own Stop button working while actually
/// taking down something else. [adopted] says which case you are in.
///
/// **It keeps the output.** A server that exits immediately — a missing venv, a
/// port already taken, a plugin that will not import — says exactly why on
/// stderr and then is gone. Without the last lines kept somewhere, the app can
/// only report that it is not running.
class ServerProcess {
  ServerProcess({required this.repo, this.host = '127.0.0.1', this.port = 8765});

  /// The checkout holding `backend/tanrim` and `.venv`.
  String repo;
  String host;
  int port;

  Process? _proc;
  bool _adopted = false;
  final List<String> _log = [];
  final _changes = StreamController<void>.broadcast();

  /// The last lines the server printed. Capped: a server left running for a
  /// week should not become the app's memory problem.
  static const _maxLines = 400;

  /// Emits whenever the state or the log changes, so a panel can rebuild.
  Stream<void> get changes => _changes.stream;

  List<String> get log => List.unmodifiable(_log);

  /// Running because this app started it — the only case Stop is honest in.
  bool get running => _proc != null;

  /// Something is answering on the port but this app did not start it.
  bool get adopted => _adopted;

  String get base => 'http://$host:$port';

  String get pythonPath => '$repo/.venv/bin/python';

  void _say(String line) {
    for (final l in const LineSplitter().convert(line)) {
      if (l.trim().isEmpty) continue;
      _log.add(l);
    }
    if (_log.length > _maxLines) {
      _log.removeRange(0, _log.length - _maxLines);
    }
    _changes.add(null);
  }

  /// Whether anything at all is serving on the port.
  Future<bool> reachable() async {
    try {
      final s = await Socket.connect(host, port,
          timeout: const Duration(milliseconds: 400));
      s.destroy();
      return true;
    } catch (_) {
      return false;
    }
  }

  /// Notice a server somebody else started, so the panel can say so rather
  /// than offering to start a second one that would fail on the port.
  Future<void> refresh() async {
    if (_proc != null) return;
    final was = _adopted;
    _adopted = await reachable();
    if (was != _adopted) _changes.add(null);
  }

  /// A sensible checkout to start from when nothing has been chosen.
  ///
  /// The app binary is built INTO the checkout — `<repo>/app/build/linux/...`
  /// — so walking up from it finds the repository in the ordinary case, and
  /// the setting is one you never have to type. Falls back to the working
  /// directory, which is right when running from `flutter run`.
  ///
  /// Guessed rather than written into the preferences file at first launch: a
  /// guess that turns out wrong is corrected by typing over it, whereas a
  /// wrong value saved on your behalf looks like something you chose.
  static String guess() {
    for (final start in [
      File(Platform.resolvedExecutable).parent.path,
      Directory.current.path,
    ]) {
      var dir = Directory(start).absolute;
      for (var i = 0; i < 8; i++) {
        if (Directory('${dir.path}/backend/tanrim').existsSync()) {
          return dir.path;
        }
        final up = dir.parent;
        if (up.path == dir.path) break;
        dir = up;
      }
    }
    return '';
  }

  /// What is wrong with this checkout, in the words needed to fix it.
  String? checkRepo() {
    if (repo.trim().isEmpty) return 'set the path to the agent_environment checkout';
    if (!Directory(repo).existsSync()) return 'no directory at $repo';
    if (!Directory('$repo/backend/tanrim').existsSync()) {
      return '$repo does not look like the checkout — no backend/tanrim in it';
    }
    if (!File(pythonPath).existsSync()) {
      return 'no virtualenv at $pythonPath — run: uv venv .venv && '
          'uv pip install --python .venv/bin/python -e .';
    }
    return null;
  }

  /// Start it. Returns null on success, or why it could not.
  Future<String?> start() async {
    if (_proc != null) return 'already running';
    final problem = checkRepo();
    if (problem != null) return problem;
    if (await reachable()) {
      _adopted = true;
      _changes.add(null);
      return 'something is already serving on $host:$port. '
          'Stop it first, or point the app at it instead.';
    }

    _log.clear();
    _say('\$ $pythonPath -m uvicorn tanrim.server:app --port $port');
    final Process proc;
    try {
      proc = await Process.start(
        pythonPath,
        ['-m', 'uvicorn', 'tanrim.server:app',
         '--host', host, '--port', '$port', '--log-level', 'warning'],
        workingDirectory: repo,
        environment: {'PYTHONPATH': '$repo/backend'},
      );
    } catch (e) {
      _say('could not start: $e');
      return '$e';
    }

    _proc = proc;
    _adopted = false;
    proc.stdout.transform(utf8.decoder).listen(_say);
    proc.stderr.transform(utf8.decoder).listen(_say);
    unawaited(proc.exitCode.then((code) {
      // Reported rather than swallowed: a server that dies two seconds after
      // starting is the common case when something is misconfigured, and the
      // reason is in the lines just above.
      _say('— the server exited ($code) —');
      _proc = null;
      _changes.add(null);
    }));
    _changes.add(null);

    // Wait for the port, so the caller can connect straight away instead of
    // guessing how long a boot takes. A boot that imports every plugin and
    // reads its prompts is not instant.
    for (var i = 0; i < 80; i++) {
      if (_proc == null) return 'the server exited while starting up';
      if (await reachable()) return null;
      await Future<void>.delayed(const Duration(milliseconds: 250));
    }
    return 'started, but nothing answered on $host:$port within 20s';
  }

  /// Stop the server this app started. Never touches one it did not.
  Future<String?> stop() async {
    final proc = _proc;
    if (proc == null) {
      return _adopted
          ? 'this server was not started by the app, so it is not the '
              "app's to stop"
          : 'not running';
    }
    proc.kill(ProcessSignal.sigterm);
    try {
      await proc.exitCode.timeout(const Duration(seconds: 8));
    } on TimeoutException {
      proc.kill(ProcessSignal.sigkill);
      _say('— did not stop in 8s; killed —');
    }
    _proc = null;
    _changes.add(null);
    return null;
  }

  /// Called when the app closes. A server left behind holds the port, and the
  /// next launch would adopt it and be unable to stop it.
  void disposeSync() {
    _proc?.kill(ProcessSignal.sigterm);
    _proc = null;
    _changes.close();
  }
}
