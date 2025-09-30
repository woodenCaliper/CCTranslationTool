import socket
import unittest
import unittest.mock as mock

from translation_service import GoogleTranslateClient, TranslationError


class GoogleTranslateClientTests(unittest.TestCase):
    def test_translate_timeout_raises_translation_error(self) -> None:
        client = GoogleTranslateClient(timeout=0.01)

        with mock.patch("urllib.request.urlopen", side_effect=socket.timeout):
            with self.assertRaises(TranslationError) as ctx:
                client.translate("hello", src="en", dest="ja")

        self.assertIn("timed out", str(ctx.exception))


if __name__ == "__main__":  # pragma: no cover - allows direct execution
    unittest.main()
