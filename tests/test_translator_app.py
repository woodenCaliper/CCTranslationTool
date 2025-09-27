import queue
import unittest
from types import SimpleNamespace

from translator_app import CCTranslationApp, TranslationRequest
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
    def __init__(self) -> None:
        self.registered = []
        self.unhooked = False

    def add_hotkey(self, *args, **kwargs):  # pragma: no cover - only used in manual runs
        self.registered.append((args, kwargs))

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


class CCTranslationAppTests(unittest.TestCase):
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

    def test_double_copy_resets_after_interval(self):
        app = self._create_app()
        app._handle_copy_event()
        app._fake_time.advance(1.0)
        app._handle_copy_event()
        with self.assertRaises(queue.Empty):
            app._request_queue.get_nowait()

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

    def test_reboot_resets_internal_state(self):
        translator = FakeTranslator()
        app = self._create_app(translator_factory=lambda: translator)
        _ = app.translator  # instantiate translator instance

        app._handle_copy_event()
        app._fake_time.advance(0.1)
        app._handle_copy_event()
        request = app._request_queue.get_nowait()
        self.assertEqual(request.text, "hello")

        app.reboot()

        self.assertTrue(app._restart_event.is_set())
        self.assertTrue(app._stop_event.is_set())
        self.assertIsNone(app._translator)
        self.assertTrue(app._request_queue.empty())

        app._restart_event.clear()
        app._stop_event.clear()

        app._handle_copy_event()
        app._fake_time.advance(0.1)
        app._handle_copy_event()
        request_after_reboot = app._request_queue.get_nowait()
        self.assertEqual(request_after_reboot.text, "hello")


if __name__ == "__main__":
    unittest.main()
