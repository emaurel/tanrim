import 'package:flutter/material.dart';

import 'api/client.dart';
import 'server/prefs.dart';
import 'server/process.dart';
import 'api/live.dart';
import 'model/approval.dart';
import 'model/castle.dart';
import 'model/record.dart';
import 'model/world.dart';
import 'ui/approvals.dart';
import 'ui/castle_dialogs.dart';
import 'ui/castle_panel.dart';
import 'ui/kingdom.dart';
import 'ui/record_window.dart';
import 'ui/windows.dart';
import 'ui/map_view.dart';
import 'ui/room_panel.dart';
import 'ui/settings.dart';

void main() => runApp(const TanrimApp());

class TanrimApp extends StatelessWidget {
  const TanrimApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Tanrim',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        brightness: Brightness.dark,
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF7AD7D7),
          brightness: Brightness.dark,
        ),
        scaffoldBackgroundColor: const Color(0xFF12141A),
      ),
      home: const WorldPage(),
    );
  }
}

class WorldPage extends StatefulWidget {
  const WorldPage({super.key});

  @override
  State<WorldPage> createState() => _WorldPageState();
}

class _WorldPageState extends State<WorldPage> {
  /// Where the environment is. One field, because the app is meant to point at
  /// a server the operator chooses — and later at one castle among several.
  final _prefs = Prefs.load();
  late final _server = TextEditingController(
      text: _prefs.string('server', 'http://127.0.0.1:8765'));

  /// Where the checkout is, so the app can run the server itself. Remembered,
  /// because typing a path every launch is the kind of friction that sends
  /// you back to the terminal.
  late final _repo = TextEditingController(
      text: _prefs.string('repo', ServerProcess.guess()))
    ..addListener(_rememberRepo);
  late final ServerProcess _process = ServerProcess(repo: _repo.text);

  /// Every plugin on disk, running or not — `/plugins/catalog`.
  List<Map<String, dynamic>> _catalog = const [];

  Api? _api;
  Live? _live;

  List<Room> _rooms = const [];
  Map<String, int> _badges = const {};
  String? _selected;
  String _state = 'not connected';
  bool _connected = false;

  List<WorkRecord> _records = const [];
  List<String> _stages = const [];
  List<String> _deadStages = const [];
  String? _selectedRecord;
  String? _selectedCastle;

  List<Approval> _approvals = const [];

  /// Which windows are open, back to front by the order they were opened.
  ///
  /// Ids rather than widgets: what a window SHOWS is rebuilt from live state
  /// on every frame, and holding the widget would freeze it at the moment it
  /// was opened.
  final List<String> _windows = ['kingdom'];

  /// So the Kingdom window can move the camera: clicking a castle there should
  /// take you to it, not just open a panel about somewhere you cannot see.
  final GlobalKey<MapViewState> _mapKey = GlobalKey<MapViewState>();

  void _open(String id) {
    if (_windows.contains(id)) {
      // Already open: bring it to the front rather than opening a second.
      setState(() {
        _windows.remove(id);
        _windows.add(id);
      });
      return;
    }
    setState(() => _windows.add(id));
  }

  void _close(String id) => setState(() => _windows.remove(id));

  List<Castle> _castles = const [];
  Web _web = const Web();
  Set<(int, int)> _taken = const {};
  List<Map<String, dynamic>> _buildable = const [];
  List<Map<String, dynamic>> _plugins = const [];
  List<String> _kinds = const [];

  @override
  void initState() {
    super.initState();
    _connect();
  }

  @override
  void dispose() {
    _live?.close();
    _api?.close();
    // A server this app started goes with it. Left behind it holds the port,
    // and the next launch would find it, adopt it, and be unable to stop it.
    _process.disposeSync();
    _server.dispose();
    _repo.dispose();
    super.dispose();
  }

  void _rememberRepo() {
    _process.repo = _repo.text.trim();
    _prefs.set('repo', _repo.text.trim());
  }

