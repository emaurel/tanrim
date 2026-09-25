import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:tanrim/server/process.dart';
import 'package:tanrim/ui/settings.dart';

/// Shaped like what `/plugins` really returns.
final _plugins = <Map<String, dynamic>>[
  {
    'id': 'web_agency',
    'name': 'Web agency',
    'description': 'Builds a site on spec and pitches the owner.',
    'requires': <String>[],
    'rooms': ['throne', 'assay', 'factory'],
    'patches': <String>[],
    'agent_patches': <String>[],
    'pipelines': ['prospect'],
    'tools': ['site_audit'],
    'gates': ['publish_site'],
  },
  {
    'id': 'website_recreation',
    'name': 'Website recreation',
    'description': 'Porting a site someone already has.',
    'requires': ['web_agency'],
    'rooms': <String>[],
    'patches': ['assay', 'gallery'],
    'agent_patches': ['probe'],
    'pipelines': ['port'],
    'tools': <String>[],
    'gates': <String>[],
  },
  {
    'id': 'job_hunt',
    'name': 'Job hunt',
    'description': 'Finds jobs worth applying to.',
    'requires': <String>[],
    'rooms': ['board', 'screening'],
    'patches': <String>[],
    'agent_patches': <String>[],
    'pipelines': ['application'],
    'tools': <String>[],
    'gates': ['send_application'],
  },
];

/// The catalog rows implied by a set of running plugins.
///
/// The cards are built from what is ON DISK, not from what is running — a
/// disabled plugin has to be visible to be switched back on — so a test about
/// the running detail still needs a directory for each one to hang off.
List<Map<String, dynamic>> _catalogFor(List<Map<String, dynamic>> plugins) => [
      for (final p in plugins)
        {
          'id': p['id'],
          'enabled': true,
          'running': true,
          'reason': '',
          'git': '',
          'unrecoverable': <String>[],
        }
    ];

Future<void> _openPlugins(
  WidgetTester t, {
  Future<String> Function()? onReload,
  List<Map<String, dynamic>>? plugins,
  List<Map<String, dynamic>>? catalog,
  Future<String> Function(String id, bool enabled)? onSetEnabled,
  Future<String> Function(String id, bool force)? onRemove,
  Future<String> Function(String source)? onInstall,
  Future<(List<Map<String, dynamic>>, List<Map<String, dynamic>>)> Function()?
      onRefreshPlugins,
  String tab = 'plugins',
}) async {
  t.view
    ..physicalSize = const Size(1200, 900)
    ..devicePixelRatio = 1.0;
  addTearDown(t.view.reset);

  final settings = Settings(
    server: TextEditingController(text: 'http://127.0.0.1:8765'),
    onConnect: () {},
    connected: true,
    status: '17 rooms',
    rooms: 17,
    plugins: plugins ?? _plugins,
    stages: const ['spotted', 'screened'],
    kinds: const ['prospect', 'application'],
    onReload: onReload ?? () async => 'nothing happened',
    repo: TextEditingController(text: '/nowhere'),
    // A port nothing is on, so `refresh` fails fast with connection refused.
    process: ServerProcess(repo: '/nowhere', port: 59997),
    onStartServer: () async => 'not in this test',
    onStopServer: () async => 'not in this test',
    catalog: catalog ?? _catalogFor(plugins ?? _plugins),
    onInstall: onInstall ?? (_) async => 'not in this test',
    onSetEnabled: onSetEnabled ?? (_, _) async => 'ok',
    onRemove: onRemove ?? (_, _) async => 'deleted',
    onRefreshPlugins: onRefreshPlugins ??
        () async =>
            (catalog ?? _catalogFor(plugins ?? _plugins), plugins ?? _plugins),
  );

  await t.pumpWidget(MaterialApp(
    home: Builder(
      builder: (context) => Scaffold(
        body: Center(
          child: ElevatedButton(
            onPressed: () => settings.open(context, tab: tab),
            child: const Text('open'),
          ),
        ),
      ),
    ),
  ));
  await t.tap(find.text('open'));
  await t.pumpAndSettle();
}

