"""PySide6 shell: a frameless, transparent, always-on-top QWebEngineView showing the HUD.

Why a separate shell instead of a browser tab? The HUD should float over whatever you are doing (like the movie),
with no chrome, see-through background, a tray icon and global hotkeys. QtWebEngine (Chromium) renders the exact
same Vite build the backend serves at http://127.0.0.1:8765, so the HUD code has one implementation.

Modes (settings.yaml → ui.mode; tray menu, Ctrl+Shift+M, the HUD's own buttons, or by voice):
  compact   reactor docked in a corner (`ui.corner`), always click-through (it never steals a click or focus). Dimmed
            while idle; on "Hey Jarvis" it brightens and the last lines of the conversation unfold beside it, then it
            folds and dims again `ui.linger_s` after the turn. With `ui.auto_hide` the window disappears entirely while
            resting. This is the "lives on your desktop" mode.
  panel     reactor + instruments + transcript + input, docked in the same corner, draggable by its header
  cinematic full-screen overlay that dims the desktop (`ui.cinematic_dim`) — the full interface, on demand only

Switching modes is animated: the window first grows to the full screen (the page keeps the old layout anchored to the
same corner, so nothing visibly moves), the page flies the reactor to its new place and unfolds the rest
(hud/src/transitions.ts), and ANIM_MS later the window shrinks to the new mode's rectangle — again pixel-identical to
what the page already shows. The Qt side therefore only ever does instant geometry changes that are invisible.

Page ↔ window: the page gets `window.jarvisBridge` (QWebChannel; qwebchannel.js is injected from Qt's resources at
document creation) with setMode / setVisible / show / hide / quit / setState / applyPrefs / beginDrag. Qt → page is
plain runJavaScript (window.setMode / switchMode / focusInput / openSettings).

Hiding: Ctrl+Shift+H, the tray, the settings drawer or "hide yourself" hide the window *explicitly* — it stays hidden
(voice still works; the tray icon shows the state) until shown the same ways. `ui.auto_hide` hides it only while
resting and lets "Hey Jarvis" bring it back. The explicit hide always wins.

Processes: Qt owns this process's main thread; the voice backend is a *child process* started by `ui.launcher` (see
app.main and D18) and this window is just one more WebSocket client of it. Hotkey callbacks arrive on pynput's thread
and are marshalled to Qt via a queued Signal.
"""

from __future__ import annotations

import ctypes
import json
import signal
import sys
import time
from collections.abc import Callable
from typing import Any, Literal

from loguru import logger
from PySide6.QtCore import QFile, QIODevice, QObject, QRect, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap, QSurfaceFormat
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineScript, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

from jarvis.config import Corner, Settings

Mode = Literal["compact", "panel", "cinematic"]
MODES: tuple[Mode, ...] = ("compact", "panel", "cinematic")
# compact: the reactor itself is ≤400 px in the docked corner of this (transparent, click-through) window; the rest of
# the area is where the last lines of conversation unfold while Jarvis is engaged (see hud/src/main.ts).
SIZES: dict[Mode, tuple[int, int]] = {"compact": (720, 560), "panel": (560, 860)}
MARGIN = 24  # distance from the screen edges; the page gets it as ?margin= so its docked layout matches exactly
ANIM_MS = 1250  # ≥ the page's whole switch animation (hud/src/transitions.ts: out 240 + flip 620 + staggered reveal)

STATE_COLOR = {
    "booting": "#2a86a8",
    "idle": "#34e4f5",
    "listening": "#ffb648",
    "thinking": "#c6ecff",
    "speaking": "#34e4f5",
    "confirming": "#ffb648",
    "error": "#ff5a5a",
}

# Win32
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WM_NCLBUTTONDOWN = 0x00A1
HTCAPTION = 2


def _reactor_icon(color: str = "#34e4f5") -> QIcon:
    """Draw a tiny arc-reactor as the tray icon (no asset files needed)."""
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    c = QColor(color)
    for r, w in ((28, 3), (20, 2), (12, 2)):
        p.setPen(QPen(c, w))
        p.drawEllipse(32 - r, 32 - r, 2 * r, 2 * r)
    p.setBrush(c)
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(26, 26, 12, 12)
    p.end()
    return QIcon(pm)


