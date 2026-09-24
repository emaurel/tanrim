// Renders the README's screenshots, headlessly, from the real widgets.
//
//     cd app && flutter test tool/screenshots.dart
//
// A test rather than a running app on purpose. A screenshot taken by hand is
// taken once, at whatever zoom and with whatever windows happened to be open,
// and goes stale the moment the UI moves — `docs/world.png` was three weeks
// old and predated castles, windows and the record view entirely. This walks
// the same widget tree the app builds, so re-running it is the whole job.
//
// The ROOMS and CASTLES come from real dumps of a running server
// (`test/*_fixture.json`), because inventing a map means drawing a map that
// does not exist. The RECORDS are invented: the real ledger holds businesses
// that have not been contacted, and their names and addresses have no place
// in a README.
import 'dart:convert';
import 'dart:math' as math;
import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:tanrim/model/castle.dart';
import 'package:tanrim/model/record.dart';
import 'package:tanrim/model/world.dart';
import 'package:tanrim/api/client.dart';
import 'package:tanrim/ui/record_window.dart';
import 'package:tanrim/ui/castle_panel.dart';
import 'package:tanrim/ui/map_view.dart';
import 'package:tanrim/ui/windows.dart';

/// Where the README looks for them.
final _out = Directory('../docs/img');

/// Real fonts, because the test binding ships one that draws every glyph as a
/// box — including every icon, which is how the first pass came out with a
/// small square where each window's close button should be.
///
/// Text: DejaVu, which is on any Linux box with fontconfig. Icons: the SDK's
/// own `MaterialIcons-Regular.otf`, found by asking Flutter where its cache
/// is rather than by hardcoding a path that is different on every install.
Future<void> _realFont() async {
  Future<void> load(String family, List<String> paths) async {
    for (final path in paths) {
      final file = File(path);
      if (!file.existsSync()) continue;
      await (FontLoader(family)
            ..addFont(
                file.readAsBytes().then((b) => ByteData.view(b.buffer))))
          .load();
      return;
    }
    // ignore: avoid_print
    print('  ! no font for $family — glyphs will draw as boxes');
  }

  await load('Roboto', const ['/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']);
  await load('MaterialIcons', _materialIcons());
}

/// Every place the icon font might be, newest install first.
List<String> _materialIcons() {
  const tail = 'bin/cache/artifacts/material_fonts/MaterialIcons-Regular.otf';
  final roots = <String>[
    // `flutter --version --machine` would be exact but costs a subprocess;
    // FLUTTER_ROOT is set when this runs under `flutter test`.
    if (Platform.environment['FLUTTER_ROOT'] != null)
      Platform.environment['FLUTTER_ROOT']!,
    '${Platform.environment['HOME']}/snap/flutter/common/flutter',
    '/usr/lib/flutter',
    '/opt/flutter',
  ];
  return [for (final r in roots) '$r/$tail'];
}

Widget _app(Widget child) => MaterialApp(
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        brightness: Brightness.dark,
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF7AD7D7),
          brightness: Brightness.dark,
        ),
        scaffoldBackgroundColor: const Color(0xFF12141A),
        fontFamily: 'Roboto',
      ),
      home: Scaffold(body: child),
    );

