import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

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

Future<void> _openPlugins(
  WidgetTester t, {
  Future<String> Function()? onReload,
  List<Map<String, dynamic>>? plugins,
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
  );

  await t.pumpWidget(MaterialApp(
    home: Builder(
      builder: (context) => Scaffold(
        body: Center(
          child: ElevatedButton(
            onPressed: () => settings.open(context, tab: 'plugins'),
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
}