class _Signals(QObject):
    hotkey = Signal(str)  # "input" | "toggle" | "mode"


class _Page(QWebEnginePage):
    def javaScriptConsoleMessage(self, level, message, line, source) -> None:
        logger.debug("hud js: {} ({}:{})", message, source.rsplit("/", 1)[-1], line)


class HudBridge(QObject):
    """What the page sees as `window.jarvisBridge`. Every slot hops back onto the window object."""

    def __init__(self, win: HudWindow) -> None:
        super().__init__(win)
        self.win = win

    @Slot(str)
    def setMode(self, mode: str) -> None:
        if mode in MODES:
            self.win.switch_mode(mode)  # type: ignore[arg-type]

    @Slot(bool)
    def setVisible(self, visible: bool) -> None:  # the page's attention logic (ui.auto_hide)
        self.win.auto_visible(visible)

    @Slot()
    def show(self) -> None:
        self.win.set_hidden(False)

    @Slot()
    def hide(self) -> None:
        self.win.set_hidden(True)

    @Slot()
    def quit(self) -> None:
        self.win.quit()

    @Slot(str)
    def setState(self, state: str) -> None:
        self.win.set_state(state)

    @Slot(str)
    def applyPrefs(self, prefs_json: str) -> None:
        try:
            self.win.apply_prefs(json.loads(prefs_json))
        except ValueError as e:
            logger.debug("bad prefs from page: {}", e)

    @Slot()
    def beginDrag(self) -> None:  # panel header: let Windows move the window as if its title bar were dragged
        self.win.begin_drag()