/// Everything under one `RepaintBoundary`, captured at 2x.
Future<void> _shoot(WidgetTester t, String name, Widget body,
    {Size size = const Size(1280, 800),
    int settle = 0,
    GlobalKey<MapViewState>? frameOn,
    Castle? castle,
    double cropBottom = 0,
    Future<void> Function(WidgetTester)? before}) async {
  t.view
    ..physicalSize = size
    ..devicePixelRatio = 1.0;
  addTearDown(t.view.reset);

  final key = GlobalKey();
  // The boundary carries its own background. Inside the `Scaffold` it captures
  // the BODY only, and the scaffold's colour is painted by the scaffold — so
  // the first run came out with the map floating on white.
  await t.pumpWidget(_app(RepaintBoundary(
    key: key,
    child: ColoredBox(color: const Color(0xFF12141A), child: body),
  )));
  // The map animates: a fixed number of pumps rather than `pumpAndSettle`,
  // which never returns while a ticker is running.
  for (var i = 0; i < 4; i++) {
    await t.pump(const Duration(milliseconds: 80));
  }
  // Frame the shot the way clicking a castle frames it, rather than picking a
  // zoom and a camera offset by hand and having them drift.
  if (frameOn != null && castle != null) {
    frameOn.currentState!.flyToCastle(castle);
    for (var i = 0; i < 20; i++) {
      await t.pump(const Duration(milliseconds: 40));
    }
  }
  if (before != null) {
    await before(t);
    await t.pump(const Duration(milliseconds: 40));
  }
  for (var i = 0; i < 6 + settle; i++) {
    await t.pump(const Duration(milliseconds: 80));
  }

  final boundary =
      key.currentContext!.findRenderObject() as RenderRepaintBoundary;
  var image = await boundary.toImage(pixelRatio: 2.0);

  // The map anchors its camera a THIRD of the way down, not halfway — the app
  // keeps the lower band clear for its controls. A screenshot has no controls,
  // so the picture is captured tall and the empty band cut off, rather than
  // shipping a third of an image of nothing.
  if (cropBottom > 0) {
    final keep = ((size.height - cropBottom) * 2).round();
    final recorder = ui.PictureRecorder();
    ui.Canvas(recorder).drawImage(image, ui.Offset.zero, ui.Paint());
    final picture = recorder.endRecording();
    final cropped = await picture.toImage(image.width, keep);
    picture.dispose();
    image.dispose();
    image = cropped;
  }

  final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
  _out.createSync(recursive: true);
  File('${_out.path}/$name.png').writeAsBytesSync(
      bytes!.buffer.asUint8List(), flush: true);
  // ignore: avoid_print
  print('wrote ${_out.path}/$name.png  (${size.width.toInt()}x'
      '${size.height.toInt()} @2x)');

  // Tear the tree down explicitly. The map drives itself off a `Ticker`, and
  // leaving it mounted at the end of the test left the binding waiting for a
  // frame that never came — every shot rendered correctly and then sat there
  // for the full ten-minute timeout.
  image.dispose();
  _written++;
}

/// How many shots actually reached disk, so the exit below can be honest.
int _written = 0;

// ---------------------------------------------------------------------------
// The world, read off real dumps
// ---------------------------------------------------------------------------

List<Room> _rooms() {
  final raw = jsonDecode(File('test/rooms_fixture.json').readAsStringSync());
  return [for (final r in raw as List) Room.fromJson(r)];
}

/// The castles, each carrying its own rooms.
///
/// `Castle.bounds` falls back to the 52-tile PLOT when a castle has no rooms,
/// and framing on that puts the shot at a zoom where the labels stop drawing —
/// which is the whole thing worth photographing.
List<Castle> _castles([List<Room>? rooms]) {
  final raw = jsonDecode(File('test/castles_fixture.json').readAsStringSync());
  final all = rooms ?? _rooms();
  return [
    for (final c in raw['castles'] as List) Castle.fromJson(c).withRooms(all),
  ];
}

/// One sprite per agent the room actually declares, named as it is named.
///
/// Read off the room dump rather than invented: the whole point of the picture
/// is that these are the rooms and these are the workers in them.
List<AgentState> _agents(List<Room> rooms,
    {Map<String, String> working = const {}}) {
  final out = <AgentState>[];
  for (final r in rooms) {
    var i = 0;
    for (final a in r.agents) {
      final says = working[r.id];
      final busy = says != null && i == 0;
      final bench = busy && r.workbenches.isNotEmpty ? r.workbenches.first : null;
      out.add(AgentState(
        id: a.id,
        name: a.name,
        roomId: r.id,
        x: bench != null
            ? r.position.x + (bench.position?.x ?? 1) + (bench.size?.x ?? 2) / 2
            : r.position.x + 2.0 + i * 1.8,
        y: bench != null
            ? r.position.y + (bench.position?.y ?? 1) + (bench.size?.y ?? 2) + .6
            : r.position.y + r.size.y - 1.6,
        color: a.color,
        busy: busy,
        status: busy ? 'working' : '',
        workbench: bench?.id,
        say: busy ? says : null,
      ));
      i++;
    }
  }
  return out;
}

