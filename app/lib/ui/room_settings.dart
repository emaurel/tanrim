import 'package:flutter/material.dart';

import '../api/client.dart';
import '../model/world.dart';
import 'settings_tab.dart';

/// What an operator may change about a room, on their own machine.
///
/// None of it is written back to the plugin. Which tools a room grants and
/// what its agent is called are the plugin author's decisions; these are one
/// operator's preferences, and they live in `state/` where they belong to
/// nobody's repository.
class RoomSettings extends StatefulWidget {
  const RoomSettings({
    super.key,
    required this.api,
    required this.room,
    required this.onChanged,
    required this.workerLimit,
  });

  final Api api;
  final Room room;
  final VoidCallback onChanged;

  /// From the room's own `/state`, not the `Room` model — the cap is a
  /// runtime setting served alongside how many are busy, and the map's room
  /// list carries a boot snapshot of it that nothing reads for capacity.
  final int workerLimit;

  @override
  State<RoomSettings> createState() => _RoomSettingsState();
}

class _RoomSettingsState extends State<RoomSettings> {
  List<String> _allTools = [];
  List<Map<String, dynamic>> _allSkills = [];
  late Set<String> _tools = widget.room.tools.toSet();
  late Set<String> _skills = widget.room.skills.toSet();
  String _problem = '';

  @override
  void initState() {
    super.initState();
    _loadCatalogue();
  }

  /// Everything installed, so the pickers can offer what is not granted yet.
  /// Tools are free choice: any registered one may go to any room.
  Future<void> _loadCatalogue() async {
    try {
      final d = await widget.api.get('/capabilities') as Map;
      if (!mounted) return;
      setState(() {
        _allTools = [...(d['tools'] as List).map((t) => '$t')];
        _allSkills = [
          for (final s in (d['skills'] as List)) (s as Map).cast<String, dynamic>()
        ];
      });
    } catch (_) {
      // A catalogue that will not load leaves the pickers showing what the
      // room already has, which is still true and still editable downward.
    }
  }

  Future<void> _grant({Set<String>? tools, Set<String>? skills}) async {
    final body = <String, dynamic>{};
    if (tools != null) {
      body['tools'] = tools.toList();
      body['set_tools'] = true;
    }
    if (skills != null) {
      body['skills'] = skills.toList();
      body['set_skills'] = true;
    }
    try {
      final out = await widget.api
          .post('/rooms/${widget.room.id}/grants', body) as Map;
      if (!mounted) return;
      setState(() {
        _tools = {...(out['tools'] as List).map((t) => '$t')};
        _skills = {...(out['skills'] as List).map((s) => '$s')};
        _problem = '';
      });
      widget.onChanged();
    } on ApiError catch (e) {
      if (mounted) setState(() => _problem = e.message);
    } catch (e) {
      if (mounted) setState(() => _problem = '$e');
    }
  }

  String? _describeSkill(String name) {
    for (final s in _allSkills) {
      if (s['name'] == name) return '${s['description'] ?? name}';
    }
    return null;
  }

  @override
  Widget build(BuildContext context) {
    final toolOptions = {..._allTools, ..._tools}.toList()..sort();
    final skillOptions =
        {..._allSkills.map((s) => '${s['name']}'), ..._skills}.toList()..sort();

    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 20),
      children: [
        if (_problem.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: Text(_problem,
                style: const TextStyle(
                    fontSize: 12, color: Color(0xFFE0A458))),
          ),
        SettingsSection(
          title: 'Crew',
          note: 'How many workers this room may run at once. A second record '
              'arriving at a busy room hires another; they retire when their '
              'record finishes. Raising this hires nobody by itself.',
          children: [
            EditableField(
              label: 'Workers',
              value: '${widget.workerLimit}',
              numeric: true,
              onSubmit: (v) async {
                final n = int.tryParse(v);
                if (n == null || n < 1) return 'a whole number, 1 or more';
                try {
                  await widget.api.send('PUT', '/rooms/workers', {
                    'rooms': {widget.room.id: n}
                  });
                  widget.onChanged();
                  return '';
                } on ApiError catch (e) {
                  return e.message;
                }
              },
            ),
          ],
        ),
        SettingsSection(
          title: 'Tools',
          note: 'Free choice: any installed tool may be granted to any room. '
              'That also means a room can be handed something nobody designed '
              'its agent to hold — the agent will see it and may try it.',
          children: [
            ChipPicker(
              options: toolOptions,
              selected: _tools,
              empty: 'no tools installed',
              onChanged: (next) => _grant(tools: next),
            ),
          ],
        ),
        SettingsSection(
          title: 'Skills',
          note: 'A skill missing from disk is dropped silently, so a name here '
              'that the agent never uses usually means it was never fetched.',
          children: [
            ChipPicker(
              options: skillOptions,
              selected: _skills,
              empty: 'no skills installed',
              describe: _describeSkill,
              onChanged: (next) => _grant(skills: next),
            ),
          ],
        ),
        for (final a in widget.room.agents) _agent(a),
        SettingsSection(
          title: 'Not editable here',
          note: 'A workbench declares which stages are worked in this room, '
              'and that IS the routing table — changing it changes the '
              'pipeline, so it belongs in the plugin that declared it rather '
              'than in a settings pane.',
          children: [
            for (final b in widget.room.workbenches)
              ReadOnlyRow(
                  label: b.name,
                  value: b.stages.isEmpty ? '—' : b.stages.join(', ')),
            ReadOnlyRow(label: 'Purpose', value: widget.room.purpose),
            ReadOnlyRow(
                label: 'Position',
                value: '${widget.room.position.x.toInt()}, '
                    '${widget.room.position.y.toInt()}'),
          ],
        ),
      ],
    );
  }

  Widget _agent(AgentSpec a) => SettingsSection(
        title: 'Agent · ${a.id}',
        note: 'What you call it here. The plugin still knows it as its role; '
            'this is the label on the map, and it is per castle — renaming '
            'the builder in one does not rename it in another.',
        children: [
          EditableField(
            label: 'Name',
            value: a.name,
            hint: 'empty restores what the plugin declared',
            onSubmit: (v) => _identity(a.id, {'name': v}),
          ),
          EditableField(
            label: 'Colour',
            value: '#${(a.color & 0xFFFFFF).toRadixString(16).padLeft(6, '0')}',
            hint: '#rrggbb',
            onSubmit: (v) => _identity(a.id, {'color': v}),
          ),
          ReadOnlyRow(label: 'Does', value: a.role),
        ],
      );

  Future<String> _identity(String agentId, Map<String, dynamic> body) async {
    try {
      await widget.api.post('/agents/$agentId/identity', body);
      widget.onChanged();
      return '';
    } on ApiError catch (e) {
      return e.message;
    } catch (e) {
      return '$e';
    }
  }
}
