import 'dart:convert';
import 'dart:io';

/// HTTP to the environment.
///
/// One place that knows the base URL, because the app is meant to point at a
/// server the operator chooses — a local one today, and later one castle among
/// several. Nothing else in the app should build a URL.
class Api {
  Api(this.base);

  /// e.g. `http://127.0.0.1:8765`. No trailing slash.
  String base;

  final HttpClient _http = HttpClient()
    ..connectionTimeout = const Duration(seconds: 5);

  Uri _uri(String path) => Uri.parse('$base$path');

  Future<dynamic> get(String path) async {
    final req = await _http.getUrl(_uri(path));
    final res = await req.close();
    final body = await res.transform(utf8.decoder).join();
    if (res.statusCode >= 400) {
      throw ApiError(path, res.statusCode, body);
    }
    return body.isEmpty ? null : jsonDecode(body);
  }

  Future<dynamic> send(String method, String path, [Object? payload]) async {
    final req = await _http.openUrl(method, _uri(path));
    req.headers.contentType = ContentType.json;
    if (payload != null) req.write(jsonEncode(payload));
    final res = await req.close();
    final body = await res.transform(utf8.decoder).join();
    if (res.statusCode >= 400) {
      throw ApiError(path, res.statusCode, body);
    }
    return body.isEmpty ? null : jsonDecode(body);
  }

  Future<dynamic> post(String path, [Object? payload]) =>
      send('POST', path, payload);

  /// The websocket URL for this server.
  Uri get socket {
    final u = Uri.parse(base);
    return u.replace(scheme: u.scheme == 'https' ? 'wss' : 'ws', path: '/ws');
  }

  void close() => _http.close(force: true);
}

class ApiError implements Exception {
  ApiError(this.path, this.status, this.body);
  final String path;
  final int status;
  final String body;

  /// The environment answers a refused action with a readable sentence — a
  /// 409 explaining why a record may not move, for instance — so show that
  /// rather than the status code.
  String get message {
    try {
      final j = jsonDecode(body);
      if (j is Map && j['detail'] != null) return j['detail'].toString();
    } catch (_) {}
    return body.isEmpty ? '$status on $path' : body;
  }

  @override
  String toString() => 'ApiError($status $path): $message';
}
