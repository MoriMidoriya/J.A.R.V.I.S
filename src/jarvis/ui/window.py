"""PySide6 shell: a frameless, transparent, always-on-top QWebEngineView showing the HUD.

Why a separate shell instead of a browser tab? The HUD should float over whatever you are doing (like the movie),
with no chrome, see-through background, a tray icon and global hotkeys. QtWebEngine (Chromium) renders the exact
same Vite build the backend serves at http://127.0.0.1:8765, so the HUD code has one implementation.

Modes (settings.yaml → ui.mode, switchable from the tray menu or Ctrl+Shift+M):
  compact   reactor in the bottom-right corner, always click-through (it never steals a click or focus). Dimmed while
            idle; on "Hey Jarvis" it brightens and the last lines of the conversation unfold beside it, then it folds
            and dims again `ui.linger_s` after the turn. This is the "lives on your desktop" mode.
  panel     reactor + instruments + transcript + input, bottom-right, draggable — for reading and typing
  cinematic full-screen overlay that dims the desktop (`ui.cinematic_dim`) — the movie takeover, on demand only

Hotkeys (global, via pynput):
  ui.hotkey_text_input  (default Ctrl+Shift+J)  show the HUD and focus the text box
  ui.hotkey_toggle_hud  (default Ctrl+Shift+H)  show / hide
  Ctrl+Shift+M                                  cycle mode

Processes: Qt owns this process's main thread; the voice backend is a *child process* started by `ui.launcher` (see
app.main and D18) and this window is just one more WebSocket client of it. Hotkey callbacks arrive on pynput's thread
and are marshalled to Qt via a queued Signal.
"""

from __future__ import annotations

import ctypes
import signal
import sys
import time
from collections.abc import Callable
from typing import Literal

from loguru import logger
from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap, QSurfaceFormat
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

from jarvis.config import Settings

Mode = Literal["compact", "panel", "cinematic"]
MODES: tuple[Mode, ...] = ("compact", "panel", "cinematic")
# compact: the reactor itself is ~300 px in the bottom-right corner of this (transparent, click-through) window; the
# rest of the area is where the last lines of conversation unfold while Jarvis is engaged (see hud/src/main.ts).
SIZES: dict[Mode, tuple[int, int]] = {"compact": (720, 560), "panel": (560, 860)}
MARGIN = 24

# Win32 extended window styles for click-through
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000


def _reactor_icon(color: str = "#38e8ff") -> QIcon:
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


class HudWindow(QWidget):
    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.s = settings
        self.mode: Mode = settings.ui.mode
        self.url = f"http://{settings.ui.host}:{settings.ui.port}/"
        self.setWindowTitle("J.A.R.V.I.S.")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

        self.view = QWebEngineView(self)
        self.view.setPage(_Page(self.view))
        self.view.page().setBackgroundColor(Qt.GlobalColor.transparent)
        st = self.view.settings()
        st.setAttribute(QWebEngineSettings.WebAttribute.ShowScrollBars, False)
        st.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        self.view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.view.setStyleSheet("background: transparent")
        self._load_when_up()

        self.signals = _Signals()
        self.signals.hotkey.connect(self._on_hotkey)
        self._tray()
        self._hotkeys()
        self.apply_mode(self.mode)
        self._drag_pos = None

    # ---- layout ----------------------------------------------------------------------------------
    def apply_mode(self, mode: Mode) -> None:
        self.mode = mode
        screen = QApplication.primaryScreen().availableGeometry()
        if mode == "cinematic":
            self.setGeometry(screen)
        else:
            w, h = SIZES[mode]
            self.setGeometry(screen.right() - w - MARGIN, screen.bottom() - h - MARGIN, w, h)
        self.view.setGeometry(self.rect())
        self.view.page().runJavaScript(f"window.setMode && window.setMode('{mode}')")
        self._set_click_through(mode == "compact")
        logger.info("HUD mode: {}", mode)

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

    # ---- loading ---------------------------------------------------------------------------------
    def _load_when_up(self) -> None:
        """The backend may still be starting; retry the URL until the server answers."""
        self.view.load(QUrl(f"{self.url}?mode={self.mode}"))
        self.view.loadFinished.connect(self._loaded)

    def _loaded(self, ok: bool) -> None:
        if ok:
            return
        QTimer.singleShot(700, lambda: self.view.load(QUrl(f"{self.url}?mode={self.mode}")))

    # ---- tray ------------------------------------------------------------------------------------
    def _tray(self) -> None:
        self.tray = QSystemTrayIcon(_reactor_icon(), self)
        self.tray.setToolTip("J.A.R.V.I.S.")
        menu = QMenu()
        self.act_toggle = QAction("Hide HUD", menu)
        self.act_toggle.triggered.connect(self.toggle)
        menu.addAction(self.act_toggle)
        a = QAction(f"Type a command  ({self.s.ui.hotkey_text_input.replace('<', '').replace('>', '').title()})", menu)
        a.triggered.connect(self.focus_input)
        menu.addAction(a)
        menu.addSeparator()
        mode_menu = menu.addMenu("Mode")
        for m in MODES:
            act = QAction(m.title(), mode_menu, checkable=True)
            act.setChecked(m == self.mode)
            act.triggered.connect(lambda _=False, mm=m: self._choose_mode(mm))
            mode_menu.addAction(act)
        self.mode_actions = mode_menu.actions()
        r = QAction("Reload HUD", menu)
        r.triggered.connect(lambda: self.view.load(QUrl(f"{self.url}?mode={self.mode}")))
        menu.addAction(r)
        menu.addSeparator()
        q = QAction("Quit J.A.R.V.I.S.", menu)
        q.triggered.connect(QApplication.instance().quit)
        menu.addAction(q)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: self.toggle() if r == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()

    def _choose_mode(self, m: Mode) -> None:
        for act in self.mode_actions:
            act.setChecked(act.text().lower() == m)
        self.apply_mode(m)
        self.show()

    def toggle(self) -> None:
        if self.isVisible():
            self.hide()
            self.act_toggle.setText("Show HUD")
        else:
            self.show()
            self.act_toggle.setText("Hide HUD")

    def focus_input(self) -> None:
        if self.mode == "compact":
            self._choose_mode("panel")
        self.show()
        self.raise_()
        self.activateWindow()
        self.view.setFocus()
        self.view.page().runJavaScript("window.focusInput && window.focusInput()")

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
        logger.info("hotkeys: {} = type, {} = toggle HUD, ctrl+shift+m = mode", self.s.ui.hotkey_text_input, self.s.ui.hotkey_toggle_hud)

    def _on_hotkey(self, what: str) -> None:
        if what == "input":
            self.focus_input()
        elif what == "toggle":
            self.toggle()
        elif what == "mode":
            self._choose_mode(MODES[(MODES.index(self.mode) + 1) % len(MODES)])

    # ---- drag to move (panel mode) -----------------------------------------------------------------
    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e) -> None:
        if self._drag_pos is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, e) -> None:
        self._drag_pos = None


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