class HudWindow(QWidget):
    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.s = settings
        self.mode: Mode = settings.ui.mode
        self.corner: Corner = settings.ui.corner
        self.auto_hide = settings.ui.auto_hide
        self.text_only = settings.ui.text_only  # mirrored from the page's prefs; the tray shows a checkbox for it
        self.user_hidden = False  # explicit hide (hotkey / tray / voice / settings) — sticky
        self.auto_hidden = False  # the page asked us to hide while resting (ui.auto_hide)
        self.state = "booting"
        self._page_ready = False
        self._switch_token = 0
        self._icons: dict[str, QIcon] = {}
        self.url = f"http://{settings.ui.host}:{settings.ui.port}/"
        self.setWindowTitle("J.A.R.V.I.S.")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

        self.view = QWebEngineView(self)
        self.view.setPage(_Page(self.view))
        page = self.view.page()
        page.setBackgroundColor(Qt.GlobalColor.transparent)
        st = self.view.settings()
        st.setAttribute(QWebEngineSettings.WebAttribute.ShowScrollBars, False)
        st.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        self.view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.view.setStyleSheet("background: transparent")
        self._install_bridge(page)
        self._load_when_up()

        self.signals = _Signals()
        self.signals.hotkey.connect(self._on_hotkey)
        self._tray()
        self._hotkeys()
        self.setGeometry(self._rect_for(self.mode))
        self._set_click_through(self.mode == "compact")
        logger.info("HUD mode: {} ({} corner)", self.mode, self.corner)

    # ---- page bridge -----------------------------------------------------------------------------
    def _install_bridge(self, page: QWebEnginePage) -> None:
        """QWebChannel: qwebchannel.js (from Qt's own resources) + a bootstrap that publishes window.jarvisBridge."""
        f = QFile(":/qtwebchannel/qwebchannel.js")
        if not f.open(QIODevice.OpenModeFlag.ReadOnly):
            logger.warning("qwebchannel.js not found in Qt resources; the HUD cannot talk back to the window")
            return
        src = bytes(f.readAll()).decode("utf-8") + (
            "\n;(function(){ if (!window.qt || !qt.webChannelTransport) return;"
            " new QWebChannel(qt.webChannelTransport, function(ch){ window.jarvisBridge = ch.objects.bridge;"
            " document.dispatchEvent(new CustomEvent('jarvis-bridge')); }); })();"
        )
        script = QWebEngineScript()
        script.setName("jarvis-bridge")
        script.setSourceCode(src)
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(False)
        page.scripts().insert(script)
        self.bridge = HudBridge(self)
        self.channel = QWebChannel(page)
        self.channel.registerObject("bridge", self.bridge)
        page.setWebChannel(self.channel)

    def _js(self, code: str) -> None:
        self.view.page().runJavaScript(code)

    # ---- geometry --------------------------------------------------------------------------------
    def _screen(self) -> QRect:
        return QApplication.primaryScreen().availableGeometry()

    def _rect_for(self, mode: Mode) -> QRect:
        screen = self._screen()
        if mode == "cinematic":
            return screen
        w, h = SIZES[mode]
        x = screen.x() + screen.width() - w - MARGIN if self.corner.endswith("right") else screen.x() + MARGIN
        y = screen.y() + screen.height() - h - MARGIN if self.corner.startswith("bottom") else screen.y() + MARGIN
        return QRect(x, y, w, h)

    def switch_mode(self, mode: Mode, animate: bool = True) -> None:
        """Change layout mode. Animated (see module docstring) unless the window is hidden or the page isn't up yet."""
        if mode == self.mode:
            return
        old, self.mode = self.mode, mode
        for act in self.mode_actions:
            act.setChecked(act.text().lower() == mode)
        self._switch_token += 1
        token = self._switch_token
        target = self._rect_for(mode)
        self._set_click_through(mode == "compact")
        if mode != "compact" and self.auto_hidden:  # only the resting compact HUD is ever auto-hidden
            self.auto_hidden = False
            self._apply_visibility()
        if not animate or not self._page_ready or not self.isVisible():
            self.setGeometry(target)
            self._js(f"window.setMode && window.setMode('{mode}')")
        else:
            self.setGeometry(self._screen())  # the page's docked layout stays put at this size → invisible step
            self._js(f"window.switchMode && window.switchMode('{mode}', '{old}')")
            if mode != "cinematic":
                QTimer.singleShot(ANIM_MS, lambda: self._settle(mode, token))
        if mode != "compact":
            self.raise_()
            self.activateWindow()
        logger.info("HUD mode: {} → {}", old, mode)

    def _settle(self, mode: Mode, token: int) -> None:
        """End of a switch animation: shrink the window to the mode's rectangle (pixel-identical to the page's layout)."""
        if token == self._switch_token and self.mode == mode:
            self.setGeometry(self._rect_for(mode))

    def resizeEvent(self, e) -> None:
        self.view.setGeometry(self.rect())
        super().resizeEvent(e)

    def _set_click_through(self, on: bool) -> None:
        """Compact mode floats over everything, so make it ignore the mouse (Win32 WS_EX_TRANSPARENT)."""
        if sys.platform != "win32":
            return
        hwnd = int(self.winId())
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style = (style | WS_EX_LAYERED | WS_EX_TRANSPARENT) if on else (style & ~WS_EX_TRANSPARENT)
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, on)

    def begin_drag(self) -> None:
        if sys.platform != "win32" or self.mode != "panel":
            return
        ctypes.windll.user32.ReleaseCapture()
        ctypes.windll.user32.SendMessageW(int(self.winId()), WM_NCLBUTTONDOWN, HTCAPTION, 0)

    # ---- visibility ------------------------------------------------------------------------------
    def set_hidden(self, hidden: bool) -> None:
        """Explicit hide/show (hotkey, tray, settings, voice). Showing also clears a pending auto-hide."""
        self.user_hidden = hidden
        if not hidden:
            self.auto_hidden = False
        self._apply_visibility()

    def auto_visible(self, visible: bool) -> None:
        """The page's attention logic: hide while resting / show when engaged (only if ui.auto_hide is on)."""
        if not self.auto_hide and not visible:
            return
        self.auto_hidden = not visible
        self._apply_visibility()

    def toggle(self) -> None:
        self.set_hidden(self.isVisible())

    def _apply_visibility(self) -> None:
        want = not (self.user_hidden or self.auto_hidden)
        if want and not self.isVisible():
            self.show()
            if self.mode != "compact":
                self.raise_()
            logger.debug("HUD shown")
        elif not want and self.isVisible():
            self.hide()
            logger.debug("HUD hidden ({})", "explicit" if self.user_hidden else "auto-hide while resting")
        self.act_toggle.setText("Show HUD" if self.user_hidden else "Hide HUD")

    # ---- state / prefs from the page -------------------------------------------------------------
    def set_state(self, state: str) -> None:
        self.state = state
        color = STATE_COLOR.get(state, STATE_COLOR["idle"])
        if color not in self._icons:
            self._icons[color] = _reactor_icon(color)
        self.tray.setIcon(self._icons[color])
        self._update_tooltip()

    def apply_prefs(self, prefs: dict[str, Any]) -> None:
        corner = prefs.get("corner")
        if corner in ("bottom-right", "bottom-left", "top-right", "top-left") and corner != self.corner:
            self.corner = corner
            if self.mode != "cinematic":
                self.setGeometry(self._rect_for(self.mode))
            logger.info("HUD corner: {}", corner)
        if "auto_hide" in prefs:
            self.auto_hide = bool(prefs["auto_hide"])
            if not self.auto_hide and self.auto_hidden:
                self.auto_hidden = False
                self._apply_visibility()
        if "text_only" in prefs:  # mirror the pref in the tray's checkbox (set by the drawer, header, voice or the tray itself)
            self.text_only = bool(prefs["text_only"])
            self.act_text_only.setChecked(self.text_only)
            self._update_tooltip()

    def set_text_only(self, on: bool) -> None:
        """Tray → page: the page sends the change to the hub like the drawer does, and it echoes back via apply_prefs."""
        self.text_only = on
        self._js(f"window.setPrefs && window.setPrefs({{text_only: {'true' if on else 'false'}}})")
        self._update_tooltip()

    def _update_tooltip(self) -> None:
        label = {"idle": "standby", "thinking": "processing", "confirming": "awaiting confirmation"}.get(self.state, self.state)
        self.tray.setToolTip(f"J.A.R.V.I.S. — {label}" + ("  · text only" if self.text_only else "") + ("  (HUD hidden)" if self.user_hidden else ""))

    def quit(self) -> None:
        logger.info("quit requested from the HUD")
        QApplication.instance().quit()

    # ---- loading ---------------------------------------------------------------------------------
    def _page_url(self) -> QUrl:
        return QUrl(f"{self.url}?mode={self.mode}&corner={self.corner}&margin={MARGIN}")

    def _load_when_up(self) -> None:
        """The backend may still be starting; retry the URL until the server answers."""
        self.view.load(self._page_url())
        self.view.loadFinished.connect(self._loaded)

    def _loaded(self, ok: bool) -> None:
        self._page_ready = ok
        if ok:
            return
        QTimer.singleShot(700, lambda: self.view.load(self._page_url()))

    # ---- tray ------------------------------------------------------------------------------------
    def _tray(self) -> None:
        self.tray = QSystemTrayIcon(_reactor_icon(), self)
        self.tray.setToolTip("J.A.R.V.I.S. — booting")
        menu = QMenu()
        hk = lambda s: s.replace("<", "").replace(">", "").title()
        self.act_toggle = QAction(f"Hide HUD  ({hk(self.s.ui.hotkey_toggle_hud)})", menu)
        self.act_toggle.triggered.connect(self.toggle)
        menu.addAction(self.act_toggle)
        a = QAction(f"Type a command  ({hk(self.s.ui.hotkey_text_input)})", menu)
        a.triggered.connect(self.focus_input)
        menu.addAction(a)
        f = QAction("Full interface  (Ctrl+Shift+M)", menu)
        f.triggered.connect(lambda: self._on_hotkey("mode"))
        menu.addAction(f)
        st = QAction("Settings…", menu)
        st.triggered.connect(self.open_settings)
        menu.addAction(st)
        self.act_text_only = QAction("Text only (don't speak)", menu, checkable=True)
        self.act_text_only.setChecked(self.text_only)
        self.act_text_only.triggered.connect(self.set_text_only)
        menu.addAction(self.act_text_only)
        menu.addSeparator()
        mode_menu = menu.addMenu("Mode")
        for m in MODES:
            act = QAction(m.title(), mode_menu, checkable=True)
            act.setChecked(m == self.mode)
            act.triggered.connect(lambda _=False, mm=m: self._choose_mode(mm))
            mode_menu.addAction(act)
        self.mode_actions = mode_menu.actions()
        r = QAction("Reload HUD", menu)
        r.triggered.connect(lambda: self.view.load(self._page_url()))
        menu.addAction(r)
        menu.addSeparator()
        q = QAction("Shut down J.A.R.V.I.S.", menu)
        q.triggered.connect(self.quit)
        menu.addAction(q)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: self.toggle() if r == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()

    def _choose_mode(self, m: Mode) -> None:
        self.set_hidden(False)
        self.switch_mode(m)

    def open_settings(self) -> None:
        self.set_hidden(False)
        if self.mode == "compact":
            self.switch_mode("panel")
        self.raise_()
        self.activateWindow()
        self._js("window.openSettings && window.openSettings()")

    def focus_input(self) -> None:
        self.set_hidden(False)
        if self.mode == "compact":
            self.switch_mode("panel")
        self.raise_()
        self.activateWindow()
        self.view.setFocus()
        self._js("window.focusInput && window.focusInput()")

    # ---- global hotkeys ---------------------------------------------------------------------------
    def _hotkeys(self) -> None:
        try:
            from pynput import keyboard
        except Exception as e:  # noqa: BLE001
            logger.warning("pynput unavailable, no global hotkeys: {}", e)
            return
        combos = {
            self.s.ui.hotkey_text_input: lambda: self.signals.hotkey.emit("input"),
            self.s.ui.hotkey_toggle_hud: lambda: self.signals.hotkey.emit("toggle"),
            "<ctrl>+<shift>+m": lambda: self.signals.hotkey.emit("mode"),
        }
        self._hk = keyboard.GlobalHotKeys(combos)
        self._hk.daemon = True
        self._hk.start()
        logger.info("hotkeys: {} = type, {} = hide/show HUD, ctrl+shift+m = full interface", self.s.ui.hotkey_text_input, self.s.ui.hotkey_toggle_hud)

    def _on_hotkey(self, what: str) -> None:
        if what == "input":
            self.focus_input()
        elif what == "toggle":
            self.toggle()
        elif what == "mode":  # HUD ⇄ full interface (panel counts as "not full")
            self.set_hidden(False)
            self.switch_mode("compact" if self.mode == "cinematic" else "cinematic")


