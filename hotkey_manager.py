"""Global hotkey management based on the Win32 RegisterHotKey API."""

from __future__ import annotations

import logging
import queue
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence


@dataclass(frozen=True)
class HotkeyBinding:
    """Represents a single hotkey registration."""

    name: str
    modifiers: int
    virtual_key: int
    display: str
    allow_repeat: bool = False


@dataclass(frozen=True)
class HotkeyEvent:
    """Event generated when a registered hotkey is triggered."""

    name: str
    timestamp: float


class BaseHotkeyService:
    """Protocol-like base class for hotkey backends."""

    def start(self) -> None:  # pragma: no cover - interface definition
        raise NotImplementedError

    def stop(self) -> None:  # pragma: no cover - interface definition
        raise NotImplementedError

    def describe_bindings(self) -> Sequence[str]:  # pragma: no cover - interface definition
        raise NotImplementedError


class RegisterHotKeyService(BaseHotkeyService):
    """Windows implementation that receives WM_HOTKEY messages."""

    _WM_APP_REREGISTER = 0x8000

    def __init__(
        self,
        bindings: Sequence[HotkeyBinding],
        event_queue: "queue.Queue[HotkeyEvent]",
        logger: logging.Logger,
        *,
        time_provider: Callable[[], float] = time.perf_counter,
        watchdog_interval: float = 5.0,
    ) -> None:
        if sys.platform != "win32":  # pragma: no cover - exercised on Windows
            raise RuntimeError("RegisterHotKeyService is only supported on Windows")

        self._bindings: List[HotkeyBinding] = list(bindings)
        self._event_queue = event_queue
        self._logger = logger
        self._time_provider = time_provider
        self._watchdog_interval = watchdog_interval

        self._thread: Optional[threading.Thread] = None
        self._hwnd: Optional[int] = None
        self._running = threading.Event()
        self._ready = threading.Event()
        self._last_watchdog = time.time()
        self._id_map: Dict[int, HotkeyBinding] = {}
        self._class_name = "CCTHotkeyWindow"
        self._class_registered = False
        self._session_registered = False

        # Loaded lazily to keep module importable on non-Windows systems.
        self._win32api = None
        self._win32con = None
        self._win32gui = None
        self._win32ts = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._running.set()
        self._ready.clear()
        self._thread = threading.Thread(target=self._run_message_loop, name="HotkeyThread", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=10):
            raise RuntimeError("Hotkey service failed to initialize within timeout")

    def stop(self) -> None:
        self._running.clear()
        if self._hwnd is not None and self._win32api is not None and self._win32con is not None:
            try:
                self._win32api.PostMessage(self._hwnd, self._win32con.WM_CLOSE, 0, 0)
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None
        self._hwnd = None
        self._ready.clear()

    def describe_bindings(self) -> Sequence[str]:
        return [f"{binding.name}: {binding.display}" for binding in self._bindings]

    def _load_win32_modules(self) -> None:
        if self._win32api is not None:
            return
        import win32api  # type: ignore
        import win32con  # type: ignore
        import win32gui  # type: ignore

        try:  # pragma: no cover - optional dependency
            import win32ts  # type: ignore
        except Exception:  # pragma: no cover - optional dependency missing
            win32ts = None  # type: ignore

        self._win32api = win32api
        self._win32con = win32con
        self._win32gui = win32gui
        self._win32ts = win32ts

    def _run_message_loop(self) -> None:
        try:
            self._load_win32_modules()
            assert self._win32api is not None
            assert self._win32con is not None
            assert self._win32gui is not None

            while self._running.is_set():
                try:
                    self._initialize_window()
                    self._ready.set()
                    self._pump_messages()
                except Exception as exc:  # pragma: no cover - watchdog ensures recovery
                    self._logger.exception("Hotkey loop crashed: %s", exc)
                    self._cleanup_window()
                    if not self._running.is_set():
                        break
                    time.sleep(1.0)
                    continue
                else:
                    break
        finally:
            self._cleanup_window()
            self._ready.set()
            self._running.clear()

    def _initialize_window(self) -> None:
        assert self._win32api is not None
        assert self._win32con is not None
        assert self._win32gui is not None

        hinstance = self._win32api.GetModuleHandle(None)
        class_name = self._class_name

        message_map = {
            self._win32con.WM_HOTKEY: self._on_hotkey,
            self._WM_APP_REREGISTER: self._on_reregister_request,
            self._win32con.WM_CLOSE: self._on_close,
            self._win32con.WM_DESTROY: self._on_destroy,
        }

        input_lang_change = getattr(self._win32con, "WM_INPUTLANGCHANGE", 0x0051)
        message_map[input_lang_change] = self._on_language_change

        power_broadcast = getattr(self._win32con, "WM_POWERBROADCAST", 0x0218)
        message_map[power_broadcast] = self._on_power_broadcast

        session_change = getattr(self._win32con, "WM_WTSSESSION_CHANGE", 0x02B1)
        if self._win32ts is not None:
            message_map[session_change] = self._on_session_change

        wndclass = self._win32gui.WNDCLASS()
        wndclass.hInstance = hinstance
        wndclass.lpszClassName = class_name
        wndclass.lpfnWndProc = message_map

        try:
            self._win32gui.RegisterClass(wndclass)
            self._class_registered = True
        except self._win32gui.error as exc:
            self._class_registered = False
            self._logger.debug("Hotkey window class registration skipped: %s", exc)

        style = 0
        ex_style = self._win32con.WS_EX_TOOLWINDOW
        self._hwnd = self._win32gui.CreateWindowEx(
            ex_style,
            class_name,
            class_name,
            style,
            0,
            0,
            0,
            0,
            0,
            0,
            hinstance,
            None,
        )

        if not self._hwnd:
            raise RuntimeError("Failed to create hidden hotkey window")

        self._register_session_notifications()
        self._register_hotkeys()
        self._last_watchdog = time.time()
        self._logger.info("Hotkey window created (hwnd=%s)", self._hwnd)

    def _cleanup_window(self) -> None:
        if self._win32api is None or self._win32con is None or self._win32gui is None:
            return

        if self._hwnd:
            if self._session_registered and self._win32ts is not None:
                try:  # pragma: no cover - Windows specific cleanup
                    self._win32ts.WTSUnRegisterSessionNotification(self._hwnd)
                except Exception:
                    pass
                finally:
                    self._session_registered = False

            for hotkey_id in list(self._id_map):
                try:
                    self._win32api.UnregisterHotKey(self._hwnd, hotkey_id)
                except Exception:
                    pass
            try:
                self._win32gui.DestroyWindow(self._hwnd)
            except Exception:
                pass
        self._hwnd = None
        self._id_map.clear()

        if self._class_registered:
            try:
                self._win32gui.UnregisterClass(self._class_name, None)
            except Exception:
                pass
            self._class_registered = False

    def _pump_messages(self) -> None:
        assert self._win32gui is not None
        assert self._win32con is not None

        while self._running.is_set():
            if self._hwnd is None:
                break
            msg = self._win32gui.GetMessage(self._hwnd, 0, 0)
            if msg is None:
                break
            if not msg[0]:
                break
            self._win32gui.TranslateMessage(msg)
            self._win32gui.DispatchMessage(msg)

            now = time.time()
            if now - self._last_watchdog >= self._watchdog_interval:
                self._logger.debug("Hotkey watchdog tick (hwnd=%s)", self._hwnd)
                self._last_watchdog = now

    def _register_hotkeys(self) -> None:
        assert self._hwnd is not None
        assert self._win32api is not None
        assert self._win32con is not None

        for hotkey_id, binding in enumerate(self._bindings, start=1):
            modifiers = binding.modifiers
            if not binding.allow_repeat:
                modifiers |= getattr(self._win32con, "MOD_NOREPEAT", 0)

            try:
                self._win32api.RegisterHotKey(self._hwnd, hotkey_id, modifiers, binding.virtual_key)
            except Exception as exc:
                self._logger.error(
                    "Failed to register hotkey %s (%s): %s",
                    binding.name,
                    binding.display,
                    exc,
                )
                continue

            self._id_map[hotkey_id] = binding
            self._logger.info("Registered hotkey '%s' as %s", binding.name, binding.display)

    def _register_session_notifications(self) -> None:
        if self._win32ts is None:
            return
        if self._hwnd is None:
            return
        try:  # pragma: no cover - Windows specific branch
            self._win32ts.WTSRegisterSessionNotification(self._hwnd, self._win32ts.NOTIFY_FOR_THIS_SESSION)
        except Exception as exc:  # pragma: no cover - optional feature
            self._logger.warning("Failed to register session notifications: %s", exc)
        else:
            self._session_registered = True
            self._logger.info("Registered for WTS session notifications")

    # Message handlers -------------------------------------------------

    def _on_hotkey(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        binding = self._id_map.get(wparam)
        if not binding:
            return 0
        timestamp = self._time_provider()
        try:
            self._event_queue.put_nowait(HotkeyEvent(binding.name, timestamp))
        except queue.Full:
            self._logger.warning("Dropping hotkey event for %s (queue full)", binding.name)
        return 0

    def _on_language_change(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        self._logger.info("Input language changed; scheduling hotkey re-registration")
        self._request_reregister()
        return 0

    def _on_power_broadcast(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        assert self._win32con is not None
        if wparam in (
            getattr(self._win32con, "PBT_APMRESUMEAUTOMATIC", 0),
            getattr(self._win32con, "PBT_APMRESUMESUSPEND", 0),
            getattr(self._win32con, "PBT_APMRESUMECRITICAL", 0),
        ):
            self._logger.info("Power resume detected; scheduling hotkey re-registration")
            self._request_reregister()
        return 1

    def _on_session_change(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        self._logger.info("Session change detected (code=%s); scheduling hotkey re-registration", wparam)
        self._request_reregister()
        return 0

    def _on_reregister_request(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        self._logger.info("Re-registering hotkeys")
        self._cleanup_window()
        if self._running.is_set():
            try:
                self._initialize_window()
            except Exception as exc:
                self._logger.exception("Failed to reinitialize hotkeys: %s", exc)
        return 0

    def _on_close(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        assert self._win32gui is not None
        self._win32gui.DestroyWindow(hwnd)
        return 0

    def _on_destroy(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        assert self._win32gui is not None
        self._win32gui.PostQuitMessage(0)
        return 0

    def _request_reregister(self) -> None:
        if self._hwnd is None or self._win32api is None:
            return
        try:
            self._win32api.PostMessage(self._hwnd, self._WM_APP_REREGISTER, 0, 0)
        except Exception:
            pass


def _vk_constant(win32con: object, name: str) -> Optional[int]:
    try:
        return getattr(win32con, name)
    except AttributeError:
        return None


def _parse_virtual_key(win32con: object, token: str) -> Optional[int]:
    upper = token.upper()
    if len(upper) == 1:
        if "A" <= upper <= "Z" or "0" <= upper <= "9":
            return ord(upper)
    if upper.startswith("F") and upper[1:].isdigit():
        value = _vk_constant(win32con, f"VK_{upper}")
        if value is not None:
            return value
    if upper.startswith("VK_"):
        value = _vk_constant(win32con, upper)
        if value is not None:
            return value
    alias_map = {
        "ESC": "VK_ESCAPE",
        "ESCAPE": "VK_ESCAPE",
        "TAB": "VK_TAB",
        "SPACE": "VK_SPACE",
        "ENTER": "VK_RETURN",
        "RETURN": "VK_RETURN",
        "BACKSPACE": "VK_BACK",
        "DELETE": "VK_DELETE",
        "HOME": "VK_HOME",
        "END": "VK_END",
        "PAGEUP": "VK_PRIOR",
        "PAGEDOWN": "VK_NEXT",
        "LEFT": "VK_LEFT",
        "RIGHT": "VK_RIGHT",
        "UP": "VK_UP",
        "DOWN": "VK_DOWN",
    }
    mapped = alias_map.get(upper)
    if mapped:
        value = _vk_constant(win32con, mapped)
        if value is not None:
            return value
    return None


def build_hotkey_binding(
    name: str,
    combo: str,
    *,
    allow_repeat: bool = False,
    win32con_module: Optional[object] = None,
) -> HotkeyBinding:
    """Create a :class:`HotkeyBinding` from a textual representation."""

    if win32con_module is None:
        if sys.platform != "win32":  # pragma: no cover - executed in Windows only
            raise RuntimeError("win32con module is required on Windows")
        import win32con  # type: ignore

        win32con_module = win32con

    parts = [part.strip() for part in combo.replace("-", "+").split("+") if part.strip()]
    if not parts:
        raise ValueError(f"Invalid hotkey definition: {combo!r}")

    modifiers = 0
    keys: List[str] = []

    for token in parts:
        normalized = token.lower()
        if normalized in {"ctrl", "control"}:
            modifiers |= win32con_module.MOD_CONTROL
            keys.append("Ctrl")
        elif normalized in {"alt"}:
            modifiers |= win32con_module.MOD_ALT
            keys.append("Alt")
        elif normalized in {"shift"}:
            modifiers |= win32con_module.MOD_SHIFT
            keys.append("Shift")
        elif normalized in {"win", "windows"}:
            modifiers |= win32con_module.MOD_WIN
            keys.append("Win")
        else:
            vk = _parse_virtual_key(win32con_module, token)
            if vk is None:
                raise ValueError(f"Unknown key token: {token!r}")
            virtual_key = vk
            keys.append(token.upper())

    if 'virtual_key' not in locals():
        raise ValueError(f"Hotkey combination is missing a non-modifier key: {combo!r}")

    display = "+".join(keys)
    return HotkeyBinding(
        name=name,
        modifiers=modifiers,
        virtual_key=virtual_key,
        display=display,
        allow_repeat=allow_repeat,
    )


def build_bindings_from_preferences(
    preferences: Dict[str, object],
    *,
    win32con_module: Optional[object] = None,
) -> List[HotkeyBinding]:
    hotkeys = preferences.get("hotkeys", {})
    if not isinstance(hotkeys, dict):
        hotkeys = {}

    copy_prefs = hotkeys.get("copy", {}) if isinstance(hotkeys.get("copy"), dict) else {}
    copy_combo = copy_prefs.get("combo") if isinstance(copy_prefs.get("combo"), str) else "Ctrl+C"

    state_dump_prefs = hotkeys.get("state_dump", {})
    if not isinstance(state_dump_prefs, dict):
        state_dump_prefs = {}
    dump_combo = (
        state_dump_prefs.get("combo")
        if isinstance(state_dump_prefs.get("combo"), str)
        else "F8"
    )

    bindings = [
        build_hotkey_binding("copy", copy_combo, win32con_module=win32con_module),
        build_hotkey_binding("state_dump", dump_combo, allow_repeat=True, win32con_module=win32con_module),
    ]
    return bindings

