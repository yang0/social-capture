from __future__ import annotations

import inspect
import unittest
from unittest.mock import patch

from social_capture import console


class ConsoleTests(unittest.TestCase):
    def test_windows_code_page_contract(self):
        source = inspect.getsource(console._set_windows_code_page)
        self.assertIn("SetConsoleOutputCP", source)
        self.assertIn("SetConsoleCP", source)
        self.assertIn("65001", source)

    def test_windows_setup_calls_code_page_and_reconfigures_streams(self):
        with (
            patch.object(console, "_set_windows_code_page", return_value=True) as set_page,
            patch.object(console, "os") as fake_os,
        ):
            fake_os.name = "nt"
            stream = type("Stream", (), {"reconfigure": lambda self, **kwargs: setattr(self, "kwargs", kwargs)})()
            with patch.object(console.sys, "stdout", stream), patch.object(console.sys, "stderr", stream):
                console.configure_utf8_console()
        set_page.assert_called_once_with()
        self.assertEqual(stream.kwargs, {"encoding": "utf-8", "errors": "replace"})


if __name__ == "__main__":
    unittest.main()
