/**
 * Alerting for things that need the operator.
 *
 * The point of the approval gates is that you can go and do something else
 * while agents work — which only holds if the app can reach you when it needs
 * a decision. Three escalating signals, none of which require the tab to be
 * focused:
 *
 *   1. a short chime, synthesised with WebAudio (no asset, no network)
 *   2. the tab title, so a background tab shows the count
 *   3. a desktop notification, if you've granted permission
 *
 * Browsers refuse to play audio until the user has interacted with the page, so
 * the AudioContext is created lazily on the first real gesture.
 */

const MUTE_KEY = "agent_env.muted";
const DESKTOP_KEY = "agent_env.desktop";

let ctx: AudioContext | null = null;
let unlocked = false;
let baseTitle = document.title;

function stored(key: string, fallback: boolean): boolean {
  try {
    const v = localStorage.getItem(key);
    return v === null ? fallback : v === "1";
  } catch {
    return fallback; // private mode / blocked storage
  }
}

function store(key: string, value: boolean): void {
  try {
    localStorage.setItem(key, value ? "1" : "0");
  } catch {
    /* nothing we can do, and nothing that matters */
  }
}

export function isMuted(): boolean {
  return stored(MUTE_KEY, false);
}

export function setMuted(muted: boolean): void {
  store(MUTE_KEY, muted);
}

export function desktopEnabled(): boolean {
  return stored(DESKTOP_KEY, false);
}

/** Called from the first click/keypress so the AudioContext is allowed to exist. */
function unlock(): void {
  if (unlocked) return;
  unlocked = true;
  try {
    const Ctor =
      window.AudioContext ??
      (window as any).webkitAudioContext;
    if (Ctor) ctx = new Ctor();
    ctx?.resume?.();
  } catch {
    ctx = null; // audio unavailable; the title badge still works
  }
}

export function installUnlockHandlers(): void {
  const once = { once: true, capture: true } as AddEventListenerOptions;
  window.addEventListener("pointerdown", unlock, once);
  window.addEventListener("keydown", unlock, once);
}

/**
 * A two-note chime. Deliberately short and quiet: this fires whenever an agent
 * needs you, which over a working session is often enough that anything
 * louder would become something you'd want to turn off.
 */
function chime(): void {
  if (!ctx) return;
  const now = ctx.currentTime;
  const notes: [number, number][] = [
    [784, 0],     // G5
    [1047, 0.11], // C6
  ];
  for (const [freq, offset] of notes) {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.value = freq;
    // Quick attack, exponential tail — a bell, not a beep.
    const start = now + offset;
    gain.gain.setValueAtTime(0.0001, start);
    gain.gain.exponentialRampToValueAtTime(0.16, start + 0.012);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.36);
    osc.connect(gain).connect(ctx.destination);
    osc.start(start);
    osc.stop(start + 0.4);
  }
}

function setTitleBadge(count: number): void {
  // Strip any badge we previously added before measuring the real title.
  baseTitle = baseTitle.replace(/^\(\d+\)\s*/, "");
  document.title = count > 0 ? `(${count}) ${baseTitle}` : baseTitle;
}

function desktopNotify(title: string, body: string): void {
  if (!desktopEnabled()) return;
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  // Only worth interrupting the desktop when the page isn't being watched.
  if (document.visibilityState === "visible") return;
  try {
    new Notification(title, { body, tag: "agent-env-approval" });
  } catch {
    /* some platforms refuse constructor notifications; not worth handling */
  }
}

export interface AlertInput {
  /** Total pending approvals right now. */
  total: number;
  /** Rooms that gained approvals since the last check, prettified for display. */
  newRooms: string[];
}

/** Signal that something new needs the operator. */
export function alertOperator({ total, newRooms }: AlertInput): void {
  setTitleBadge(total);
  if (!newRooms.length) return;
  if (!isMuted()) chime();
  const where = newRooms.join(", ");
  desktopNotify(
    "An agent needs you",
    `${where} ${newRooms.length === 1 ? "is" : "are"} waiting on a decision.`,
  );
}

/** Keep the title badge accurate without alerting (e.g. after you resolve one). */
export function updateBadge(total: number): void {
  setTitleBadge(total);
}

/** The control in the crew panel header. */
export function buildToggle(): HTMLElement {
  const wrap = document.createElement("div");
  wrap.className = "crew-alerts";

  const sound = document.createElement("button");
  sound.type = "button";
  sound.className = "crew-alert-btn";
  const paint = () => {
    const muted = isMuted();
    sound.textContent = muted ? "🔇" : "🔔";
    sound.title = muted
      ? "Approval alerts are muted — click to unmute"
      : "Chime when an agent needs a decision — click to mute";
    sound.setAttribute("aria-pressed", String(!muted));
  };
  sound.addEventListener("click", () => {
    setMuted(!isMuted());
    paint();
    if (!isMuted()) chime(); // confirm it works, and that you'll hear it
  });
  paint();
  wrap.appendChild(sound);

  const desktop = document.createElement("button");
  desktop.type = "button";
  desktop.className = "crew-alert-btn";
  const paintDesktop = () => {
    const on = desktopEnabled() && Notification?.permission === "granted";
    desktop.textContent = on ? "🖥" : "🖥";
    desktop.classList.toggle("crew-alert-btn--off", !on);
    desktop.title = on
      ? "Desktop notifications on when this tab is in the background — click to turn off"
      : "Also send a desktop notification when this tab is in the background";
  };
  desktop.addEventListener("click", async () => {
    if (desktopEnabled()) {
      store(DESKTOP_KEY, false);
      paintDesktop();
      return;
    }
    if (!("Notification" in window)) {
      desktop.title = "This browser has no notification support";
      return;
    }
    const perm =
      Notification.permission === "granted"
        ? "granted"
        : await Notification.requestPermission();
    store(DESKTOP_KEY, perm === "granted");
    paintDesktop();
  });
  if (!("Notification" in window)) desktop.style.display = "none";
  paintDesktop();
  wrap.appendChild(desktop);

  return wrap;
}
