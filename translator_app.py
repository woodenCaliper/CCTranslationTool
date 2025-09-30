"""Desktop utility to translate copied text via Google Translate."""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import IO, Callable, Optional, Protocol, Sequence

try:  # pragma: no cover - executed during module import
    import pyperclip  # type: ignore
except ImportError:  # pragma: no cover - handled in __init__
    pyperclip = None  # type: ignore

try:
    import tkinter as tk
    from tkinter import messagebox, scrolledtext, font as tkfont
except ImportError as exc:  # pragma: no cover - tkinter is part of stdlib on Windows
    raise SystemExit("tkinter is required to display the translation window") from exc

try:  # pragma: no cover - optional dependency for system tray support
    import pystray  # type: ignore
    from pystray import MenuItem  # type: ignore
except ImportError:  # pragma: no cover - handled when starting the tray icon
    pystray = None  # type: ignore
    MenuItem = None  # type: ignore

try:  # pragma: no cover - optional dependency for system tray support
    from PIL import Image, ImageDraw  # type: ignore
except ImportError:  # pragma: no cover - handled when starting the tray icon
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore

import sys
import tempfile

from logging.handlers import RotatingFileHandler

from hotkey_manager import (
    BaseHotkeyService,
    HotkeyBinding,
    HotkeyEvent,
    RegisterHotKeyService,
    build_bindings_from_preferences,
)
from translation_service import GoogleTranslateClient, TranslationError, TranslationResult


DOUBLE_COPY_INTERVAL = 0.25  # Seconds allowed between two copy events.
MIN_TRIGGER_INTERVAL = 0.15

LOG_FILE_NAME = "cctranslationtool_hotkeys.log"
LOG_MAX_BYTES = 2_097_152
LOG_BACKUP_COUNT = 3

PREFERENCES_FILE = Path.home() / ".cctranslationtool_preferences.json"

DEFAULT_HOTKEY_PREFERENCES = {
    "copy": {"combo": "Ctrl+C", "press_count": 2},
    "state_dump": {"combo": "F8", "press_count": 1},
    "double_press_interval": DOUBLE_COPY_INTERVAL,
    "min_trigger_interval": MIN_TRIGGER_INTERVAL,
}


def _get_hotkey_logger() -> logging.Logger:
    logger = logging.getLogger("cctranslationtool.hotkeys")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    log_dir = PREFERENCES_FILE.parent
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    log_path = log_dir / LOG_FILE_NAME
    handler = RotatingFileHandler(
        log_path,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)
    return logger

LANGUAGE_SEQUENCE = ("ja", "en")
LANGUAGE_DISPLAY_NAMES = {
    "ja": "日本語",
    "en": "英語",
    "auto": "自動検出",
    None: "自動検出",
}


