import io
import queue
import threading
import sys
import threading
import types
import unittest
import unittest.mock as mock
from contextlib import redirect_stdout
from types import SimpleNamespace

if "pystray" not in sys.modules:
    stub_pystray = types.ModuleType("pystray")

    class _StubIcon:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def run_detached(self) -> None:
            pass

        def stop(self) -> None:
            pass

    class _StubMenuItem:
        def __init__(self, *args, **kwargs) -> None:
            pass

    stub_pystray.Icon = _StubIcon
    stub_pystray.Menu = lambda *args, **kwargs: None
    stub_pystray.MenuItem = _StubMenuItem
    sys.modules["pystray"] = stub_pystray

from translator_app import CCTranslationApp, TranslationRequest, SystemTrayController
from translation_service import TranslationError


class FakeTime:
    def __init__(self) -> None:
        self._value = 0.0

    def advance(self, amount: float) -> None:
        self._value += amount

    def now(self) -> float:
        return self._value


class FakeClipboard:
    def __init__(self, text: str = "") -> None:
        self.text = text

    def paste(self) -> str:
        return self.text


class FakeKeyboard:
    class Listener:
        def __init__(self) -> None:
            self.listening = True
            self.start_calls = 0

        def start_if_necessary(self) -> None:
            self.start_calls += 1
            self.listening = True

    def __init__(self) -> None:
        self.registered = []
        self._handles: dict[int, tuple[tuple, dict]] = {}
        self.unhooked = False
        self.removed: list[int] = []
        self._next_handle = 1
        self._listener = FakeKeyboard.Listener()

    def add_hotkey(self, *args, **kwargs):  # pragma: no cover - only used in manual runs
        handle = self._next_handle
        self._next_handle += 1
        self.registered.append((args, kwargs))
        self._handles[handle] = (args, kwargs)
        return handle

    def remove_hotkey(self, handle: int) -> None:  # pragma: no cover - only used in manual runs
        self._handles.pop(handle, None)
        self.removed.append(handle)

    def wait(self):  # pragma: no cover - only used in manual runs
        raise RuntimeError("wait should not be called during tests")

    def unhook_all(self):  # pragma: no cover - only used in manual runs
        self.unhooked = True


class FakeTranslator:
    def __init__(self, translated: str = "こんにちは", detected: str = "en") -> None:
        self.calls = []
        self.translated = translated
        self.detected = detected

    def translate(self, text: str, src=None, dest=None):
        self.calls.append((text, src, dest))
        return SimpleNamespace(text=self.translated, detected_source=self.detected)


class ErroringTranslator:
    def translate(self, text: str, src=None, dest=None):
        raise TranslationError("boom")


class CCTranslationAppTestMixin:
    def _create_app(self, **overrides) -> CCTranslationApp:
        fake_time = overrides.pop("fake_time", None)
        if fake_time is None:
            fake_time = FakeTime()
        defaults = dict(
            dest_language="ja",
            source_language=None,
            translator_factory=lambda: FakeTranslator(),
            keyboard_module=FakeKeyboard(),
            clipboard_module=FakeClipboard("hello"),
            time_provider=fake_time.now,
            display_callback=lambda original, translated, detected: None,
            double_copy_interval=0.5,
        )
        defaults.update(overrides)
        app = CCTranslationApp(**defaults)
        app._fake_time = fake_time
        return app


