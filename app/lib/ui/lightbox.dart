import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

/// One picture, as big as the window will allow.
///
/// A record's images are thumbnails in a 130px column, which is enough to know
/// a photograph is there and not enough to judge it — and judging it is the
/// whole reason a build carries the client's photographs at all.
Future<void> showPicture(BuildContext context, String url,
    {String caption = ''}) {
  return showDialog<void>(
    context: context,
    // Its own barrier, dark enough that the picture is the only thing lit.
    barrierColor: Colors.black.withValues(alpha: .86),
    barrierDismissible: true,
    builder: (_) => _Picture(url: url, caption: caption),
  );
}

class _Picture extends StatelessWidget {
  const _Picture({required this.url, required this.caption});

  final String url;
  final String caption;

  @override
  Widget build(BuildContext context) {
    final size = MediaQuery.of(context).size;
    return Focus(
      autofocus: true,
      // Escape closes it, because a picture opened by accident should not
      // need the mouse to get out of.
      onKeyEvent: (_, e) {
        if (e is KeyDownEvent && e.logicalKey == LogicalKeyboardKey.escape) {
          Navigator.of(context).maybePop();
          return KeyEventResult.handled;
        }
        return KeyEventResult.ignored;
      },
      child: Stack(children: [
        // Anywhere off the picture closes it too.
        Positioned.fill(
          child: GestureDetector(
            behavior: HitTestBehavior.opaque,
            onTap: () => Navigator.of(context).maybePop(),
          ),
        ),
        Center(
          child: GestureDetector(
            // Swallowed, so a click ON the picture does not close it.
            onTap: () {},
            child: Column(mainAxisSize: MainAxisSize.min, children: [
              ConstrainedBox(
                constraints: BoxConstraints(
                  maxWidth: size.width * 0.9,
                  maxHeight: size.height * (caption.isEmpty ? 0.9 : 0.82),
                ),
                child: InteractiveViewer(
                  maxScale: 6,
                  child: Image.network(
                    url,
                    fit: BoxFit.contain,
                    errorBuilder: (_, _, _) => const Padding(
                      padding: EdgeInsets.all(40),
                      child: Text('that picture is not there any more',
                          style: TextStyle(color: Colors.white54)),
                    ),
                    loadingBuilder: (_, child, progress) => progress == null
                        ? child
                        : const Padding(
                            padding: EdgeInsets.all(60),
                            child: CircularProgressIndicator(strokeWidth: 2),
                          ),
                  ),
                ),
              ),
              if (caption.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 12),
                  child: SizedBox(
                    width: size.width * 0.6,
                    child: Text(caption,
                        textAlign: TextAlign.center,
                        style: const TextStyle(
                            fontSize: 12.5, height: 1.4,
                            color: Colors.white70)),
                  ),
                ),
            ]),
          ),
        ),
        // The cross, top right, over everything.
        Positioned(
          top: 12,
          right: 16,
          child: IconButton(
            icon: const Icon(Icons.close_rounded),
            iconSize: 26,
            color: Colors.white70,
            tooltip: 'close',
            style: IconButton.styleFrom(
              backgroundColor: Colors.black.withValues(alpha: .45),
            ),
            onPressed: () => Navigator.of(context).maybePop(),
          ),
        ),
      ]),
    );
  }
}
