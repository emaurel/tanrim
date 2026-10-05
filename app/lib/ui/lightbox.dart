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
                  maxWidth: size.width * 0.94,
                  maxHeight: size.height * (caption.isEmpty ? 0.92 : 0.84),
                ),
                // Sized to the picture's own shape, and panned rather than
                // fitted.
                //
                // A filled application form is 1,280 by 4,228. `BoxFit.contain`
                // fits the HEIGHT of that into a landscape window, so the width
                // collapses to a sliver of illegible grey — and `constrained`
                // kept the zoom inside that sliver, so magnifying it did not
                // help either. A tall picture wants to be read at full width
                // and scrolled, which is how it was read on the site it came
                // from.
                child: _Panned(url: url),
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


/// How big to draw a picture of this shape in a frame of this size.
///
/// A picture TALLER than the frame takes the frame's full width and runs off
/// the bottom, where panning reaches it. Anything else is fitted.
///
/// A filled application form is 1,280 by 4,228. Fitted into a landscape
/// window that means fitting its HEIGHT, so the width collapses to a sliver of
/// illegible grey — and the zoom was trapped inside that sliver, so magnifying
/// it did not help either. Full width and scrolled is how the form was read on
/// the site it came from.
//: Past this much taller than the frame, a picture is better read at full
//: width and scrolled than shrunk to fit. Below it, fitting is still kinder —
//: a square in a landscape window does not want to be scrolled.
const _tallerThanWorthFitting = 1.6;

@visibleForTesting
Size layFor(double aspect, Size box) {
  // Full width first: for anything WIDER than the frame this is the fit, and
  // for anything taller it is the readable choice.
  final atFullWidth = box.width / aspect;
  if (atFullWidth <= box.height) return Size(box.width, atFullWidth);
  if (atFullWidth > box.height * _tallerThanWorthFitting) {
    return Size(box.width, atFullWidth);
  }
  // Only a little taller than the frame — fit it, and no scrolling.
  return Size(box.height * aspect, box.height);
}

/// A picture at its own proportions, pannable and zoomable past the frame.
class _Panned extends StatefulWidget {
  const _Panned({required this.url});

  final String url;

  @override
  State<_Panned> createState() => _PannedState();
}

class _PannedState extends State<_Panned> {
  ImageStreamListener? _listener;
  ImageStream? _stream;
  double? _aspect;
  Object? _failed;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    // The intrinsic size, because the layout depends on it: a wide picture is
    // fitted and a tall one is read at full width, and `BoxFit` alone cannot
    // tell them apart.
    final provider = NetworkImage(widget.url);
    final stream = provider.resolve(createLocalImageConfiguration(context));
    if (stream.key == _stream?.key) return;
    _drop();
    _stream = stream;
    _listener = ImageStreamListener(
      (info, _) {
        if (!mounted) return;
        setState(() => _aspect = info.image.width / info.image.height);
      },
      onError: (e, _) {
        if (mounted) setState(() => _failed = e);
      },
    );
    stream.addListener(_listener!);
  }

  void _drop() {
    if (_stream != null && _listener != null) {
      _stream!.removeListener(_listener!);
    }
    _stream = null;
    _listener = null;
  }

  @override
  void dispose() {
    _drop();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (_failed != null) {
      return const Padding(
        padding: EdgeInsets.all(40),
        child: Text('that picture is not there any more',
            style: TextStyle(color: Colors.white54)),
      );
    }
    final aspect = _aspect;
    if (aspect == null) {
      return const Padding(
        padding: EdgeInsets.all(60),
        child: CircularProgressIndicator(strokeWidth: 2),
      );
    }
    return LayoutBuilder(builder: (_, box) {
      final laid = layFor(aspect, Size(box.maxWidth, box.maxHeight));
      return InteractiveViewer(
        // Unconstrained, so a zoom is not trapped inside the frame the
        // picture was fitted into.
        constrained: false,
        minScale: 0.15,
        maxScale: 8,
        boundaryMargin: const EdgeInsets.all(2000),
        child: SizedBox(
          width: laid.width,
          height: laid.height,
          child: Image.network(widget.url, fit: BoxFit.fill),
        ),
      );
    });
  }
}
