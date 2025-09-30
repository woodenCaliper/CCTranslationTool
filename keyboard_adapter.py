"""Keyboard event handling powered by pyWinhook."""

from __future__ import annotations

import sys
import threading
import time
from typing import Callable, Dict, Optional, Tuple

try:
    import win32api  # type: ignore
    import win32con  # type: ignore
except Exception:  # pragma: no cover - optional dependency on non-Windows platforms
    win32api = None  # type: ignore
    win32con = None  # type: ignore

try:
    import pyWinhook  # type: ignore
except Exception:  # pragma: no cover - optional dependency on non-Windows platforms
    pyWinhook = None  # type: ignore

try:
    import pythoncom  # type: ignore
except Exception:  # pragma: no cover - optional dependency on non-Windows platforms
    pythoncom = None  # type: ignore


Hotkey = Tuple[str, ...]
Callback = Callable[[], None]


def create_keyboard_listener() -> Optional["PyWinhookKeyboardAdapter"]:
    """Create a keyboard listener backed by pyWinhook if supported."""

    if sys.platform != "win32":  # pragma: no cover - Windows specific functionality
        return None
    if pyWinhook is None or pythoncom is None:
        return None
    return PyWinhookKeyboardAdapter()


class PyWinhookKeyboardAdapter:
    """Minimal adapter that provides a subset of the keyboard module API."""

    def __init__(self) -> None:
        if pyWinhook is None or pythoncom is None:  # pragma: no cover - guarded by factory
            raise RuntimeError("pyWinhook is not available")

        self._stop_event = threading.Event()
        self._pressed: Dict[str, int] = {}
        self._callbacks: Dict[Hotkey, Callback] = {}
        self._lock = threading.Lock()
        self._ready_event = threading.Event()
        self._thread_id: Optional[int] = None
        self._hook_manager: Optional["pyWinhook.HookManager"] = None

        self._pump_thread = threading.Thread(
            target=self._run_message_loop, name="PyWinhookKeyboard", daemon=True
        )
        self._pump_thread.start()

        if not self._ready_event.wait(timeout=2.0):
            raise RuntimeError("pyWinhook keyboard hook failed to initialise")

    def add_hotkey(
        self, hotkey: str, callback: Callback, suppress: bool = False
    ) -> None:
        """Register a hotkey callback.

        Only a subset of the keyboard module interface is implemented. The
        ``suppress`` argument is accepted for compatibility but ignored.
        """

        del suppress  # Unused argument kept for signature compatibility

        normalized = self._parse_hotkey(hotkey)
        if not normalized:
            raise ValueError(f"Unsupported hotkey: {hotkey}")

        with self._lock:
            self._callbacks[normalized] = callback

    def unhook_all(self) -> None:
        """Remove all registered hotkeys and stop the hook."""

        with self._lock:
            self._callbacks.clear()
            self._pressed.clear()

        if not self._stop_event.is_set():
            self._stop_event.set()
            self._wake_message_loop()
            self._ready_event.wait(timeout=0.5)
            if self._pump_thread.is_alive():
                self._pump_thread.join(timeout=1.0)

    # Internal helpers -------------------------------------------------

    def _run_message_loop(self) -> None:
        if pythoncom is None or pyWinhook is None:  # pragma: no cover - guarded by __init__
            return

        try:
            pythoncom.CoInitialize()
        except Exception:  # pragma: no cover - defensive fallback
            self._ready_event.set()
            return

        try:
            hook_manager = pyWinhook.HookManager()
            hook_manager.KeyDown = self._on_key_down
            hook_manager.KeyUp = self._on_key_up
            hook_manager.HookKeyboard()

            self._hook_manager = hook_manager
            if win32api is not None:  # pragma: no branch - platform specific
                try:
                    self._thread_id = win32api.GetCurrentThreadId()  # type: ignore[attr-defined]
                except Exception:  # pragma: no cover - defensive fallback
                    self._thread_id = None
            self._ready_event.set()

            while not self._stop_event.is_set():
                try:
                    pythoncom.PumpWaitingMessages()
                except pythoncom.com_error:  # pragma: no cover - defensive cleanup
                    break
                time.sleep(0.01)
        finally:
            try:
                if self._hook_manager is not None:
                    self._hook_manager.UnhookKeyboard()
            except Exception:  # pragma: no cover - defensive cleanup
                pass
            self._hook_manager = None
            self._thread_id = None
            self._ready_event.set()
            try:
                pythoncom.CoUninitialize()
            except Exception:  # pragma: no cover - defensive cleanup
                pass

    def _wake_message_loop(self) -> None:
        thread_id = self._thread_id
        if thread_id is None or win32api is None or win32con is None:  # pragma: no cover - fallback
            return
        try:
            win32api.PostThreadMessage(thread_id, win32con.WM_NULL, 0, 0)
        except Exception:  # pragma: no cover - defensive fallback
            pass

    def _on_key_down(self, event: "pyWinhook.KeyboardEvent") -> bool:
        key = self._normalize_event_key(event)
        if key is None:
            return True

        with self._lock:
            self._pressed[key] = self._pressed.get(key, 0) + 1
            callbacks = list(self._callbacks.items())

        for combo, callback in callbacks:
            if self._is_combo_triggered(combo, key):
                callback()

        return True

    def _on_key_up(self, event: "pyWinhook.KeyboardEvent") -> bool:
        key = self._normalize_event_key(event)
        if key is None:
            return True

        with self._lock:
            count = self._pressed.get(key, 0)
            if count <= 1:
                self._pressed.pop(key, None)
            else:
                self._pressed[key] = count - 1

        return True

    def _is_combo_triggered(self, combo: Hotkey, key: str) -> bool:
        if combo[-1] != key:
            return False

        with self._lock:
            pressed_keys = set(self._pressed.keys())

        return all(token in pressed_keys for token in combo)

    @staticmethod
    def _parse_hotkey(hotkey: str) -> Hotkey:
        parts = [part.strip().lower() for part in hotkey.split("+") if part.strip()]
        normalized = [PyWinhookKeyboardAdapter._normalize_token(part) for part in parts]
        if any(token is None for token in normalized):
            return ()
        return tuple(token for token in normalized if token is not None)

    @staticmethod
    def _normalize_event_key(event: "pyWinhook.KeyboardEvent") -> Optional[str]:
        key_name = getattr(event, "Key", "")
        if not key_name:
            return None
        return PyWinhookKeyboardAdapter._normalize_token(key_name)

    @staticmethod
    def _normalize_token(token: str) -> Optional[str]:
        token = token.lower()
        if token in {"lcontrol", "rcontrol", "control", "ctrl"}:
            return "ctrl"
        if token in {"lshift", "rshift", "shift"}:
            return "shift"
        if token in {"lmenu", "rmenu", "menu", "alt"}:
            return "alt"
        if len(token) == 1:
            return token
        return token

