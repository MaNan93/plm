"""Exercise the bundled renderer in an actual Qt WebEngine subprocess."""

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest


class MermaidRenderingTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("PyQt5.QtWebEngineWidgets"), "PyQtWebEngine is optional")
    def test_offline_rendering_zoom_latest_revision_and_errors(self):
        environment = dict(os.environ, QTWEBENGINE_CHROMIUM_FLAGS=(
            "--disable-gpu --disable-background-timer-throttling --disable-renderer-backgrounding"
        ))
        # Windows' offscreen Qt plugin crashes when creating a Chromium view.
        # The probe uses the native platform without showing any windows.
        if sys.platform == "win32":
            environment["QT_QPA_PLATFORM"] = "windows"
        probe = Path(__file__).with_name("mermaid_render_probe.py")
        result = subprocess.run(
            [sys.executable, str(probe)], env=environment,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            # The complete matrix now checks numeric ordering as well as
            # routing; some graphs require a second ELK routing pass.
            encoding="utf-8", errors="replace", timeout=300,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("OFFLINE_RENDER_PASS", result.stdout)