class CCTranslationAppTests(CCTranslationAppTestMixin, unittest.TestCase):
    def test_single_copy_does_not_enqueue(self):
        app = self._create_app()
        app._handle_copy_event()
        self.assertTrue(app._request_queue.empty())

    def test_double_copy_enqueues_request(self):
        app = self._create_app()
        app._handle_copy_event()
        app._fake_time.advance(0.1)
        app._handle_copy_event()
        request = app._request_queue.get_nowait()
        self.assertEqual(request.text, "hello")
        self.assertEqual(request.dest, "ja")
        self.assertTrue(request.reposition)

    def test_double_copy_resets_after_interval(self):
        app = self._create_app()
        app._handle_copy_event()
        app._fake_time.advance(1.0)
        app._handle_copy_event()
        with self.assertRaises(queue.Empty):
            app._request_queue.get_nowait()

    def test_set_dest_language_retranslates_last_text(self):
        app = self._create_app()
        app._process_single_request(TranslationRequest(text="hello", src=None, dest="ja"))
        with mock.patch("translator_app._save_dest_language"):
            app._set_dest_language("en")
        request = app._request_queue.get_nowait()
        self.assertEqual(request.text, "hello")
        self.assertIsNone(request.src)
        self.assertEqual(request.dest, "en")
        self.assertFalse(request.reposition)

    def test_toggle_language_retranslates_last_text(self):
        app = self._create_app()
        app.source_language = "en"
        app.dest_language = "ja"
        app._process_single_request(TranslationRequest(text="こんにちは", src="en", dest="ja"))
        with mock.patch("translator_app._save_dest_language"):
            app._toggle_language()
        request = app._request_queue.get_nowait()
        self.assertEqual(request.text, "こんにちは")
        self.assertEqual(request.src, "ja")
        self.assertEqual(request.dest, "en")
        self.assertFalse(request.reposition)

    def test_set_source_language_retranslates_last_text(self):
        app = self._create_app()
        app.dest_language = "en"
        app._process_single_request(TranslationRequest(text="hello", src=None, dest="en"))
        app._set_source_language("ja")
        request = app._request_queue.get_nowait()
        self.assertEqual(request.text, "hello")
        self.assertEqual(request.src, "ja")
        self.assertEqual(request.dest, "en")
        self.assertFalse(request.reposition)

    def test_process_single_request_uses_translator(self):
        translator = FakeTranslator(translated="translated", detected="en")
        captured = []

        def capture(original, translated, detected):
            captured.append((original, translated, detected))

        app = self._create_app(translator_factory=lambda: translator, display_callback=capture)
        request = TranslationRequest(text="hello", src=None, dest="ja")
        app._process_single_request(request)

        self.assertEqual(translator.calls, [("hello", None, "ja")])
        self.assertEqual(captured, [("hello", "translated", "en")])

    def test_process_single_request_handles_errors(self):
        captured = []

        def capture(original, translated, detected):
            captured.append((original, translated, detected))

        app = self._create_app(translator_factory=lambda: ErroringTranslator(), display_callback=capture)
        request = TranslationRequest(text="hello", src="en", dest="ja")
        app._process_single_request(request)

        self.assertEqual(captured[0][0], "hello")
        self.assertIn("Error during translation", captured[0][1])
        self.assertEqual(captured[0][2], "en")

    def test_stop_sets_event(self):
        app = self._create_app()
        self.assertFalse(app._stop_event.is_set())
        app.stop()
        self.assertTrue(app._stop_event.is_set())

    def test_handle_copy_event_updates_last_copy_timestamp(self):
        fake_time = FakeTime()
        app = self._create_app(fake_time=fake_time)
        self.assertEqual(app._last_copy_timestamp, 0.0)
        app._handle_copy_event()
        self.assertEqual(app._last_copy_timestamp, fake_time.now())

    def test_keyboard_monitor_attempts_restart_when_listener_inactive(self):
        app = self._create_app()
        app._register_hotkeys(force=True)
        app._keyboard._listener.listening = False
        with mock.patch.object(app, "_register_hotkeys") as register_mock:
            register_mock.side_effect = lambda force=False: None
            failures, state = app._check_keyboard_listener(0)
        self.assertEqual(failures, 1)
        self.assertFalse(state)
        self.assertEqual(app._keyboard._listener.start_calls, 1)
        register_mock.assert_called_once_with(force=True)

    def test_keyboard_monitor_noop_when_hotkeys_inactive(self):
        app = self._create_app()
        app._hotkeys_active.clear()
        failures, state = app._check_keyboard_listener(2)
        self.assertEqual(failures, 0)
        self.assertTrue(state)
        self.assertEqual(app._keyboard._listener.start_calls, 0)

    def test_keyboard_status_logs_every_ten_seconds(self):
        app = self._create_app()
        app._register_hotkeys(force=True)
        with mock.patch("translator_app.logger.info") as info_mock:
            app._log_keyboard_status(True, 0)
            self.assertFalse(info_mock.called)
            app._fake_time.advance(10.1)
            app._log_keyboard_status(True, 0)
            info_mock.assert_called_once()

    def test_clipboard_error_does_not_enqueue_and_recovers(self):
        class LockedClipboard(FakeClipboard):
            def __init__(self) -> None:
                super().__init__("hello")
                self.locked = True

            def paste(self) -> str:
                if self.locked:
                    raise RuntimeError("clipboard locked")
                return super().paste()

        clipboard = LockedClipboard()
        app = self._create_app(clipboard_module=clipboard)

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            app._handle_copy_event()
            app._fake_time.advance(0.1)
            app._handle_copy_event()

        self.assertTrue(app._request_queue.empty())
        self.assertIn("clipboard", buffer.getvalue().lower())

        clipboard.locked = False
        app._fake_time.advance(0.1)
        app._handle_copy_event()
        app._fake_time.advance(0.1)
        app._handle_copy_event()

        request = app._request_queue.get_nowait()
        self.assertEqual(request.text, "hello")

    def test_translate_is_safe_during_reboot(self):
        translator = FakeTranslator(translated="こんにちは", detected="en")
        factory_started = threading.Event()
        allow_factory_to_finish = threading.Event()
        exceptions = []

        def blocking_factory() -> FakeTranslator:
            factory_started.set()
            allow_factory_to_finish.wait(timeout=1)
            return translator

        app = self._create_app(translator_factory=blocking_factory)

        def worker() -> None:
            try:
                app._process_single_request(TranslationRequest(text="hello", src=None, dest="ja"))
            except Exception as exc:  # pragma: no cover - failure captured in assertions
                exceptions.append(exc)

        worker_thread = threading.Thread(target=worker)
        worker_thread.start()

        self.assertTrue(factory_started.wait(timeout=1), "translator_factory was not invoked")

        reboot_thread = threading.Thread(target=app.reboot)
        reboot_thread.start()
        reboot_thread.join(0.01)
        self.assertTrue(
            reboot_thread.is_alive(),
            "reboot() should wait until translator initialization has finished",
        )

        allow_factory_to_finish.set()

        worker_thread.join()
        reboot_thread.join()

        if exceptions:
            raise exceptions[0]

        self.assertEqual(translator.calls, [("hello", None, "ja")])

    def test_reboot_resets_translator_and_requests_restart(self):
        translators = []

        def factory() -> FakeTranslator:
            translator = FakeTranslator(translated=f"translated-{len(translators)}")
            translators.append(translator)
            return translator

        app = self._create_app(translator_factory=factory)

        request = TranslationRequest(text="hello", src=None, dest="ja")
        app._process_single_request(request)
        self.assertEqual(len(translators), 1, "translator_factory should have been called once")

        app.reboot()

        self.assertTrue(app._restart_event.is_set())
        self.assertTrue(app._stop_event.is_set())

        app._restart_event.clear()
        app._stop_event.clear()

        app._process_single_request(request)
        self.assertEqual(len(translators), 2, "reboot should clear cached translator")
        self.assertIsNot(translators[0], translators[1])


