import 'dart:convert';
import 'dart:io';

import 'package:path_provider/path_provider.dart';

/// What the app remembers between launches.
///
/// A JSON file rather than a preferences package: there are a handful of
/// values, and the file is readable and editable with a text editor when
/// something goes wrong — which for a setting like "where is the checkout" is
/// most of the time you would want to look at it.
///
/// **Where it lives is asked, not assumed.** The first version put it at
/// `$XDG_CONFIG_HOME/tanrim/app.json`, falling back to `$HOME/.config`, which
/// is right on a desktop and wrong on Android: an app process there has no
/// writable `$HOME`, so every write landed on a path the app may not create,
/// threw, and was swallowed by the catch below. The app forgot the server
/// address on every launch and nothing anywhere said why — which is exactly
/// the symptom that sent an operator back to typing an IP by hand. So the
/// desktop keeps the editable config path and everywhere else asks the
/// platform for its own directory.
///
/// Every read and write is still wrapped. A missing, unreadable or corrupt
/// file must leave the app working with its defaults: this holds
/// conveniences, not data, and losing it should never be the reason the app
/// will not open.
class Prefs {
  Prefs._(this._file, this._values);

  final File _file;
  final Map<String, dynamic> _values;

  File get file => _file;

  /// The desktop path: visible, documented, and editable by hand.
  static File? _configHome() {
    if (!Platform.isLinux && !Platform.isMacOS && !Platform.isWindows) {
      return null;
    }
    final env = Platform.environment;
    final base = env['XDG_CONFIG_HOME']?.trim().isNotEmpty == true
        ? env['XDG_CONFIG_HOME']!
        : (env['HOME']?.trim().isNotEmpty == true
            ? '${env['HOME']}/.config'
            : null);
    return base == null ? null : File('$base/tanrim/app.json');
  }

  static Future<Prefs> load() async {
    File? f = _configHome();
    try {
      f ??= File('${(await getApplicationSupportDirectory()).path}/app.json');
    } catch (_) {
      // No platform answer either. The app still runs; it just forgets.
    }
    return Prefs.at(f ?? File('app.json'));
  }

  /// Read one named file. The directory is the only part that is platform
  /// business, so a test can say where without a fake environment.
  factory Prefs.at(File f) {
    try {
      if (!f.existsSync()) return Prefs._(f, {});
      final raw = jsonDecode(f.readAsStringSync());
      return Prefs._(f, raw is Map ? raw.cast<String, dynamic>() : {});
    } catch (_) {
      return Prefs._(f, {});
    }
  }

  String string(String key, [String fallback = '']) {
    final v = _values[key];
    return v is String ? v : fallback;
  }

  void set(String key, Object? value) {
    if (value == null) {
      _values.remove(key);
    } else {
      _values[key] = value;
    }
    save();
  }

  void save() {
    try {
      _file.parent.createSync(recursive: true);
      _file.writeAsStringSync(const JsonEncoder.withIndent('  ').convert(_values));
    } catch (_) {
      // Not worth telling anyone about: the app works without it, and a
      // dialog about a preferences file is noise at the worst moment.
    }
  }
}