void main() {
  testWidgets('an extension is drawn inside what it extends', (t) async {
    // Flat, the list said "3 plugins" while the map showed two castles, and
    // nothing explained the difference. `requires` is the edge, and it is the
    // same declaration that orders the boot.
    await _openPlugins(t);

    expect(find.text('Web agency'), findsOneWidget);
    expect(find.text('Website recreation'), findsOneWidget);
    expect(find.text('Job hunt'), findsOneWidget);

    // The extension is indented, and says it has no castle.
    expect(find.text('no castle of its own'), findsOneWidget);
    expect(find.byIcon(Icons.subdirectory_arrow_right), findsOneWidget);

    final parent = t.getTopLeft(find.text('Web agency')).dx;
    final child = t.getTopLeft(find.text('Website recreation')).dx;
    final sibling = t.getTopLeft(find.text('Job hunt')).dx;
    expect(child, greaterThan(parent), reason: 'the extension should indent');
    expect(sibling, parent, reason: 'a plugin of its own is not indented');
  });

  testWidgets('it says which rooms and agents an extension patches', (t) async {
    await _openPlugins(t);
    expect(find.text('rooms: assay, gallery'), findsOneWidget);
    expect(find.text('agents: probe'), findsOneWidget);
  });

  testWidgets('an extension of something not installed stands alone',
      (t) async {
    // Otherwise it would vanish: drawn under a parent that is not in the list
    // means drawn nowhere, and a plugin you cannot see is worse than one in
    // the wrong place.
    await _openPlugins(t, plugins: [
      {
        'id': 'orphan',
        'name': 'Orphan',
        'requires': ['not_installed'],
        'rooms': <String>[],
        'patches': ['somewhere'],
      }
    ]);
    expect(find.text('Orphan'), findsOneWidget);
  });

  testWidgets('reloading shows what the server said', (t) async {
    await _openPlugins(t, onReload: () async => 'Installed: job_hunt\n17 rooms now.');

    // Below the fold — the installed list is above it.
    await t.ensureVisible(find.text('Reload plugins'));
    await t.pumpAndSettle();
    await t.tap(find.text('Reload plugins'));
    await t.pumpAndSettle();
    expect(find.textContaining('Installed: job_hunt'), findsOneWidget);
  });

  testWidgets('a refusal is shown, not swallowed', (t) async {
    // Declining to reload while an agent is running is a normal outcome and
    // the operator has to be able to read it; a button that silently did
    // nothing would be worse than no button.
    await _openPlugins(t,
        onReload: () async => '2 agent run(s) in flight\n\nWait for them.');

    await t.ensureVisible(find.text('Reload plugins'));
    await t.pumpAndSettle();
    await t.tap(find.text('Reload plugins'));
    await t.pumpAndSettle();
    expect(find.textContaining('in flight'), findsOneWidget);
  });

  testWidgets('a disabled plugin is still listed, with why', (t) async {
    // Something you cannot see is something you cannot switch back on.
    await _openPlugins(t, plugins: const [], catalog: [
      {
        'id': 'job_hunt',
        'enabled': false,
        'running': false,
        'reason': 'misbehaving',
        'git': '',
        'unrecoverable': ['plugins/job_hunt/prompts/'],
      },
    ]);
    await t.ensureVisible(find.text('job_hunt'));
    await t.pumpAndSettle();
    expect(find.text('job_hunt'), findsOneWidget);
    // It says it is off AND why, on the card itself.
    expect(find.textContaining('switched off'), findsOneWidget);
    expect(find.textContaining('misbehaving'), findsOneWidget);
    expect(t.widget<Switch>(find.byType(Switch)).value, isFalse);
  });

  testWidgets('the switch turns a plugin on', (t) async {
    final calls = <(String, bool)>[];
    await _openPlugins(
      t,
      plugins: const [],
      catalog: [
        {'id': 'job_hunt', 'enabled': false, 'running': false,
         'reason': '', 'git': '', 'unrecoverable': <String>[]},
      ],
      onSetEnabled: (id, on) async {
        calls.add((id, on));
        return 'enabled';
      },
    );
    await t.ensureVisible(find.byType(Switch));
    await t.pumpAndSettle();
    await t.tap(find.byType(Switch));
    await t.pumpAndSettle();
    expect(calls, [('job_hunt', true)]);
  });

  testWidgets('deleting names what would be lost, and can be cancelled',
      (t) async {
    // The one thing here that cannot be undone. A plugin's prompts are
    // gitignored on purpose, so they exist on exactly one machine, and the
    // directory gives no sign of it.
    var removed = 0;
    await _openPlugins(
      t,
      plugins: const [],
      catalog: [
        {'id': 'web_agency', 'enabled': true, 'running': true,
         'reason': '', 'git': '',
         'unrecoverable': ['plugins/web_agency/prompts/']},
      ],
      onRemove: (_, _) async {
        removed++;
        return 'deleted';
      },
    );
    await t.ensureVisible(find.byIcon(Icons.delete_outline));
    await t.pumpAndSettle();
    await t.tap(find.byIcon(Icons.delete_outline));
    await t.pumpAndSettle();

    expect(find.text('Delete web_agency?'), findsOneWidget);
    expect(find.textContaining('only copy'), findsOneWidget);
    expect(find.textContaining('prompts/'), findsOneWidget);
    // It says "Delete anyway", not "Delete": the wording is the warning.
    expect(find.text('Delete anyway'), findsOneWidget);

    await t.tap(find.text('Cancel'));
    await t.pumpAndSettle();
    expect(removed, 0, reason: 'cancelling must not delete anything');
  });

  testWidgets('cloning a plugin reports what the server said', (t) async {
    await _openPlugins(t,
        onInstall: (src) async => 'Installed tanrim_job_hunt.');

    await t.ensureVisible(find.text('Clone'));
    await t.pumpAndSettle();
    await t.enterText(find.byType(TextField).last,
        'git@github.com:you/tanrim-job-hunt.git');
    await t.tap(find.text('Clone'));
    await t.pumpAndSettle();
    expect(find.textContaining('Installed tanrim_job_hunt'), findsOneWidget);
  });

  testWidgets('the server panel will not offer Stop for one it did not start',
      (t) async {
    // Stop would look like it worked while taking down something else, with
    // its own flags and its own log.
    await _openPlugins(t, tab: 'connection');
    await t.pumpAndSettle();

    expect(find.text('not running'), findsOneWidget);
    final stop = t.widget<OutlinedButton>(
        find.widgetWithText(OutlinedButton, 'Stop'));
    expect(stop.onPressed, isNull);
    // And it says what is wrong with the checkout rather than just failing.
    expect(find.textContaining('no directory at /nowhere'), findsOneWidget);
  });

  testWidgets('the switch shows the new state, not the old one', (t) async {
    // The bug this is here for: the panel is a modal built from a SNAPSHOT.
    // Throwing the switch changed the server and the app's own lists, and the
    // open panel went on drawing the switch where it had been — so the one
    // control whose whole job is to show a state showed the opposite of what
    // had just happened.
    var enabled = false;
    List<Map<String, dynamic>> catalogNow() => [
          {'id': 'job_hunt', 'enabled': enabled, 'running': enabled,
           'reason': '', 'git': '', 'unrecoverable': <String>[]},
        ];

    await _openPlugins(
      t,
      plugins: const [],
      catalog: catalogNow(),
      onSetEnabled: (_, on) async {
        enabled = on;
        return 'ok';
      },
      onRefreshPlugins: () async => (catalogNow(), <Map<String, dynamic>>[]),
    );

    await t.ensureVisible(find.byType(Switch));
    await t.pumpAndSettle();
    expect(t.widget<Switch>(find.byType(Switch)).value, isFalse);

    await t.tap(find.byType(Switch));
    await t.pumpAndSettle();

    expect(t.widget<Switch>(find.byType(Switch)).value, isTrue,
        reason: 'the panel must refetch and redraw, not keep its snapshot');
    // And nothing writes "enabled" underneath it: the switch says it.
    expect(find.text('enabled'), findsNothing);
    expect(find.text('disabled'), findsNothing);
  });
}