def _load_preferences() -> dict:
    try:
        data = json.loads(PREFERENCES_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_preferences(preferences: dict) -> None:
    try:
        PREFERENCES_FILE.parent.mkdir(parents=True, exist_ok=True)
        PREFERENCES_FILE.write_text(json.dumps(preferences, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _load_saved_dest_language(default: str = "ja") -> str:
    data = _load_preferences()
    dest = data.get("dest_language") if isinstance(data, dict) else None
    return dest if isinstance(dest, str) else default


def _save_dest_language(dest: str) -> None:
    data = _load_preferences()
    data["dest_language"] = dest
    if "hotkeys" not in data:
        data["hotkeys"] = DEFAULT_HOTKEY_PREFERENCES
    _save_preferences(data)


def _load_hotkey_preferences() -> dict:
    data = _load_preferences()
    hotkeys = data.get("hotkeys") if isinstance(data, dict) else None
    if not isinstance(hotkeys, dict):
        hotkeys = {}

    result = json.loads(json.dumps(DEFAULT_HOTKEY_PREFERENCES))

    def _merge_single(key: str, default_press: int) -> None:
        source = hotkeys.get(key)
        if not isinstance(source, dict):
            return
        combo = source.get("combo")
        press_count = source.get("press_count")
        if isinstance(combo, str) and combo.strip():
            result[key]["combo"] = combo.strip()
        if isinstance(press_count, int) and press_count >= 1:
            result[key]["press_count"] = press_count
        else:
            result[key]["press_count"] = default_press

    _merge_single("copy", DEFAULT_HOTKEY_PREFERENCES["copy"]["press_count"])
    _merge_single("state_dump", DEFAULT_HOTKEY_PREFERENCES["state_dump"]["press_count"])

    double_interval = hotkeys.get("double_press_interval")
    if isinstance(double_interval, (int, float)) and double_interval > 0:
        result["double_press_interval"] = float(double_interval)

    min_interval = hotkeys.get("min_trigger_interval")
    if isinstance(min_interval, (int, float)) and min_interval >= 0:
        result["min_trigger_interval"] = float(min_interval)

    return result


def _language_display(language_code: Optional[str]) -> str:
    if language_code is None:
        return LANGUAGE_DISPLAY_NAMES[None]
    return LANGUAGE_DISPLAY_NAMES.get(language_code, language_code)


def _resource_path(relative_path: str) -> Path:
    """Return an absolute path to a bundled resource."""

    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))  # type: ignore[attr-defined]
    return base_path / relative_path


@dataclass
class TranslationRequest:
    text: str
    src: Optional[str]
    dest: str
    reposition: bool = True


class TranslatorProtocol(Protocol):  # pragma: no cover - protocol is for type checking only
    def translate(self, text: str, src: Optional[str], dest: str) -> TranslationResult | SimpleNamespace:
        """Translate text and return a result object."""


@dataclass
class DoubleCopyDetector:
    """Utility that tracks consecutive copy events within a time window."""

    interval: float
    now: Callable[[], float]
    required_count: int = 2
    _last_time: float = field(default=0.0, init=False)
    _count: int = field(default=0, init=False)

    def register(self, *, timestamp: Optional[float] = None) -> bool:
        """Register a copy event.

        Returns ``True`` if the event completes a "double copy" sequence.
        """

        current = self.now() if timestamp is None else timestamp
        if current - self._last_time <= self.interval:
            self._count += 1
        else:
            self._count = 1
        self._last_time = current

        if self._count >= self.required_count:
            self._count = 0
            return True
        return False

    def reset(self) -> None:
        """Reset the detector state so future copies restart the sequence."""

        self._last_time = 0.0
        self._count = 0


class SingleInstanceError(RuntimeError):
    """Raised when another instance of the application is already running."""


class SingleInstanceGuard:
    """Cross-platform single instance guard using a filesystem lock."""

    def __init__(self, name: str) -> None:
        self._lock_path = Path(tempfile.gettempdir()) / f"{name}.lock"
        self._lock_file: Optional[IO[str]] = None

    def acquire(self) -> None:
        if self._lock_file is not None:
            return
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_file = open(self._lock_path, "a+")
        try:
            if sys.platform == "win32":  # pragma: no cover - platform specific
                import msvcrt  # type: ignore

                self._lock_file.seek(0)
                msvcrt.locking(self._lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:  # pragma: no cover - exercised on non-Windows platforms
                import fcntl  # type: ignore

                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.release()
            raise SingleInstanceError("Another instance is already running") from exc

    def release(self) -> None:
        if self._lock_file is None:
            return
        try:
            if sys.platform == "win32":  # pragma: no cover - platform specific
                import msvcrt  # type: ignore

                self._lock_file.seek(0)
                try:
                    msvcrt.locking(self._lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:  # pragma: no cover - exercised on non-Windows platforms
                import fcntl  # type: ignore

                try:
                    fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
        finally:
            try:
                self._lock_file.close()
            finally:
                self._lock_file = None
                with contextlib.suppress(OSError):
                    self._lock_path.unlink()

    def __enter__(self) -> "SingleInstanceGuard":
        self.acquire()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.release()


class TranslationWindowManager:
    """Create and reuse a single Tk window for displaying translations."""

    def __init__(
        self,
        source_language: Optional[str],
        dest_language: str,
        *,
        language_toggle_callback: Optional[Callable[[], None]] = None,
        source_language_callback: Optional[Callable[[Optional[str]], None]] = None,
        dest_language_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._source_language = source_language
        self._dest_language = dest_language
        self._queue: "queue.Queue[tuple[str, str, Optional[str], bool]]" = queue.Queue()
        self._ready = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._language_toggle_callback = language_toggle_callback
        self._source_language_callback = source_language_callback
        self._dest_language_callback = dest_language_callback
        self._window: Optional[tk.Tk] = None
        self._toggle_button: Optional[tk.Button] = None
        self._source_button: Optional[tk.Button] = None
        self._dest_button: Optional[tk.Button] = None

    def show(
        self,
        original: str,
        translated: str,
        detected_source: Optional[str],
        *,
        reposition: bool = True,
    ) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._ready.clear()
            self._thread = threading.Thread(target=self._run_window, daemon=True)
            self._thread.start()
            self._ready.wait()
        self._queue.put((original, translated, detected_source, reposition))

    def update_languages(self, source_language: Optional[str], dest_language: str) -> None:
        self._source_language = source_language
        self._dest_language = dest_language
        if self._window is not None:
            self._window.after(0, self._update_language_widgets)

    def _update_language_widgets(self) -> None:
        if self._source_button is not None:
            self._source_button.configure(text=self._source_button_text())
        if self._dest_button is not None:
            self._dest_button.configure(text=self._dest_button_text())

    def _source_button_text(self) -> str:
        display = _language_display(self._source_language or "auto")
        return f"検出言語: {display}"

    def _dest_button_text(self) -> str:
        dest_label = _language_display(self._dest_language)
        return f"翻訳先: {dest_label}"

    def _toggle_button_style(self, button: tk.Button, font: tkfont.Font) -> None:
        button.configure(
            text="⇄",
            font=font,
            width=3,
            bg="#1a73e8",
            fg="white",
            activebackground="#1765c1",
            activeforeground="white",
            relief=tk.FLAT,
            bd=0,
            highlightthickness=0,
            cursor="hand2",
        )

    def _on_source_language_selected(self, selection: Optional[str]) -> None:
        self._source_language = selection
        if self._source_language_callback is not None:
            self._source_language_callback(selection)
        self._update_language_widgets()

    def _on_dest_language_selected(self, selection: str) -> None:
        self._dest_language = selection
        if self._dest_language_callback is not None:
            self._dest_language_callback(selection)
        self._update_language_widgets()

    def _on_language_toggle(self) -> None:
        if self._language_toggle_callback is not None:
            self._language_toggle_callback()

    def _open_source_menu(self, widget: tk.Widget) -> None:
        if self._window is None:
            return
        menu = tk.Menu(self._window, tearoff=0)
        options: tuple[Optional[str], ...] = (None, "ja", "en")
        for code in options:
            label = _language_display(code or "auto")
            menu.add_command(
                label=label,
                command=lambda c=code: self._on_source_language_selected(c),
            )
        try:
            menu.tk_popup(
                widget.winfo_rootx(),
                widget.winfo_rooty() + widget.winfo_height(),
            )
        finally:
            menu.grab_release()

    def _open_dest_menu(self, widget: tk.Widget) -> None:
        if self._window is None:
            return
        menu = tk.Menu(self._window, tearoff=0)
        options: tuple[str, ...] = ("ja", "en")
        for code in options:
            label = _language_display(code)
            menu.add_command(
                label=label,
                command=lambda c=code: self._on_dest_language_selected(c),
            )
        try:
            menu.tk_popup(
                widget.winfo_rootx(),
                widget.winfo_rooty() + widget.winfo_height(),
            )
        finally:
            menu.grab_release()

    def _run_window(self) -> None:
        window = tk.Tk()
        self._window = window
        window.title("CCTranslationTool")
        window.geometry("500x400")
        window.withdraw()

        default_font = tkfont.nametofont("TkDefaultFont")
        preferred_families = (
            "Google Sans",
            "Roboto",
            "Noto Sans JP",
            "Noto Sans CJK JP",
            "Noto Sans",
            "Arial",
            default_font.actual("family"),
        )
        available_families = {
            name.lower(): name for name in tkfont.families()
        }

        def resolve_family(preferences: tuple[str, ...]) -> str:
            for family in preferences:
                key = family.lower()
                if key in available_families:
                    return available_families[key]
            return default_font.actual("family")

        base_family = resolve_family(preferred_families)
        button_font = tkfont.Font(family=base_family, size=11)
        toggle_font = tkfont.Font(family=base_family, size=14, weight="bold")
        label_font = tkfont.Font(family=base_family, size=10, weight="bold")
        text_font = tkfont.Font(family=base_family, size=12)

        controls_frame = tk.Frame(window, bg="#f8f9fa")
        controls_frame.pack(fill=tk.X, padx=10, pady=(10, 5))

        source_button = tk.Button(
            controls_frame,
            text=self._source_button_text(),
            font=button_font,
            relief=tk.SOLID,
            bd=1,
            bg="white",
            activebackground="#e8f0fe",
            cursor="hand2",
            command=lambda: self._open_source_menu(source_button),
        )
        source_button.pack(side=tk.LEFT, expand=True, fill=tk.X)
        self._source_button = source_button

        toggle_button = tk.Button(
            controls_frame,
            command=self._on_language_toggle,
        )
        self._toggle_button_style(toggle_button, toggle_font)
        toggle_button.pack(side=tk.LEFT, padx=8)
        self._toggle_button = toggle_button

        dest_button = tk.Button(
            controls_frame,
            text=self._dest_button_text(),
            font=button_font,
            relief=tk.SOLID,
            bd=1,
            bg="white",
            activebackground="#e8f0fe",
            cursor="hand2",
            command=lambda: self._open_dest_menu(dest_button),
        )
        dest_button.pack(side=tk.LEFT, expand=True, fill=tk.X)
        self._dest_button = dest_button

        content_pane = tk.PanedWindow(window, orient=tk.VERTICAL, sashwidth=6)
        content_pane.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        original_frame = tk.Frame(content_pane)
        content_pane.add(original_frame, minsize=80)

        original_label = tk.Label(original_frame, text="Original", font=label_font)
        original_label.pack(anchor="w", pady=(0, 4))

        original_box = scrolledtext.ScrolledText(original_frame, wrap=tk.WORD, height=8)
        original_box.configure(state=tk.DISABLED, font=text_font)
        original_box.pack(fill=tk.BOTH, expand=True)

        translated_frame = tk.Frame(content_pane)
        content_pane.add(translated_frame, minsize=80)

        translated_label = tk.Label(translated_frame, text="Translated", font=label_font)
        translated_label.pack(anchor="w", pady=(0, 4))

        translated_box = scrolledtext.ScrolledText(translated_frame, wrap=tk.WORD, height=8)
        translated_box.configure(state=tk.DISABLED, font=text_font)
        translated_box.pack(fill=tk.BOTH, expand=True)

        def hide_window() -> None:
            window.withdraw()

        def handle_escape(event: tk.Event) -> str:
            """Hide the window when the Escape key is pressed."""

            hide_window()
            return "break"

        window.protocol("WM_DELETE_WINDOW", hide_window)
        window.bind("<Escape>", handle_escape)

        def _monitor_work_area(pointer_x: int, pointer_y: int) -> tuple[int, int, int, int] | None:
            """Return work area bounds for the monitor nearest the pointer."""

            # Tk's multi-monitor support on Windows is limited to the primary display
            # when querying ``winfo_screenwidth``/``winfo_screenheight``.  We fall back
            # to the Windows API so the popup can be constrained to whichever monitor
            # currently hosts the cursor.
            import sys

            if sys.platform != "win32":
                return None

            try:
                import ctypes
                from ctypes import wintypes
            except Exception:  # pragma: no cover - only executed on Windows
                return None

            MONITOR_DEFAULTTONEAREST = 2

            user32 = ctypes.windll.user32  # type: ignore[attr-defined]

            class MONITORINFO(ctypes.Structure):  # pragma: no cover - Windows only
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD),
                ]

            monitor = user32.MonitorFromPoint(  # pragma: no cover - Windows only
                wintypes.POINT(pointer_x, pointer_y),
                MONITOR_DEFAULTTONEAREST,
            )
            if not monitor:
                return None

            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(MONITORINFO)
            if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                return None

            work = info.rcWork
            return work.left, work.top, work.right, work.bottom

        def place_near_pointer() -> None:
            window.update_idletasks()
            width = window.winfo_width() or window.winfo_reqwidth()
            height = window.winfo_height() or window.winfo_reqheight()
            pointer_x = window.winfo_pointerx()
            pointer_y = window.winfo_pointery()

            bounds = _monitor_work_area(pointer_x, pointer_y)
            if bounds is None:
                left, top = 0, 0
                right = window.winfo_screenwidth()
                bottom = window.winfo_screenheight()
            else:
                left, top, right, bottom = bounds

            target_x = pointer_x - width // 2
            target_y = pointer_y - height // 2

            max_x = max(right - width, left)
            max_y = max(bottom - height, top)
            x = min(max(target_x, left), max_x)
            y = min(max(target_y, top), max_y)
            window.geometry(f"+{x}+{y}")

        def _force_foreground() -> None:
            """Ensure the Tk window becomes the active foreground window."""

            import sys

            if sys.platform != "win32":
                return

            try:  # pragma: no cover - Windows specific implementation
                import ctypes
                from ctypes import wintypes
            except Exception:  # pragma: no cover - if ctypes is unavailable
                return

            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

            hwnd = wintypes.HWND(window.winfo_id())
            if not hwnd:
                return

            SW_SHOWNORMAL = 1

            user32.ShowWindow(hwnd, SW_SHOWNORMAL)

            user32.GetForegroundWindow.restype = wintypes.HWND
            foreground_hwnd = user32.GetForegroundWindow()
            if foreground_hwnd == hwnd:
                return

            user32.GetWindowThreadProcessId.restype = wintypes.DWORD
            user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]

            pid = wintypes.DWORD()
            foreground_thread_id = user32.GetWindowThreadProcessId(
                foreground_hwnd, ctypes.byref(pid)
            )
            current_thread_id = kernel32.GetCurrentThreadId()

            attached = False
            if foreground_thread_id and foreground_thread_id != current_thread_id:
                attached = bool(
                    user32.AttachThreadInput(foreground_thread_id, current_thread_id, True)
                )

            try:
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
            finally:
                if attached:
                    user32.AttachThreadInput(foreground_thread_id, current_thread_id, False)

        def bring_to_front(reposition: bool) -> None:
            if reposition:
                place_near_pointer()
            window.deiconify()
            window.lift()
            _force_foreground()
            window.attributes("-topmost", True)
            window.after(100, lambda: window.attributes("-topmost", False))
            window.focus_force()

        def apply_update() -> None:
            try:
                while True:
                    original, translated, detected_source, reposition = self._queue.get_nowait()
                    original_box.configure(state=tk.NORMAL)
                    original_box.delete("1.0", tk.END)
                    original_box.insert(tk.END, original)
                    original_box.configure(state=tk.DISABLED)
                    translated_box.configure(state=tk.NORMAL)
                    translated_box.delete("1.0", tk.END)
                    translated_box.insert(tk.END, translated)
                    translated_box.configure(state=tk.DISABLED)
                    bring_to_front(reposition)
            except queue.Empty:
                pass
            window.after(100, apply_update)

        self._ready.set()
        apply_update()
        window.mainloop()
        self._window = None
        self._toggle_button = None
        self._source_button = None
        self._dest_button = None


class SystemTrayController:
    """Manage a Windows system tray icon with Reboot and Exit commands."""

    def __init__(self, app: "CCTranslationApp") -> None:
        self._app = app
        self._icon: Optional["pystray.Icon"] = None
        self._icon_image: Optional["Image.Image"] = None

    @staticmethod
    def _is_supported() -> bool:
        return (
            sys.platform == "win32"
            and pystray is not None
            and MenuItem is not None
            and Image is not None
            and ImageDraw is not None
        )

    def start(self) -> None:
        if not self._is_supported():
            if sys.platform == "win32":
                print(
                    "System tray icon is unavailable because required dependencies are missing."
                )
            return

        assert pystray is not None  # noqa: S101 - guarded by _is_supported
        image = self._create_icon_image()
        self._icon_image = image
        menu = pystray.Menu(
            MenuItem("Reboot", self._on_reboot),
            MenuItem("Exit", self._on_exit),
        )
        self._icon = pystray.Icon("cctranslationtool", image, "CCTranslationTool", menu=menu)
        self._icon.run_detached()

    def stop(self) -> None:
        if self._icon is not None:
            self._icon.stop()
            self._icon = None
        self._icon_image = None

    def _on_exit(self, icon: "pystray.Icon", _: MenuItem) -> None:
        self._app.stop()
        icon.stop()

    def _on_reboot(self, icon: "pystray.Icon", _: MenuItem) -> None:
        self._app.reboot()
        icon.stop()

    def _create_icon_image(self) -> "Image.Image":
        assert Image is not None  # noqa: S101 - guarded by _is_supported

        icon_path = _resource_path("icon/CCT_icon.png")
        if icon_path.exists():
            try:
                with Image.open(icon_path) as icon:
                    return icon.convert("RGBA")
            except Exception:
                pass

        assert ImageDraw is not None  # noqa: S101 - fallback icon requires drawing support
        size = 64
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.ellipse((8, 8, size - 8, size - 8), fill=(28, 114, 206, 255))
        draw.rectangle((size // 2 - 4, 16, size // 2 + 4, size - 16), fill=(255, 255, 255, 255))
        return image


class CCTranslationApp:
    """Listens for double Ctrl+C and shows the translated text."""

    def __init__(
        self,
        dest_language: str,
        source_language: Optional[str] = None,
        *,
        translator_factory: Callable[[], TranslatorProtocol] = GoogleTranslateClient,
        clipboard_module=pyperclip,
        time_provider: Callable[[], float] = time.perf_counter,
        display_callback: Optional[Callable[[str, str, Optional[str]], None]] = None,
        double_copy_interval: Optional[float] = None,
        min_trigger_interval: Optional[float] = None,
        hotkey_service_factory: Optional[
            Callable[[Sequence[HotkeyBinding], "queue.Queue[HotkeyEvent]", logging.Logger, Callable[[], float]], BaseHotkeyService]
        ] = None,
        hotkey_bindings: Optional[Sequence[HotkeyBinding]] = None,
    ) -> None:
        self.dest_language = dest_language
        self.source_language = source_language
        self._translator: Optional[TranslatorProtocol] = None
        self._translator_factory = translator_factory
        self._translator_lock = threading.Lock()
        self._hotkey_preferences = _load_hotkey_preferences()
        if double_copy_interval is None:
            double_copy_interval = self._hotkey_preferences.get(
                "double_press_interval", DOUBLE_COPY_INTERVAL
            )
        copy_press_count = max(
            1,
            int(self._hotkey_preferences["copy"].get("press_count", DEFAULT_HOTKEY_PREFERENCES["copy"]["press_count"])),
        )
        self._copy_detector = DoubleCopyDetector(
            double_copy_interval, time_provider, required_count=copy_press_count
        )
        self._min_trigger_interval = (
            min_trigger_interval
            if min_trigger_interval is not None
            else self._hotkey_preferences.get("min_trigger_interval", MIN_TRIGGER_INTERVAL)
        )
        self._last_trigger_time = 0.0
        self._time_provider = time_provider
        self._lock = threading.Lock()
        self._request_queue: "queue.Queue[TranslationRequest]" = queue.Queue()
        self._hotkey_event_queue: "queue.Queue[HotkeyEvent]" = queue.Queue()
        self._stop_event = threading.Event()
        self._restart_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None
        if clipboard_module is None:
            raise RuntimeError(
                "The 'pyperclip' package is required. Install it with 'pip install pyperclip'."
            )
        self._clipboard = clipboard_module
        self._display_callback = display_callback
        self._window_manager = TranslationWindowManager(
            self.source_language,
            self.dest_language,
            language_toggle_callback=self._toggle_language,
            source_language_callback=self._set_source_language,
            dest_language_callback=self._set_dest_language,
        )
        self._tray_controller: Optional[SystemTrayController] = None
        self._language_options = list(LANGUAGE_SEQUENCE)
        self._last_original_text: Optional[str] = None
        self._hotkey_logger = _get_hotkey_logger()
        self._hotkey_service_factory = hotkey_service_factory
        self._hotkey_service: Optional[BaseHotkeyService] = None
        self._hotkey_dispatcher: Optional[threading.Thread] = None
        self._state_dump_press_count = max(
            1,
            int(
                self._hotkey_preferences["state_dump"].get(
                    "press_count", DEFAULT_HOTKEY_PREFERENCES["state_dump"]["press_count"]
                )
            ),
        )
        self._state_dump_detector = (
            DoubleCopyDetector(
                double_copy_interval,
                time_provider,
                required_count=self._state_dump_press_count,
            )
            if self._state_dump_press_count > 1
            else None
        )
        if hotkey_bindings is not None:
            self._hotkey_bindings = list(hotkey_bindings)
        elif sys.platform == "win32":
            prefs_for_bindings = {"hotkeys": self._hotkey_preferences}
            try:
                self._hotkey_bindings = build_bindings_from_preferences(prefs_for_bindings)
            except Exception as exc:
                self._hotkey_logger.error("Failed to build hotkey bindings: %s", exc)
                self._hotkey_bindings = []
        else:
            self._hotkey_bindings = []

    @property
    def translator(self) -> TranslatorProtocol:
        with self._translator_lock:
            if self._translator is None:
                self._translator = self._translator_factory()
            translator = self._translator
        assert translator is not None  # For type checkers
        return translator

    def _reset_translator(self) -> None:
        with self._translator_lock:
            self._translator = None

    def start(self, *, tray_controller: Optional[SystemTrayController] = None) -> None:
        """Start listening for keyboard events and processing translations."""

        self._tray_controller = tray_controller

        while True:
            self._ensure_background_threads()
            self._hotkey_service = self._create_hotkey_service()
            if self._hotkey_service is not None:
                try:
                    self._hotkey_service.start()
                    self._hotkey_logger.info("Hotkey service started with %s", self._hotkey_service.describe_bindings())
                except Exception as exc:
                    self._hotkey_logger.exception("Failed to start hotkey service: %s", exc)
                    self._hotkey_service.stop()
                    self._hotkey_service = None

            print(
                "CCTranslationTool is running. Double press Ctrl+C on selected text to translate."
            )
            if self._tray_controller is not None:
                self._tray_controller.start()

            try:
                self._stop_event.wait()
            except KeyboardInterrupt:  # pragma: no cover - manual console interruption
                self.stop()
            finally:
                if self._tray_controller is not None:
                    self._tray_controller.stop()
                if self._hotkey_service is not None:
                    self._hotkey_service.stop()
                    self._hotkey_logger.info("Hotkey service stopped")
                    self._hotkey_service = None

            if self._restart_event.is_set():
                self._restart_event.clear()
                self._stop_event.clear()
                continue

            break

    def stop(self) -> None:
        """Signal the application to shut down."""

        self._restart_event.clear()
        self._stop_event.set()
        if self._hotkey_dispatcher is not None and self._hotkey_dispatcher.is_alive():
            self._hotkey_event_queue.put(None)

    def reboot(self) -> None:
        """Restart the application loop and reset cached translator state."""

        self._reset_translator()
        self._restart_event.set()
        self._stop_event.set()

    @staticmethod
    def _default_hotkey_service_factory(
        bindings: Sequence[HotkeyBinding],
        event_queue: "queue.Queue[HotkeyEvent]",
        logger: logging.Logger,
        time_provider: Callable[[], float],
    ) -> BaseHotkeyService:
        return RegisterHotKeyService(bindings, event_queue, logger, time_provider=time_provider)

    def _create_hotkey_service(self) -> Optional[BaseHotkeyService]:
        if not self._hotkey_bindings:
            self._hotkey_logger.warning(
                "No hotkey bindings available; global hotkeys are disabled"
            )
            return None
        factory = self._hotkey_service_factory or self._default_hotkey_service_factory
        try:
            return factory(self._hotkey_bindings, self._hotkey_event_queue, self._hotkey_logger, self._time_provider)
        except Exception as exc:
            self._hotkey_logger.exception("Failed to create hotkey service: %s", exc)
            return None

    def _ensure_background_threads(self) -> None:
        if self._worker_thread is None or not self._worker_thread.is_alive():
            self._worker_thread = threading.Thread(target=self._process_requests, daemon=True)
            self._worker_thread.start()
        if self._hotkey_dispatcher is None or not self._hotkey_dispatcher.is_alive():
            self._hotkey_dispatcher = threading.Thread(
                target=self._dispatch_hotkey_events,
                name="HotkeyDispatcher",
                daemon=True,
            )
            self._hotkey_dispatcher.start()

    def _dispatch_hotkey_events(self) -> None:
        while True:
            event = self._hotkey_event_queue.get()
            if event is None:
                break
            try:
                self._process_hotkey_event(event)
            except Exception as exc:  # pragma: no cover - logging runtime issues
                self._hotkey_logger.exception("Error while processing hotkey event: %s", exc)

    def _process_hotkey_event(self, event: HotkeyEvent) -> None:
        if event.name == "copy":
            self._handle_copy_event(timestamp=event.timestamp)
        elif event.name == "state_dump":
            self._handle_state_dump_event(timestamp=event.timestamp)
        else:
            self._hotkey_logger.debug("Unknown hotkey event: %s", event.name)

    def _handle_state_dump_event(self, *, timestamp: float) -> None:
        if self._state_dump_detector is not None and not self._state_dump_detector.register(
            timestamp=timestamp
        ):
            return
        bindings = []
        if self._hotkey_service is not None:
            bindings = list(self._hotkey_service.describe_bindings())
        self._hotkey_logger.info(
            "Hotkey state dump | bindings=%s | queue=%d | last_trigger=%.3f | restart_flag=%s",
            bindings,
            self._request_queue.qsize(),
            self._last_trigger_time,
            self._restart_event.is_set(),
        )

    def _toggle_language(self) -> None:
        with self._lock:
            if self.source_language and self.dest_language:
                self.source_language, self.dest_language = self.dest_language, self.source_language
            else:
                try:
                    current_index = self._language_options.index(self.dest_language)
                except ValueError:
                    current_index = -1
                next_index = (current_index + 1) % len(self._language_options)
                self.dest_language = self._language_options[next_index]
            self._window_manager.update_languages(self.source_language, self.dest_language)
            _save_dest_language(self.dest_language)
            last_text = self._last_original_text
            src = self.source_language
            dest = self.dest_language
        self._enqueue_retranslation(last_text, src, dest)

    def _set_dest_language(self, language: str) -> None:
        with self._lock:
            self.dest_language = language
            if language not in self._language_options:
                self._language_options.append(language)
            self._window_manager.update_languages(self.source_language, self.dest_language)
            _save_dest_language(self.dest_language)
            last_text = self._last_original_text
            src = self.source_language
            dest = self.dest_language
        self._enqueue_retranslation(last_text, src, dest)

    def _set_source_language(self, language: Optional[str]) -> None:
        with self._lock:
            self.source_language = language
            self._window_manager.update_languages(self.source_language, self.dest_language)
            last_text = self._last_original_text
            src = self.source_language
            dest = self.dest_language
        self._enqueue_retranslation(last_text, src, dest)

    def _handle_copy_event(self, *, timestamp: Optional[float] = None) -> None:
        with self._lock:
            if not self._copy_detector.register(timestamp=timestamp):
                return
            current_time = timestamp if timestamp is not None else self._time_provider()
            if current_time - self._last_trigger_time < self._min_trigger_interval:
                return
            self._last_trigger_time = current_time

        try:
            text = self._clipboard.paste()
        except Exception as exc:  # pragma: no cover - exercised via unit tests
            if pyperclip is not None and isinstance(exc, pyperclip.PyperclipException):
                message = f"Failed to read clipboard: {exc}"
            else:
                message = f"Unexpected error while accessing clipboard: {exc}"
            self._hotkey_logger.error(message)
            self._copy_detector.reset()
            return

        text = text.strip()
        if text:
            self._request_queue.put(
                TranslationRequest(text=text, src=self.source_language, dest=self.dest_language)
            )

    def _process_requests(self) -> None:
        while True:
            request = self._request_queue.get()
            try:
                self._process_single_request(request)
            finally:
                self._request_queue.task_done()

    def _process_single_request(self, request: TranslationRequest) -> None:
        with self._lock:
            self._last_original_text = request.text
        try:
            translation = self.translator.translate(request.text, src=request.src, dest=request.dest)
        except TranslationError as exc:  # pragma: no cover - network errors are runtime issues
            self._render_translation(
                request,
                f"Error during translation: {exc}",
                request.src,
            )
            return

        translated_text = getattr(translation, "text", str(translation))
        detected_source = getattr(translation, "detected_source", getattr(translation, "src", None))
        self._render_translation(request, translated_text, detected_source)

    def _render_translation(
        self,
        request: TranslationRequest,
        translated: str,
        detected_source: Optional[str],
    ) -> None:
        if self._display_callback is not None:
            self._display_callback(request.text, translated, detected_source)
        else:
            self._show_translation_window(
                request.text,
                translated,
                detected_source,
                reposition=request.reposition,
            )

    def _show_translation_window(
        self,
        original: str,
        translated: str,
        detected_source: Optional[str],
        *,
        reposition: bool = True,
    ) -> None:
        self._window_manager.show(original, translated, detected_source, reposition=reposition)

    def _enqueue_retranslation(
        self, text: Optional[str], src: Optional[str], dest: Optional[str]
    ) -> None:
        if not text or not dest:
            return
        self._request_queue.put(
            TranslationRequest(text=text, src=src, dest=dest, reposition=False)
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Translate selected text after a double Ctrl+C.")
    parser.add_argument(
        "--dest",
        default=_load_saved_dest_language(),
        help="Destination language (default: last saved or ja). Use Google Translate language codes.",
    )
    parser.add_argument(
        "--src",
        default=None,
        help="Source language. Leave empty to auto-detect.",
    )
    return parser.parse_args()


def main() -> None:
    try:
        with SingleInstanceGuard("cctranslationtool"):
            args = parse_args()
            _save_dest_language(args.dest)
            app = CCTranslationApp(dest_language=args.dest, source_language=args.src)
            tray_controller = SystemTrayController(app)
            app.start(tray_controller=tray_controller)
    except SingleInstanceError:
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo("CCTranslationTool", "CCTranslationToolは既に起動しています。")
        root.destroy()


if __name__ == "__main__":
    main()
