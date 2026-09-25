import 'package:flutter/material.dart';

import '../api/client.dart';
import '../model/castle.dart';
import 'settings_tab.dart';

/// What an operator may change about a castle.
///
/// A castle has no colour of its own — its rooms carry theirs — so there is
/// nothing to offer there. What it has is a name, a plot, and the fact that it
/// can be taken down.
class CastleSettings extends StatelessWidget {
  const CastleSettings({
    super.key,
    required this.api,
    required this.castle,
    required this.onChanged,
    required this.onRaze,
  });

  final Api api;
  final Castle castle;
  final VoidCallback onChanged;
  final Future<void> Function() onRaze;

  Future<String> _patch(Map<String, dynamic> body) async {
    try {
      await api.send('PATCH', '/castles/${castle.id}', body);
      onChanged();
      return '';
    } on ApiError catch (e) {
      return e.message;
    } catch (e) {
      return '$e';
    }
  }

  @override
  Widget build(BuildContext context) => ListView(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 20),
        children: [
          SettingsSection(
            title: 'Name',
            note: 'What this instance is called. The plugin is still '
                '${castle.pluginName}; this is which one of them it is.',
            children: [
              EditableField(
                label: 'Name',
                value: castle.name,
                onSubmit: (v) => _patch({'name': v}),
              ),
            ],
          ),
          SettingsSection(
            title: 'Where it stands',
            note: 'Plots sit on rings around the hub, and ring n holds 6n of '
                'them. Moving a castle moves its rooms with it; nothing about '
                'the work changes.',
            children: [
              EditableField(
                label: 'Ring',
                value: '${castle.ring}',
                numeric: true,
                onSubmit: (v) => _patch({'ring': int.tryParse(v) ?? castle.ring}),
              ),
              EditableField(
                label: 'Plot',
                value: '${castle.slot}',
                numeric: true,
                onSubmit: (v) => _patch({'slot': int.tryParse(v) ?? castle.slot}),
              ),
              const SizedBox(height: 2),
              ReadOnlyRow(label: 'Plugin', value: castle.pluginId),
              ReadOnlyRow(label: 'Rooms', value: '${castle.rooms.length}'),
              ReadOnlyRow(label: 'Records', value: '${castle.records}'),
            ],
          ),
          SettingsSection(
            title: 'Raze',
            note: castle.records == 0
                ? 'It holds no records.'
                : 'Its ${castle.records} record(s) are KEPT. They are the work, '
                    'and taking down a place should not delete what was done '
                    'there.',
            children: [
              DangerButton(
                label: 'Raze this castle',
                title: 'Raze ${castle.name}?',
                explain: 'Its rooms leave the map and its workers are '
                    'retired.\n\n'
                    '${castle.records} record(s) survive — they are the work, '
                    'not the building. The plugin stays installed, so you can '
                    'build another instance of it on any free plot.',
                onConfirmed: onRaze,
              ),
            ],
          ),
        ],
      );
}
