import 'package:flutter/material.dart';

/// One tab in a [MenuPanel].
///
/// A record rather than a widget subclass so a tab is cheap to declare: a
/// title, an icon, and a builder for its body. Adding one is a line in a
/// list.
class MenuTab {
  const MenuTab({
    required this.id,
    required this.title,
    required this.icon,
    required this.build,
    this.badge,
  });

  /// Stable, so the panel can remember which tab was open across rebuilds.
  final String id;
  final String title;
  final IconData icon;
  final WidgetBuilder build;

  /// A count drawn beside the title. Null or zero draws nothing.
  final int? badge;
}

/// A tabbed panel, shown as a sheet over the app.
///
/// This exists as its own class because it will not be the only one: the
/// settings menu is the first of several (plugins, castles, a server picker),
/// and they should all look and behave the same. A new menu is a title and a
/// list of [MenuTab]s — no layout, no state handling, no close button to
/// remember.
///
///     MenuPanel.show(context, title: 'Settings', tabs: [...]);
class MenuPanel extends StatefulWidget {
  const MenuPanel({
    super.key,
    required this.title,
    required this.tabs,
    this.initialTab,
    this.width = 720,
  });

  final String title;
  final List<MenuTab> tabs;
  final String? initialTab;
  final double width;

  /// Open it. Returns when it is dismissed.
  static Future<void> show(
    BuildContext context, {
    required String title,
    required List<MenuTab> tabs,
    String? initialTab,
    double width = 720,
  }) {
    return showGeneralDialog<void>(
      context: context,
      barrierDismissible: true,
      barrierLabel: title,
      barrierColor: Colors.black54,
      transitionDuration: const Duration(milliseconds: 140),
      pageBuilder: (_, _, _) => MenuPanel(
        title: title,
        tabs: tabs,
        initialTab: initialTab,
        width: width,
      ),
      transitionBuilder: (_, anim, _, child) => FadeTransition(
        opacity: anim,
        child: ScaleTransition(
          scale: Tween(begin: 0.98, end: 1.0).animate(
              CurvedAnimation(parent: anim, curve: Curves.easeOut)),
          child: child,
        ),
      ),
    );
  }

  @override
  State<MenuPanel> createState() => _MenuPanelState();
}

class _MenuPanelState extends State<MenuPanel> {
  late String _open = widget.initialTab ?? widget.tabs.first.id;

  @override
  Widget build(BuildContext context) {
    final tab = widget.tabs.firstWhere((t) => t.id == _open,
        orElse: () => widget.tabs.first);
    final short = MediaQuery.of(context).size.width < 700;

    return Center(
      child: Material(
        color: const Color(0xFF161922),
        borderRadius: BorderRadius.circular(10),
        clipBehavior: Clip.antiAlias,
        child: ConstrainedBox(
          constraints: BoxConstraints(
            maxWidth: widget.width,
            maxHeight: MediaQuery.of(context).size.height * 0.86,
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              _header(context),
              const Divider(height: 1, color: Colors.white12),
              Flexible(
                child: short
                    // On a narrow window the rail costs more than it gives,
                    // so the tabs go across the top instead.
                    ? Column(children: [
                        _strip(),
                        const Divider(height: 1, color: Colors.white12),
                        Flexible(child: _body(tab)),
                      ])
                    : Row(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          _rail(),
                          const VerticalDivider(
                              width: 1, color: Colors.white12),
                          Expanded(child: _body(tab)),
                        ],
                      ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _header(BuildContext context) => Padding(
        padding: const EdgeInsets.fromLTRB(20, 14, 8, 14),
        child: Row(children: [
          Text(widget.title,
              style: const TextStyle(
                  fontSize: 17, fontWeight: FontWeight.w700)),
          const Spacer(),
          IconButton(
            onPressed: () => Navigator.of(context).pop(),
            icon: const Icon(Icons.close, size: 20),
          ),
        ]),
      );

  Widget _body(MenuTab tab) => Padding(
        padding: const EdgeInsets.all(20),
        child: SingleChildScrollView(child: Builder(builder: tab.build)),
      );

  Widget _rail() => SizedBox(
        width: 186,
        child: ListView(
          padding: const EdgeInsets.symmetric(vertical: 8),
          children: [for (final t in widget.tabs) _railItem(t)],
        ),
      );

  Widget _railItem(MenuTab t) {
    final on = t.id == _open;
    return InkWell(
      onTap: () => setState(() => _open = t.id),
      child: Container(
        color: on ? Colors.white10 : Colors.transparent,
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 11),
        child: Row(children: [
          Icon(t.icon, size: 17, color: on ? Colors.white : Colors.white54),
          const SizedBox(width: 11),
          Expanded(
            child: Text(t.title,
                style: TextStyle(
                    fontSize: 13.5,
                    color: on ? Colors.white : Colors.white70)),
          ),
          if ((t.badge ?? 0) > 0) _badge(t.badge!),
        ]),
      ),
    );
  }

  Widget _strip() => SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: Row(children: [
          for (final t in widget.tabs)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 6),
              child: TextButton.icon(
                onPressed: () => setState(() => _open = t.id),
                icon: Icon(t.icon, size: 16),
                label: Text(t.title),
                style: TextButton.styleFrom(
                  backgroundColor:
                      t.id == _open ? Colors.white10 : Colors.transparent,
                  foregroundColor:
                      t.id == _open ? Colors.white : Colors.white54,
                ),
              ),
            ),
        ]),
      );

  Widget _badge(int n) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
        decoration: BoxDecoration(
          color: const Color(0xFFE23D3D),
          borderRadius: BorderRadius.circular(9),
        ),
        child: Text('$n',
            style: const TextStyle(
                fontSize: 10.5,
                color: Colors.white,
                fontWeight: FontWeight.w700)),
      );
}

/// A labelled block inside a menu tab, so tabs look alike without each one
/// rebuilding the same layout.
class MenuSection extends StatelessWidget {
  const MenuSection(
      {super.key, required this.title, required this.children, this.note});

  final String title;
  final String? note;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 22),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(title.toUpperCase(),
              style: const TextStyle(
                  fontSize: 10.5,
                  letterSpacing: 1.3,
                  color: Colors.white38,
                  fontWeight: FontWeight.w700)),
          if (note != null) ...[
            const SizedBox(height: 5),
            Text(note!,
                style: const TextStyle(
                    fontSize: 12, color: Colors.white38, height: 1.4)),
          ],
          const SizedBox(height: 10),
          ...children,
        ],
      ),
    );
  }
}