def run_window(settings: Settings, on_tick: Callable[[], None] | None = None) -> int:
    """Create the Qt app + HUD window and run the Qt event loop on the calling (main) thread.

    `on_tick` is called every 2 s from the Qt loop (the launcher uses it to supervise the backend process)."""
    # Chromium flags: GPU compositing for the transparent WebGL canvas, and no throttling when the window is unfocused.
    sys.argv += ["--disable-renderer-backgrounding", "--enable-gpu-rasterization", "--ignore-gpu-blocklist"]
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    fmt = QSurfaceFormat()  # GL-backed widgets need an alpha channel in the default surface for translucency
    fmt.setAlphaBufferSize(8)
    QSurfaceFormat.setDefaultFormat(fmt)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # closing the window hides it; quit from the tray
    app.setApplicationName("JARVIS")
    app.setWindowIcon(_reactor_icon())
    # Qt's C++ loop never returns to Python on its own, so Ctrl+C in the console would be ignored; let the default
    # handler kill us, and tick a no-op timer so Python gets a chance to see the signal.
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    tick = QTimer()
    tick.timeout.connect(lambda: None)
    tick.start(250)
    supervise = QTimer()
    if on_tick is not None:
        supervise.timeout.connect(on_tick)
        supervise.start(2000)
    t0 = time.perf_counter()
    win = HudWindow(settings)
    win.show()
    logger.info("HUD window up ({} mode) in {:.2f}s", win.mode, time.perf_counter() - t0)
    return app.exec()