Set<(int, int)> _taken(List<Castle> castles) =>
    {for (final c in castles) (c.ring, c.slot)};

// ---------------------------------------------------------------------------
// Records, invented
// ---------------------------------------------------------------------------

WorkRecord _record(String name, String stage, String castle,
        {String kind = 'prospect', Map<String, dynamic> extra = const {}}) =>
    WorkRecord({
      'id': name.toLowerCase().replaceAll(' ', '-'),
      'name': name,
      'stage': stage,
      'kind': kind,
      'castle_id': castle,
      'updated_ts': 1790000000.0,
      ...extra,
    });

void main() {
  setUpAll(_realFont);

  // ONE SHOT PER PROCESS, and the process leaves as soon as it has its file.
  //
  // This is a tool, not a suite, and it cannot be a suite. The map drives
  // itself off a post-frame callback that reschedules itself every frame, so
  // once a map has been mounted the test binding never reaches idle again: it
  // will not end the test, and it will not accept a second `pumpWidget`. Both
  // were established the slow way — every shot rendered correctly and then sat
  // there until the ten-minute timeout killed it.
  //
  // So `screenshots.sh` runs this file three times, naming one shot each, and
  // each run writes its file and exits. Compiling is cached after the first.
  testWidgets('world', (t) async => _leave(await _shotWorld(t)));
  testWidgets('record', (t) async => _leave(await _shotRecord(t)));
  testWidgets('castle', (t) async => _leave(await _shotCastle(t)));
}

/// Written, or not. Never returns.
Never _leave(void _) {
  // ignore: avoid_print
  print(_written == 1 ? 'ok' : 'NOTHING WAS WRITTEN');
  exit(_written == 1 ? 0 : 1);
}

Future<void> _shotWorld(WidgetTester t) async {
    final rooms = _rooms();
    final castles = _castles(rooms);
    final agency = castles.firstWhere((c) => c.pluginId == 'web_agency');
    // One castle's worth, so the rooms are legible rather than a mosaic.
    final mine = rooms.where((r) => r.castleId == agency.id).toList();
    final key = GlobalKey<MapViewState>();
    await _shoot(
      t,
      'world',
      MapView(
        key: key,
        rooms: mine,
        agents: _agents(mine, working: {
          for (final r in mine)
            if (r.baseId == 'factory') r.id: 'building…'
              else if (r.baseId == 'gallery') r.id: 'looking…',
        }),
        badges: {mine[6].id: 2, mine[8].id: 1},
        castles: castles,
        web: const Web(),
        taken: _taken(castles),
        onRoomTapped: (_) {},
      ),
      size: const Size(1360, 1060),
      cropBottom: 420,
      frameOn: key,
      castle: _framing(agency, mine),
    );
}

