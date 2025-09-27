"""Translation utilities for the CCTranslationTool application."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional


class TranslationError(RuntimeError):
    """Raised when the translation service cannot complete a request."""


@dataclass
class TranslationResult:
    text: str
    detected_source: Optional[str]


class GoogleTranslateClient:
    """Minimal client for the unofficial Google Translate web API."""

    endpoint = "https://translate.googleapis.com/translate_a/single"

    def __init__(self, timeout: float = 5.0) -> None:
        self.timeout = timeout

    def translate(self, text: str, src: Optional[str], dest: str) -> TranslationResult:
        if not text:
            raise TranslationError("Cannot translate empty text")

        params = {
            "client": "gtx",
            "dt": "t",
            "sl": (src or "auto"),
            "tl": dest,
            "q": text,
        }
        url = f"{self.endpoint}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = response.read()
        except urllib.error.URLError as exc:  # pragma: no cover - network errors are runtime issues
            raise TranslationError("Network error while contacting Google Translate") from exc

        try:
            data = json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError as exc:  # pragma: no cover - unexpected response is rare
            raise TranslationError("Invalid response from Google Translate") from exc

        try:
            segments = data[0]
        except (IndexError, TypeError) as exc:  # pragma: no cover - guards against API changes
            raise TranslationError("Unexpected translation response structure") from exc

        translated_text = "".join(part[0] for part in segments if part and part[0])
        detected_source = None
        if len(data) > 2:
            detected_source = data[2]

        return TranslationResult(text=translated_text, detected_source=detected_source)
