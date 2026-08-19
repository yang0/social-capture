"""Console encoding setup for Windows terminals."""

from __future__ import annotations

import ctypes
import os
import sys
from typing import TextIO


def _set_windows_code_page() -> bool:
    """Set the native console code page when a Windows console is attached."""

    if os.name != "nt":
        return False
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        output_ok = bool(kernel32.SetConsoleOutputCP(65001))
        input_ok = bool(kernel32.SetConsoleCP(65001))
        return output_ok and input_ok
    except (AttributeError, OSError):
        # Some embedded Python hosts do not expose kernel32; stream
        # reconfiguration below still gives pipes deterministic UTF-8 bytes.
        return False


def _configure(stream: TextIO) -> None:
    if os.name != "nt":
        return
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure:
        reconfigure(encoding="utf-8", errors="replace")


def configure_utf8_console() -> None:
    """Use UTF-8 for Windows consoles and redirected CLI output.

    ``SetConsole*`` only affects an attached console; it has no effect on a
    pipe. Reconfiguring real TextIOWrapper streams also makes JSON redirected
    to a file/pipe UTF-8. StringIO test captures and non-Windows streams are
    left untouched.
    """

    _set_windows_code_page()
    _configure(sys.stdout)
    _configure(sys.stderr)
