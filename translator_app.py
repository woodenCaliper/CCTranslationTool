"""Desktop utility to translate copied text via Google Translate."""

from __future__ import annotations

import argparse
import queue
import threading
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Callable, Optional, Protocol

try:  # pragma: no cover - executed during module import
    import keyboard  # type: ignore
except ImportError:  # pragma: no cover - handled in __init__
    keyboard = None  # type: ignore

try:  # pragma: no cover - executed during module import
    import pyperclip  # type: ignore
except ImportError:  # pragma: no cover - handled in __init__
    pyperclip = None  # type: ignore

try:
    import tkinter as tk
    from tkinter import scrolledtext
except ImportError as exc:  # pragma: no cover - tkinter is part of stdlib on Windows
    raise SystemExit("tkinter is required to display the translation window") from exc

from translation_service import GoogleTranslateClient, TranslationError, TranslationResult


DOUBLE_COPY_INTERVAL = 0.5  # Seconds allowed between two copy events.


@dataclass
class TranslationRequest:
    text: str
    src: Optional[str]
    dest: str


class TranslatorProtocol(Protocol):  # pragma: no cover - protocol is for type checking only
    def translate(self, text: str, src: Optional[str], dest: str) -> TranslationResult | SimpleNamespace:
        """Translate text and return a result object."""


@dataclass
class DoubleCopyDetector:
    """Utility that tracks consecutive copy events within a time window."""

    interval: float
    now: Callable[[], float]
    _last_time: float = field(default=0.0, init=False)
    _count: int = field(default=0, init=False)

    def register(self) -> bool:
        """Register a copy event.

        Returns ``True`` if the event completes a "double copy" sequence.
        """

        current = self.now()
        if current - self._last_time <= self.interval:
            self._count += 1
        else:
            self._count = 1
        self._last_time = current

        if self._count >= 2:
            self._count = 0
            return True
        return False


class CCTranslationApp:
    """Listens for double Ctrl+C and shows the translated text."""

    def __init__(
        self,
        dest_language: str,
        source_language: Optional[str] = None,
        *,
        translator_factory: Callable[[], TranslatorProtocol] = GoogleTranslateClient,
        keyboard_module=keyboard,
        clipboard_module=pyperclip,
        time_provider: Callable[[], float] = time.time,
        display_callback: Optional[Callable[[str, str, Optional[str]], None]] = None,
        double_copy_interval: float = DOUBLE_COPY_INTERVAL,
    ) -> None:
        self.dest_language = dest_language
        self.source_language = source_language
        self._translator: Optional[TranslatorProtocol] = None
        self._translator_factory = translator_factory
        self._copy_detector = DoubleCopyDetector(double_copy_interval, time_provider)
        self._lock = threading.Lock()
        self._request_queue: "queue.Queue[TranslationRequest]" = queue.Queue()
        if keyboard_module is None:
            raise RuntimeError(
                "The 'keyboard' package is required. Install it with 'pip install keyboard'."
            )
        if clipboard_module is None:
            raise RuntimeError(
                "The 'pyperclip' package is required. Install it with 'pip install pyperclip'."
            )
        self._keyboard = keyboard_module
        self._clipboard = clipboard_module
        self._display_callback = display_callback

    @property
    def translator(self) -> TranslatorProtocol:
        if self._translator is None:
            self._translator = self._translator_factory()
        return self._translator

    def start(self) -> None:
        """Start listening for keyboard events and processing translations."""

        self._keyboard.add_hotkey("ctrl+c", self._handle_copy_event, suppress=False)

        worker = threading.Thread(target=self._process_requests, daemon=True)
        worker.start()

        print("CCTranslationTool is running. Double press Ctrl+C on selected text to translate.")
        try:
            self._keyboard.wait()  # Blocks forever until keyboard is interrupted (Ctrl+C in console).
        finally:
            self._keyboard.unhook_all()

    def _handle_copy_event(self) -> None:
        with self._lock:
            if self._copy_detector.register():
                text = self._clipboard.paste().strip()
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
        try:
            translation = self.translator.translate(request.text, src=request.src, dest=request.dest)
        except TranslationError as exc:  # pragma: no cover - network errors are runtime issues
            self._render_translation(request.text, f"Error during translation: {exc}", request.src)
            return

        translated_text = getattr(translation, "text", str(translation))
        detected_source = getattr(translation, "detected_source", getattr(translation, "src", None))
        self._render_translation(request.text, translated_text, detected_source)

    def _render_translation(self, original: str, translated: str, detected_source: Optional[str]) -> None:
        if self._display_callback is not None:
            self._display_callback(original, translated, detected_source)
        else:
            self._show_translation_window(original, translated, detected_source)

    def _show_translation_window(self, original: str, translated: str, detected_source: Optional[str]) -> None:
        def runner() -> None:
            window = tk.Tk()
            window.title("CCTranslationTool")
            window.geometry("500x400")
            window.lift()
            window.attributes("-topmost", True)
            window.focus_force()

            header = tk.Label(
                window,
                text=f"Detected source: {detected_source or self.source_language or 'auto'} → {self.dest_language}",
                font=("Segoe UI", 12, "bold"),
                wraplength=480,
            )
            header.pack(pady=(10, 5))

            original_label = tk.Label(window, text="Original", font=("Segoe UI", 10, "bold"))
            original_label.pack(anchor="w", padx=10)

            original_box = scrolledtext.ScrolledText(window, wrap=tk.WORD, height=8)
            original_box.insert(tk.END, original)
            original_box.configure(state=tk.DISABLED)
            original_box.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

            translated_label = tk.Label(window, text="Translated", font=("Segoe UI", 10, "bold"))
            translated_label.pack(anchor="w", padx=10)

            translated_box = scrolledtext.ScrolledText(window, wrap=tk.WORD, height=8)
            translated_box.insert(tk.END, translated)
            translated_box.configure(state=tk.DISABLED)
            translated_box.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

            close_button = tk.Button(window, text="Close", command=window.destroy)
            close_button.pack(pady=(0, 10))

            window.mainloop()

        threading.Thread(target=runner, daemon=True).start()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Translate selected text after a double Ctrl+C.")
    parser.add_argument(
        "--dest",
        default="ja",
        help="Destination language (default: ja). Use Google Translate language codes.",
    )
    parser.add_argument(
        "--src",
        default=None,
        help="Source language. Leave empty to auto-detect.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    app = CCTranslationApp(dest_language=args.dest, source_language=args.src)
    app.start()


if __name__ == "__main__":
    main()
