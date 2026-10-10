/** Animated switch between layout modes (compact ⇄ panel ⇄ cinematic).
 *
 *  The reactor is the anchor: it never disappears, it *flies* from where it is to where the new layout puts it (FLIP:
 *  measure First, apply the new layout, measure Last, play the Inverse transform back to identity). Everything else
 *  leaves first (shutter-wipe, 240 ms), then re-enters in the new layout one piece after another (staggered 60 ms,
 *  480 ms each). Total ≈ 1.2 s; the Qt shell waits ANIM_MS = 1250 ms before shrinking the window (ui/window.py).
 *
 *  The window itself has already grown to the full screen when this runs, with the old layout anchored to the same
 *  corner (style.css: --dock-x/--dock-y), so the "First" rectangle is exactly where the user last saw the reactor. */

import type { Mode } from "./ws";

const OUT_MS = 240;
const FLIP_MS = 620;
const IN_MS = 480;
const STAGGER_MS = 60;
const IN_DELAY_MS = 160;
const EASE = "cubic-bezier(.2,.8,.2,1)";

let running: Promise<void> | null = null;

export type SwitchOptions = {
  /** Elements that are laid out but meant to be invisible in the target layout (e.g. the folded-away conversation
   *  of a resting compact HUD) — they are left alone instead of being revealed. */
  skip?: (el: HTMLElement) => boolean;
};

/** Elements (other than the reactor) that are currently laid out, in DOM order. */
function parts(skip?: (el: HTMLElement) => boolean): HTMLElement[] {
  return Array.from(document.querySelectorAll<HTMLElement>("#head, .tile, #convo, #foot")).filter((el) => el.getClientRects().length > 0 && !skip?.(el));
}

function wipeOut(el: HTMLElement): Animation {
  return el.animate(
    [
      { opacity: 1, clipPath: "inset(0 0 0 0)" },
      { opacity: 0, clipPath: "inset(0 0 100% 0)" },
    ],
    { duration: OUT_MS, easing: "ease-in", fill: "forwards" },
  );
}

function wipeIn(el: HTMLElement, delay: number): Animation {
  return el.animate(
    [
      { opacity: 0, clipPath: "inset(0 100% 0 0)" },
      { opacity: 1, clipPath: "inset(0 -2% 0 0)" },
    ],
    { duration: IN_MS, delay, easing: EASE, fill: "backwards" },
  );
}

/**
 * @param to      target mode
 * @param apply   applies the target layout synchronously (body classes, tile placement)
 */
export function switchMode(to: Mode, apply: (mode: Mode) => void, opts: SwitchOptions = {}): Promise<void> {
  const run = async (): Promise<void> => {
    const body = document.body;
    const reactor = document.getElementById("reactor")!;
    body.classList.add("switching");
    try {
      // 1. everything but the reactor leaves, in the current layout
      const leaving = parts(opts.skip);
      const outs = leaving.map(wipeOut);
      await Promise.all(outs.map((a) => a.finished.catch(() => undefined)));

      // 2. FLIP the reactor into the new layout
      const first = reactor.getBoundingClientRect();
      apply(to);
      outs.forEach((a) => a.cancel()); // release the forward-fill so the elements can be shown again
      const last = reactor.getBoundingClientRect();
      const dx = first.left - last.left;
      const dy = first.top - last.top;
      const s = last.width > 0 ? first.width / last.width : 1;
      const flip = reactor.animate([{ transform: `translate(${dx}px, ${dy}px) scale(${s})` }, { transform: "none" }], { duration: FLIP_MS, easing: EASE });

      // 3. the new layout's pieces unfold one after another while the reactor is still travelling
      const entering = parts(opts.skip);
      const ins = entering.map((el, i) => wipeIn(el, IN_DELAY_MS + i * STAGGER_MS));
      await flip.finished.catch(() => undefined);
      await Promise.all(ins.map((a) => a.finished.catch(() => undefined)));
    } finally {
      body.classList.remove("switching");
    }
  };
  // serialise: a second switch waits for the first to finish rather than fighting it
  const next = (running ?? Promise.resolve()).then(run, run);
  running = next.finally(() => {
    if (running === next) running = null;
  });
  return running;
}

/** Quick reveal used when the drawer or a group of tiles appears without a mode change. */
export function reveal(els: HTMLElement[]): void {
  els.forEach((el, i) => wipeIn(el, i * 40));
}