  /// Start the server, then connect to it.
  ///
  /// One action rather than two, because a started server nobody connected to
  /// looks exactly like a server that failed to start.
  Future<String> _startServer() async {
    final problem = await _process.start();
    if (problem != null) return problem;
    _server.text = _process.base;
    _prefs.set('server', _server.text);
    await _connect();
    return 'started, and connected to ${_process.base}';
  }

  Future<String> _stopServer() async {
    final problem = await _process.stop();
    if (problem != null) return problem;
    _live?.close();
    _api?.close();
    _api = null;
    if (mounted) {
      setState(() {
        _connected = false;
        _state = 'stopped';
        _rooms = const [];
        _castles = const [];
      });
    }
    return 'stopped';
  }

  Future<void> _connect() async {
    _live?.close();
    _api?.close();
    setState(() {
      _state = 'connecting…';
      _connected = false;
      _rooms = const [];
    });

    final api = Api(_server.text.trim().replaceAll(RegExp(r'/+$'), ''));
    _api = api;
    try {
      await _loadRooms();
      setState(() => _connected = true);
      await _loadPlugins();
      await _loadCastles();
      await _loadCatalog();
      await _loadApprovals();
      await _loadBoard();
    } catch (e) {
      setState(() => _state = e is ApiError ? e.message : '$e');
      return;
    }

    final live = Live(api.socket);
    _live = live;
    live.events.listen((e) async {
      switch (e.kind) {
        case LiveKind.agentsChanged:
          setState(() {});
          // A castle is `working` when a run is in flight in it, and that
          // comes from `/castles` — which was fetched on connect and never
          // again. So a build could run for fifteen minutes with the sprite
          // walking to its bench and the castle still drawn idle.
          //
          // Refetched only when the set of BUSY workers changes, not on every
          // sprite move: positions arrive many times a second, and the answer
          // cannot have changed unless somebody started or stopped.
          await _castleStatusMayHaveChanged();
        case LiveKind.approvalsChanged:
          await _loadApprovals();
          await _loadBoard();
        case LiveKind.worldChanged:
          // Someone reloaded the plugins or built a castle — possibly in
          // another window.
          await _loadRooms();
          await _loadPlugins();
          await _loadCastles();
        case LiveKind.disconnected:
          setState(() => _state = 'reconnecting…');
        case LiveKind.connected:
          setState(() => _state = '${_rooms.length} rooms · live');
        case LiveKind.talk:
          break;
      }
    });
    await live.connect();
  }

  /// The board. Its stages and their order come from the server, so a plugin
  /// adding a pipeline needs no change here.
  Future<void> _loadBoard() async {
    final api = _api;
    if (api == null) return;
    try {
      final b = await api.get('/leads?slim=1') as Map<String, dynamic>;
      if (!mounted) return;
      setState(() {
        _records = ((b['leads'] ?? []) as List)
            .map((r) => WorkRecord(r as Map<String, dynamic>))
            .toList();
        _stages = ((b['stages'] ?? []) as List).cast<String>();
        _deadStages = ((b['dead_stages'] ?? []) as List).cast<String>();
      });
    } catch (_) {
      // A board that will not load is not worth killing the map for.
    }
  }

  /// The map itself.
  ///
  /// Extracted from `_connect` because a plugin reload changes the rooms too,
  /// and a second copy of "parse /rooms into Room objects" is how one of them
  /// ends up not setting `_state`.
  /// Every plugin directory, running or not.
  Future<void> _loadCatalog() async {
    final api = _api;
    if (api == null) return;
    try {
      final d = await api.get('/plugins/catalog') as Map<String, dynamic>;
      final list = ((d['plugins'] ?? []) as List)
          .map((p) => (p as Map).cast<String, dynamic>())
          .toList();
      if (!mounted) return;
      setState(() => _catalog = list);
    } catch (_) {
      // An older server has no catalog. The rest of the panel still works.
    }
  }

  /// After anything that changes what is installed.
  Future<String> _afterPluginChange(Map<String, dynamic> out,
      String Function() describe) async {
    if (out['ok'] != true) {
      return '${out['error'] ?? 'it did not work'}';
    }
    await _loadRooms();
    await _loadPlugins();
    await _loadCastles();
    await _loadCatalog();
    return describe();
  }