Future<void> _shotRecord(WidgetTester t) async {
    final rooms = _rooms();
    final castles = _castles(rooms);
    final agency = castles.firstWhere((c) => c.pluginId == 'web_agency');
    final key = GlobalKey<MapViewState>();
    await _shoot(
      t,
      'record',
      Stack(children: [
        Positioned.fill(
          child: MapView(
            key: key,
            rooms: rooms,
            agents: _agents(rooms),
            badges: const {},
            castles: castles,
            web: const Web(),
            taken: _taken(castles),
            onRoomTapped: (_) {},
          ),
        ),
        Positioned.fill(
          child: WindowLayer(
            onClose: (_) {},
            windows: [
              AppWindow(
                id: 'rec',
                title: 'Le Banc d\'Essai',
                subtitle: 'prospect · built',
                icon: Icons.description_outlined,
                initialSize: const Size(540, 560),
                // The real window, so the picture shows its tabs rather than
                // a bare column of blocks.
                child: RecordWindow(api: _FakeApi(_recordView), recordId: 'r'),
              ),
            ],
          ),
        ),
      ]),
      size: const Size(1360, 1000),
      cropBottom: 320,
      frameOn: key,
      castle: _framing(agency, rooms.where((r) => r.castleId == agency.id)
          .toList()),
      // Cards start folded — a record opens on its SHAPE, not on six hundred
      // rows. One open says more in a picture than two headings.
      before: (t) async {
        await t.tap(find.text('THE BUSINESS'));
        await t.pump(const Duration(milliseconds: 60));
      },
    );
}

Future<void> _shotCastle(WidgetTester t) async {
    final rooms = _rooms();
    final castles = _castles(rooms);
    final agency = castles.firstWhere((c) => c.pluginId == 'web_agency');
    final key = GlobalKey<MapViewState>();
    await _shoot(
      t,
      'castle',
      Stack(children: [
        Positioned.fill(
          child: MapView(
            key: key,
            rooms: rooms,
            agents: _agents(rooms),
            badges: const {},
            castles: castles,
            web: const Web(),
            taken: _taken(castles),
            onRoomTapped: (_) {},
          ),
        ),
        Positioned.fill(
          child: WindowLayer(
            onClose: (_) {},
            windows: [
              AppWindow(
                id: 'castle',
                title: agency.name,
                subtitle: 'Web agency',
                icon: Icons.castle_outlined,
                initialSize: const Size(470, 560),
                child: CastlePanel(
                  castle: _withRecordCount(agency, _board(agency.id).length),
                  rooms: rooms,
                  badges: const {},
                  onRaze: () async {},
                  onOpenRoom: (_) {},
                  records: _board(agency.id),
                  stages: const [
                    'sourced', 'qualified', 'enriched', 'appraised',
                    'visualised', 'built', 'qa_passed', 'published',
                    'drafted', 'contacted', 'replied', 'won',
                  ],
                  deadStages: const ['disqualified', 'lost'],
                  onTapRecord: (_) {},
                ),
              ),
            ],
          ),
        ),
      ]),
      size: const Size(1360, 1000),
      cropBottom: 320,
      frameOn: key,
      castle: _framing(agency, rooms.where((r) => r.castleId == agency.id)
          .toList()),
      // Stage groups start folded — that is the design, and a picture of eight
      // headings says less than one of them open.
      before: (t) async {
        await t.tap(find.text('built'));
        await t.pump(const Duration(milliseconds: 60));
      },
  );
}

Castle _withRecordCount(Castle c, int n) => Castle(
      id: c.id, pluginId: c.pluginId, pluginName: c.pluginName, name: c.name,
      ring: c.ring, slot: c.slot, centre: c.centre, span: c.span,
      records: n, installed: c.installed, status: c.status,
      waiting: c.waiting, rooms: c.rooms,
    );

/// Serves one prepared view, the way the server would.
class _FakeApi extends Api {
  _FakeApi(this.view) : super('http://127.0.0.1:1');
  final Map<String, dynamic> view;
  @override
  Future<dynamic> get(String path) async => view;
}

const _recordView = <String, dynamic>{
  'id': 'r',
  'name': 'Le Banc d\'Essai',
  'kind': 'prospect',
  'stage': 'built',
  'blocks': _blocks,
};

