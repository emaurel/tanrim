/// The agent figure, as pixel rows.
///
/// Copied from the web build's `art.ts` rather than reinvented: these are the
/// characters you already recognise, and a redraw would make every sprite
/// subtly different for no gain. Each character in a row is a palette slot,
/// each row is one pixel tall, and the whole figure is 12 wide.
///
///   h hood · f face · e eye · b body · t trim · s sleeve · k emblem
///   space and '.' draw nothing
library;

const bodyRows = <String>[
  '....hhhh....',
  '...hhhhhh...',
  '..hhffffhh..',
  '..hffeffehf.',
  '..hffffffhf.',
  '...ffffff...',
  '....tttt....',
  '..sbbbbbbs..',
  '.sbbbkkbbbs.',
  '.sbbbkkbbbs.',
  '.sbbbbbbbbs.',
  '..bbbbbbbb..',
  '..bbbbbbbb..',
  '..tbbbbbbt..',
];

const legsStand = <String>[
  '...tt..tt...',
  '...tt..tt...',
  '..ttt..ttt..',
];

const legsStep = <String>[
  '...tt..tt...',
  '..ttt...tt..',
  '.ttt....ttt.',
];

/// Hands down at a bench, and hands up mid-gesture. The web build uses these
/// for an agent standing at a workbench, which is most of the time it is busy.
const workDownRows = <String>[
  '....hhhh....',
  '...hhhhhh...',
  '..hhffffhh..',
  '..hffeffehf.',
  '..hffffffhf.',
  '...ffffff...',
  '....tttt....',
  '..bbbbbbbb..',
  '..bbbkkbbb..',
  '.sbbbkkbbbs.',
  '.sbbbbbbbbs.',
  '.sbbbbbbbbs.',
  '..bbbbbbbb..',
  '..tbbbbbbt..',
];

const workUpRows = <String>[
  '....hhhh....',
  '...hhhhhh...',
  '..hhffffhh..',
  '..hffeffehf.',
  '..hffffffhf.',
  '...ffffff...',
  's...tttt...s',
  's.bbbbbbbb.s',
  '.sbbbkkbbbs.',
  '..bbbkkbbb..',
  '..bbbbbbbb..',
  '..bbbbbbbb..',
  '..bbbbbbbb..',
  '..tbbbbbbt..',
];

/// The four poses, matching the web build's frame order.
enum Pose { stand, step, workDown, workUp }

List<String> posture(Pose p) => switch (p) {
      Pose.stand => [...bodyRows, ...legsStand],
      Pose.step => [...bodyRows, ...legsStep],
      Pose.workDown => [...workDownRows, ...legsStand],
      Pose.workUp => [...workUpRows, ...legsStand],
    };

const spriteWidth = 12;