  Future<String> _installPlugin(String source) async {
    final api = _api;
    if (api == null) return 'not connected to a server';
    final out = (await api.post('/plugins/install', {'source': source}))
        as Map<String, dynamic>;
    return _afterPluginChange(out, () {
      final id = ((out['installed'] ?? {}) as Map)['id'];
      final problems =
          (((out['reload'] ?? {}) as Map)['problems'] as List?) ?? const [];
      return 'Installed $id.'
          '${problems.isEmpty ? '' : '\n\nReported at boot:\n  '
              '${problems.join('\n  ')}'}';
    });
  }

  Future<String> _setPluginEnabled(String id, bool enabled) async {
    final api = _api;
    if (api == null) return 'not connected to a server';
    final out = (await api
            .post('/plugins/$id/${enabled ? 'enable' : 'disable'}', {}))
        as Map<String, dynamic>;
    return _afterPluginChange(out, () => enabled ? 'enabled' : 'disabled');
  }

  Future<String> _removePlugin(String id, bool force) async {
    final api = _api;
    if (api == null) return 'not connected to a server';
    final out = (await api.send(
            'DELETE', '/plugins/$id${force ? '?force=true' : ''}'))
        as Map<String, dynamic>;
    return _afterPluginChange(out, () => 'deleted');
  }

  Future<void> _loadRooms() async {
    final api = _api;
    if (api == null) return;
    final rooms = (await api.get('/rooms') as List)
        .map((r) => Room.fromJson(r as Map<String, dynamic>))
        .toList();
    if (!mounted) return;
    setState(() {
      _rooms = rooms;
      _state = '${rooms.length} rooms';
    });
  }

  /// Castles come from `/plugins`: a plugin that declares rooms is a castle,
  /// an extension that only patches is not.
  Future<void> _loadPlugins() async {
    final api = _api;
    if (api == null) return;
    try {
      final d = await api.get('/plugins') as Map<String, dynamic>;
      final list = ((d['plugins'] ?? []) as List)
          .map((p) => (p as Map).cast<String, dynamic>())
          .toList();
      if (!mounted) return;
      setState(() {
        _plugins = list;
        _kinds = ((d['kinds'] ?? []) as List).cast<String>();
      });
    } catch (_) {
      // The map still works without them; it just cannot group.
    }
  }

  /// Who was busy last time we looked, so a sprite walking does not refetch.
  String _busyKey = '';

  Future<void> _castleStatusMayHaveChanged() async {
    final busy = (_live?.agents.values ?? const <AgentState>[])
        .where((a) => a.busy)
        .map((a) => a.id)
        .toList()
      ..sort();
    final key = busy.join(',');
    if (key == _busyKey) return;
    _busyKey = key;
    await _loadCastles();
  }

  /// The castles, the empty land around them, and what can be built on it.
  ///
  /// From the server rather than grouped here. Grouping rooms by which plugin
  /// declared them could only ever produce ONE castle per plugin, and — more
  /// quietly — knew nothing about where they sat. The plot geometry has to
  /// match the room coordinates exactly, and the only way to be sure of that
  /// is for one side to own both.
  Future<void> _loadCastles() async {
    final api = _api;
    if (api == null) return;
    try {
      final d = await api.get('/castles') as Map<String, dynamic>;
      final castles = ((d['castles'] ?? []) as List)
          .map((c) => Castle.fromJson((c as Map).cast<String, dynamic>())
              .withRooms(_rooms))
          .toList();
      final web = Web.fromJson(
          ((d['web'] ?? const {}) as Map).cast<String, dynamic>());
      final taken = {
        for (final t in ((d['taken'] ?? []) as List))
          ((t as List)[0] as int, t[1] as int),
      };
      final buildable = ((d['buildable'] ?? []) as List)
          .map((p) => (p as Map).cast<String, dynamic>())
          .toList();
      if (!mounted) return;
      setState(() {
        _castles = castles;
        _web = web;
        _taken = taken;
        _buildable = buildable;
      });
    } catch (_) {
      // An older server has no castles. The map still draws its rooms.
    }
  }

  /// Build one on an empty plot.
  Future<String> _buildCastle(String plugin, int ring, int slot) async {
    final api = _api;
    if (api == null) return 'not connected to a server';
    final out = (await api.post('/castles',
            {'plugin': plugin, 'ring': ring, 'slot': slot}))
        as Map<String, dynamic>;
    if (out['ok'] != true) return '${out['error']}';
    await _loadRooms();
    await _loadCastles();
    return 'built ${(out['castle'] as Map)['name']}';
  }

  Future<String> _renameCastle(String id, String name) async {
    final api = _api;
    if (api == null) return 'not connected to a server';
    final out = (await api.send('PATCH', '/castles/$id', {'name': name}))
        as Map<String, dynamic>;
    if (out['ok'] != true) return '${out['error']}';
    await _loadCastles();
    return '';
  }

  /// Clicked empty land: ask what to build, then build it.
  Future<void> _onPlotTapped(int ring, int slot) async {
    final chosen = await askWhatToBuild(context,
        buildable: _buildable, ring: ring, slot: slot);
    if (chosen == null || !mounted) return;
    final said = await _buildCastle(chosen, ring, slot);
    if (mounted) _say(said);
  }

  /// Clicked a castle: open its panel in the right-hand column.
  ///
  /// Not a dialog. A castle is a PLACE with work in it — its rooms, its
  /// records, what it is an instance of — and a modal that asks for a name and
  /// goes away can hold none of that.
  void _onCastleTapped(Castle castle) {
    setState(() => _selectedCastle = castle.id);
    _open('castle:${castle.id}');
  }

  Future<void> _askRaze(Castle castle) async {
    if (!await confirmRaze(context, castle)) return;
    final said = await _razeCastle(castle.id);
    if (!mounted) return;
    _close('castle:${castle.id}');
    if (_selectedCastle == castle.id) {
      setState(() => _selectedCastle = null);
    }
    _say(said);
  }

  Castle? _castleById(String? id) {
    if (id == null) return null;
    for (final c in _castles) {
      if (c.id == id) return c;
    }
    return null;
  }

  void _say(String message) {
    if (message.isEmpty || !mounted) return;
    ScaffoldMessenger.of(context)
        .showSnackBar(SnackBar(content: Text(message)));
  }

  Future<String> _razeCastle(String id) async {
    final api = _api;
    if (api == null) return 'not connected to a server';
    final out =
        (await api.send('DELETE', '/castles/$id')) as Map<String, dynamic>;
    if (out['ok'] != true) return '${out['error']}';
    await _loadRooms();
    await _loadCastles();
    final left = out['records_left'] ?? 0;
    return 'razed${left == 0 ? '' : ' — $left record(s) kept'}';
  }

  /// Pending approvals per castle, for the badge you can read from far off.
  Map<String, int> get _castleBadges {
    final out = <String, int>{};
    for (final c in _castles) {
      var n = 0;
      for (final r in c.rooms) {
        n += _badges[r.id] ?? 0;
      }
      if (n > 0) out[c.pluginId] = n;
    }
    return out;
  }

  Future<void> _loadApprovals() async {
    final api = _api;
    if (api == null) return;
    try {
      final a = await api.get('/approvals?status=pending');
      if (!mounted) return;
      setState(() {
        _approvals = ((a['approvals'] ?? []) as List)
            .map((x) => Approval(x as Map<String, dynamic>))
            .toList();
        _badges = _countsByRoom(a);
      });
    } catch (_) {
      // Not worth killing the map for.
    }
  }

  Map<String, int> _countsByRoom(dynamic payload) {
    if (payload is Map && payload['counts_by_room'] is Map) {
      return (payload['counts_by_room'] as Map)
          .map((k, v) => MapEntry(k as String, (v as num).toInt()));
    }
    return const {};
  }

  @override
  Widget build(BuildContext context) {
    final agents = _live?.agents.values.toList() ?? const <AgentState>[];

    return Scaffold(
      body: !_connected
          ? _disconnected()
          // The map fills the window and the panel sits OVER it, so folding
          // the panel away gives the map the whole screen rather than a
          // slightly wider column.
          : Stack(children: [
              Positioned.fill(
                child: MapView(
                  key: _mapKey,
                  rooms: _rooms,
                  agents: agents,
                  badges: _badges,
                  castles: _castles,
                  castleBadges: _castleBadges,
                  web: _web,
                  taken: _taken,
                  onPlotTapped: _onPlotTapped,
                  onCastleTapped: _onCastleTapped,
                  selectedRoom: _selected,
                  onRoomTapped: (r) {
                    setState(() => _selected = r.id);
                    _open('room:${r.id}');
                  },
                ),
              ),
              Positioned.fill(
                child: WindowLayer(
                  windows: _openWindows(agents),
                  onClose: _close,
                ),
              ),
              _controls(),
            ]),
    );
  }

  Widget _disconnected() => Center(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Text(_state, style: const TextStyle(color: Colors.white54)),
          const SizedBox(height: 14),
          FilledButton.tonal(
            onPressed: () => _settings().open(context, tab: 'connection'),
            child: const Text('Connection settings'),
          ),
        ]),
      );

  Settings _settings() => Settings(
        server: _server,
        onConnect: () {
          Navigator.of(context).maybePop();
          _connect();
        },
        connected: _connected,
        status: _state,
        rooms: _rooms.length,
        plugins: _plugins,
        stages: _stages,
        kinds: _kinds,
        onReload: _reloadPlugins,
        repo: _repo,
        process: _process,
        onStartServer: _startServer,
        onStopServer: _stopServer,
        catalog: _catalog,
        onInstall: _installPlugin,
        onSetEnabled: _setPluginEnabled,
        onRemove: _removePlugin,
        onRefreshPlugins: _pluginLists,
      );

  /// What is on disk and what is running, freshly fetched.
  ///
  /// Returned rather than only stored, because the settings panel is a modal
  /// built from a snapshot: updating this state does not reach a panel that is
  /// already open, so it asks for the new lists itself.
  Future<(List<Map<String, dynamic>>, List<Map<String, dynamic>>)>
      _pluginLists() async {
    await _loadCatalog();
    await _loadPlugins();
    return (_catalog, _plugins);
  }

  /// Re-read `plugins/` on the server, then refetch everything derived from it.
  ///
  /// The refetch is the point: rooms, the castles and the pipeline are all
  /// answers the server gave once and this app cached, so a reload that
  /// changed them server-side without this would leave the map drawing a
  /// world that no longer exists.
  Future<String> _reloadPlugins() async {
    final api = _api;
    if (api == null) return 'not connected to a server';
    final out = (await api.post('/plugins/reload')) as Map<String, dynamic>;

    if (out['ok'] != true) {
      final detail = '${out['detail'] ?? ''}';
      return '${out['error']}'
          '${detail.isEmpty ? '' : '\n\n$detail'}';
    }

    await _loadRooms();
    await _loadPlugins();

    final added = ((out['added'] ?? []) as List).cast<String>();
    final removed = ((out['removed'] ?? []) as List).cast<String>();
    final problems = ((out['problems'] ?? []) as List).cast<String>();

    final lines = <String>[
      if (added.isEmpty && removed.isEmpty)
        'No change — the same ${(out['plugins'] as List).length} plugin(s), '
            '${out['rooms']} rooms.'
      else ...[
        if (added.isNotEmpty) 'Installed: ${added.join(', ')}',
        if (removed.isNotEmpty) 'Removed: ${removed.join(', ')}',
        '${out['rooms']} rooms now.',
      ],
      if (problems.isNotEmpty) '\nReported at boot:\n  ${problems.join('\n  ')}',
    ];
    return lines.join('\n');
  }

  /// The only chrome: fold the panel, and the menu. Everything that was in
  /// the old top bar lives in Settings now — a bar across the top of a map is
  /// a bar across the top of the thing you came to look at.
  Widget _controls() {
    return Positioned(
      top: 10,
      left: 12,
      child: Row(children: [
        _round(
          icon: Icons.menu,
          tip: 'settings',
          onTap: () => _settings().open(context),
        ),
        const SizedBox(width: 8),
        _round(
          icon: Icons.castle_outlined,
          tip: 'the kingdom',
          onTap: () => _open('kingdom'),
        ),
        const SizedBox(width: 8),
        _round(
          icon: Icons.how_to_vote_outlined,
          tip: 'approvals',
          onTap: () => _open('approvals'),
          badge: _approvals.length,
        ),
      ]),
    );
  }

  /// The open windows, rebuilt from live state every frame.
  /// The records an agent is busy on right now.
  ///
  /// Derived rather than asked for: every agent already streams the record it
  /// was spawned for and whether it is busy, so "is this record being worked
  /// on" is a question the client can already answer. Adding a field to the
  /// record for it would be a second copy of a fact, and the one that goes
  /// stale — a record is written when a run ENDS, and the interesting moment
  /// is while it is in flight.
  Set<String> _workingRecords(List<AgentState> agents) => {
        for (final a in agents)
          if (a.busy && (a.recordId ?? '').isNotEmpty) a.recordId!,
      };

  /// Start a record's next step from its row.
  ///
  /// The server picks the room from the stage — the same call the record
  /// window makes, so a row and an open record cannot disagree about what
  /// "run" means.
  Future<void> _runRecord(WorkRecord r) => _recordAction(
        () => _api!.post('/leads/${r.id}/run-next'),
        '${r.name}: started',
      );

  Future<void> _stopRecord(WorkRecord r) => _recordAction(
        () => _api!.post('/leads/${r.id}/stop', {'reason': 'stopped from the board'}),
        '${r.name}: stopped',
      );

  Future<void> _recordAction(Future<dynamic> Function() send, String ok) async {
    String message = ok;
    try {
      final out = await send();
      // A refusal answers `ok: false` with a sentence — nothing running, or a
      // stage nobody works. Half the refusals here are deliberate, and the
      // sentence is the useful part.
      if (out is Map && out['ok'] == false) message = '${out['error']}';
    } on ApiError catch (e) {
      message = e.message;
    } catch (e) {
      message = '$e';
    }
    if (!mounted) return;
    ScaffoldMessenger.maybeOf(context)?.showSnackBar(
        SnackBar(content: Text(message), duration: const Duration(seconds: 4)));
  }

  List<AppWindow> _openWindows(List<AgentState> agents) {
    final out = <AppWindow>[];
    for (final id in _windows) {
      final w = _window(id, agents);
      if (w != null) out.add(w);
    }
    return out;
  }

  AppWindow? _window(String id, List<AgentState> agents) {
    if (id == 'kingdom') {
      return AppWindow(
        id: id,
        title: 'Kingdom',
        icon: Icons.castle_outlined,
        subtitle: '${_castles.length} castles',
        initialSize: const Size(380, 480),
        child: Kingdom(
          castles: _castles,
          badges: _castleBadges,
          onOpen: _goToCastle,
        ),
      );
    }
    if (id == 'approvals') {
      return AppWindow(
        id: id,
        title: 'Approvals',
        icon: Icons.how_to_vote_outlined,
        subtitle: _approvals.isEmpty ? 'none' : '${_approvals.length} waiting',
        initialSize: const Size(560, 640),
        child: Approvals(
          api: _api!,
          approvals: _approvals,
          castleNames: {for (final c in _castles) c.id: c.name},
          onResolved: () async {
            await _loadApprovals();
            await _loadBoard();
          },
          onOpenRecord: (recordId) {
            setState(() => _selectedRecord = recordId);
            _open('record:$recordId');
          },
        ),
      );
    }
    if (id.startsWith('castle:')) {
      final castle = _castleById(id.substring(7));
      if (castle == null) return null;
      return AppWindow(
        id: id,
        title: castle.name,
        icon: Icons.castle_outlined,
        subtitle: castle.pluginName,
        initialSize: const Size(460, 620),
        // Renamed from the title bar, where the name already is.
        onRename: (name) => _renameCastle(castle.id, name),
        child: CastlePanel(
          castle: castle,
          rooms: _rooms,
          badges: _badges,
          records: _records,
          stages: _stages,
          deadStages: _deadStages,
          selectedRecord: _selectedRecord,
          working: _workingRecords(agents),
          onRunRecord: _runRecord,
          onStopRecord: _stopRecord,
          agents: agents,
          onTapRecord: (r) {
            setState(() => _selectedRecord = r.id);
            _open('record:${r.id}');
          },
          onRaze: () => _askRaze(castle),
          onOpenRoom: (r) {
            setState(() => _selected = r.id);
            _open('room:${r.id}');
          },
        ),
      );
    }
    if (id.startsWith('record:')) {
      final recordId = id.substring(7);
      final row = _records.where((r) => r.id == recordId).firstOrNull;
      return AppWindow(
        id: id,
        title: row?.name ?? 'Record',
        icon: Icons.description_outlined,
        subtitle: row?.kind ?? '',
        initialSize: const Size(520, 700),
        child: RecordWindow(
          api: _api!,
          recordId: recordId,
          working: _workingRecords(agents).contains(recordId),
          // Both lists, because a hand-move is the escape hatch FROM the
          // pipeline — including into a terminal stage, which is where a
          // record goes when the answer is "stop working this".
          stages: [..._stages, ..._deadStages],
          onOpenRoom: (roomId) {
            setState(() => _selected = roomId);
            _open('room:$roomId');
          },
        ),
      );
    }
    if (id.startsWith('room:')) {
      final roomId = id.substring(5);
      final room = _rooms.where((r) => r.id == roomId).cast<Room?>().firstOrNull;
      if (room == null) return null;
      return AppWindow(
        id: id,
        title: room.name,
        icon: Icons.meeting_room_outlined,
        subtitle: _castleById(room.castleId)?.name ?? '',
        initialSize: const Size(460, 620),
        child: RoomPanel(
          api: _api!,
          room: room,
          here: agents.where((a) => a.roomId == room.id).toList(),
          onOpenRecord: (recordId) {
            setState(() => _selectedRecord = recordId);
            _open('record:$recordId');
          },
          onChanged: () async {
            await _loadBoard();
            await _loadApprovals();
          },
        ),
      );
    }
    return null;
  }

  /// Open a castle: travel to it on the map, and open its window.
  void _goToCastle(Castle castle) {
    _mapKey.currentState?.flyToCastle(castle);
    setState(() => _selectedCastle = castle.id);
    _open('castle:${castle.id}');
  }

  Widget _round({
    required IconData icon,
    required String tip,
    required VoidCallback onTap,
    int badge = 0,
  }) {
    return Tooltip(
      message: tip,
      child: Stack(clipBehavior: Clip.none, children: [
        Material(
          color: const Color(0xCC1C2029),
          shape: const CircleBorder(),
          child: InkWell(
            customBorder: const CircleBorder(),
            onTap: onTap,
            child: Padding(
              padding: const EdgeInsets.all(9),
              child: Icon(icon, size: 20, color: Colors.white70),
            ),
          ),
        ),
        if (badge > 0)
          Positioned(
            right: -2,
            top: -2,
            child: Container(
              padding:
                  const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
              decoration: BoxDecoration(
                color: const Color(0xFFE23D3D),
                borderRadius: BorderRadius.circular(9),
              ),
              child: Text('$badge',
                  style: const TextStyle(
                      fontSize: 10.5, fontWeight: FontWeight.w700)),
            ),
          ),
      ]),
    );
  }

  /// The right-hand column's tabs. Approvals carries a count, because a gate
  /// nobody notices is a gate that does not work — the whole design assumes
  /// the operator can walk away and be called back.


}

/// Which pane the right-hand column shows.
