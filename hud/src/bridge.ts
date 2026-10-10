/** The page's handle on the Qt window (QWebChannel object published as `window.jarvisBridge` by a script the window
 *  injects — see ui/window.py). In a plain browser tab there is no bridge: every call is a harmless no-op and the
 *  caller falls back to doing what it can locally (layout changes only). */

import type { Mode } from "./ws";

type Raw = {
  setMode(mode: string): void;
  setVisible(visible: boolean): void;
  show(): void;
  hide(): void;
  quit(): void;
  setState(state: string): void;
  applyPrefs(prefsJson: string): void;
  beginDrag(): void;
};

function raw(): Raw | null {
  return ((window as unknown as { jarvisBridge?: Raw }).jarvisBridge as Raw | undefined) ?? null;
}

export const bridge = {
  /** True inside the Qt shell (once the channel handshake has completed). */
  get present(): boolean {
    return raw() !== null;
  },
  onReady(cb: () => void): void {
    if (raw()) cb();
    else document.addEventListener("jarvis-bridge", () => cb(), { once: true });
  },
  setMode(mode: Mode): boolean {
    const b = raw();
    if (!b) return false;
    b.setMode(mode);
    return true;
  },
  setVisible(visible: boolean): void {
    raw()?.setVisible(visible);
  },
  show(): void {
    raw()?.show();
  },
  hide(): void {
    raw()?.hide();
  },
  quit(): boolean {
    const b = raw();
    if (!b) return false;
    b.quit();
    return true;
  },
  setState(state: string): void {
    raw()?.setState(state);
  },
  applyPrefs(prefs: unknown): void {
    raw()?.applyPrefs(JSON.stringify(prefs));
  },
  beginDrag(): void {
    raw()?.beginDrag();
  },
};