/// The castle to aim the camera at: the real one, with two tiles of margin.
///
/// `_flyTo` fits the ROOM BOX, and a room is not all a room draws — its back
/// wall stands above it, its sprites stand on it and its name floats over the
/// middle. Fitting the box exactly clipped the topmost room's worker off the
/// top edge.
Castle _framing(Castle c, List<Room> rooms) {
  const pad = 3.0;
  var minX = double.infinity, minY = double.infinity;
  var maxX = -double.infinity, maxY = -double.infinity;
  for (final r in rooms) {
    minX = math.min(minX, r.position.x);
    minY = math.min(minY, r.position.y);
    maxX = math.max(maxX, r.position.x + r.size.x);
    maxY = math.max(maxY, r.position.y + r.size.y);
  }
  return c.withRooms([
    Room.fromJson({
      'id': '_frame',
      'name': '',
      'castle_id': c.id,
      'position': {'x': minX - pad, 'y': minY - pad},
      'size': {'w': (maxX - minX) + pad * 2, 'h': (maxY - minY) + pad * 2},
      'color': '#000000',
    }),
  ]);
}

/// A plausible board. Every name is made up.
List<WorkRecord> _board(String castle) => [
      _record('Le Banc d\'Essai', 'built', castle),
      _record('Atelier Marceau', 'qualified', castle),
      _record('Pépinière du Vallon', 'published', castle),
      _record('Café des Deux Ponts', 'contacted', castle),
      _record('Garage Saint-Roch', 'sourced', castle),
      _record('Fleurs & Compagnie', 'won', castle),
      _record('Toiture Lavergne', 'needs_review', castle),
      _record('Boucherie Aubanel', 'qa_failed', castle),
    ];

/// What a plugin answers `record_view` with, and what the app draws.
const _blocks = <Map<String, dynamic>>[
  {
    'block': 'fields',
    'title': 'The business',
    'rows': [
      {'label': 'Category', 'value': 'Bistro'},
      {'label': 'Address', 'value': '4 rue des Essais, 30000 Nîmes'},
      {'label': 'Phone', 'value': '04 66 00 00 00'},
      {
        'label': 'Hours',
        'value': 'Tue–Sat 12:00–14:30, 19:00–22:00',
        'source': 'https://example.invalid/hours',
        'conflict': 'one source says 17:00, another 19:00 — call before building',
      },
    ],
  },
  {
    'block': 'table',
    'title': 'Offering',
    'columns': ['Item', 'Price'],
    'rows': [
      {'cells': ['Planche charcuterie', '13 €'], 'source': 'photo'},
      {'cells': ['Viognier Pays d\'Oc', '13 €'], 'source': 'photo'},
      {'cells': ['Formule déjeuner', '19 €'],
       'source': 'https://example.invalid/menu'},
    ],
  },
  {
    'block': 'text',
    'title': 'Design rationale',
    'value': 'Editorial treatment. Terracotta #C0352A read off the back wall '
        'in four photographs, not guessed from "bistro". Screenshotted the '
        'build at 390px and fixed two real defects it caught.',
  },
  {
    'block': 'timeline',
    'title': 'History',
    'steps': [
      {
        'ts': 1790003600.0, 'from_stage': 'visualised', 'stage': 'built',
        'agent': 'forge', 'room': 'factory', 'room_name': 'Factory',
        'note': 'wrote index.html, menu.html, styles.css',
        'wrote': ['site'], 'by_hand': false,
      },
      {
        'ts': 1790002000.0, 'from_stage': 'appraised', 'stage': 'visualised',
        'agent': 'lens', 'room': 'gallery', 'room_name': 'Gallery',
        'note': 'read 12 photographs; no legible signage in any of them',
        'wrote': ['visual'], 'by_hand': false,
      },
      {
        'ts': 1790001000.0, 'from_stage': 'enriched', 'stage': 'appraised',
        'agent': 'probe', 'room': 'assay', 'room_name': 'Assay Room',
        'note': 'register matched; 6 staff', 'wrote': ['appraisal'],
        'by_hand': false,
      },
    ],
  },
];