class CCTranslationAppLifecycleTests(CCTranslationAppTestMixin, unittest.TestCase):
    def test_stop_after_reboot_exits_start_loop(self):
        app = self._create_app()

        ready = threading.Event()

        class FakeTrayController:
            def __init__(self) -> None:
                self.start_calls = 0
                self.stop_calls = 0

            def start(self) -> None:
                self.start_calls += 1
                ready.set()

            def stop(self) -> None:
                self.stop_calls += 1

        tray = FakeTrayController()

        thread = threading.Thread(target=lambda: app.start(tray_controller=tray))
        thread.start()
        try:
            self.assertTrue(ready.wait(timeout=1), "App did not reach running state in time")

            ready.clear()
            app.reboot()
            app.stop()

            thread.join(timeout=1)
            self.assertFalse(thread.is_alive(), "App should exit after stop following reboot")
        finally:
            app.stop()
            thread.join(timeout=1)


class SystemTrayControllerTests(unittest.TestCase):
    def test_reboot_menu_item_triggers_reboot_and_stops_icon(self) -> None:
        class DummyApp:
            def __init__(self) -> None:
                self.reboot_called = False

            def reboot(self) -> None:
                self.reboot_called = True

        class DummyIcon:
            def __init__(self) -> None:
                self.stopped = False

            def stop(self) -> None:
                self.stopped = True

        dummy_app = DummyApp()
        controller = SystemTrayController(dummy_app)
        icon = DummyIcon()

        controller._on_reboot(icon, None)

        self.assertTrue(dummy_app.reboot_called)
        self.assertTrue(icon.stopped)


if __name__ == "__main__":
    unittest.main()
