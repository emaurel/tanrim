import 'dart:convert';
import 'dart:io';

/// What the app remembers between launches.
///
/// A JSON file rather than a preferences package: there are three values, and
/// the file is readable and editable with a text editor when something goes
/// wrong — which for a setting like "where is the checkout" is most of the
/// time you would want to look at it.
///
/// Every read and write is wrapped. A missing, unreadable or corrupt file must
/// leave the app working with its defaults: this holds conveniences, not data,
/// and losing it should never be the reason the app will not open.
class Prefs {
  Prefs._(this._values);

  final Map<String, dynamic> _values;

  static File get file {
    final env = Platform.environment;
    final base = env['XDG_CONFIG_HOME']?.trim().isNotEmpty == true
        ? env['XDG_CONFIG_HOME']!
        : '${env['HOME'] ?? '.'}/.config';
    return File('$base/tanrim/app.json');
  }

  static Prefs load() {
    try {
      final f = file;
      if (!f.existsSync()) return Prefs._({});
      final raw = jsonDecode(f.readAsStringSync());
      return Prefs._(raw is Map ? raw.cast<String, dynamic>() : {});
    } catch (_) {
      return Prefs._({});
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
      final f = file;
      f.parent.createSync(recursive: true);
      f.writeAsStringSync(const JsonEncoder.withIndent('  ').convert(_values));
    } catch (_) {
      // Not worth telling anyone about: the app works without it, and a
      // dialog about a preferences file is noise at the worst moment.
    }
  }
}
