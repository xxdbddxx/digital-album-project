import subprocess
import sys
import unittest
from pathlib import Path


class VoiceImportPathTests(unittest.TestCase):
    def test_backend_package_imports_work_when_started_from_backend_dir(self):
        repo_root = Path(__file__).resolve().parents[2]
        backend_dir = repo_root / "backend"
        probe = (
            "import runpy, sys; "
            "state = runpy.run_path('services/voice_server.py', run_name='voice_server_probe'); "
            "import backend.services.emotion.models; "
            "print('ok')"
        )

        result = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=backend_dir,
            text=True,
            capture_output=True,
            timeout=20,
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
