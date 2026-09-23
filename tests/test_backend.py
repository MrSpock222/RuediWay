import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app.backend import analyze_image


class BackendTests(unittest.TestCase):
    def test_returns_only_final_answer_and_attaches_image(self):
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "photo.jpg"
            output = Path(folder) / "answer.txt"
            output.write_text("42", encoding="utf-8")
            with patch("app.backend.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "progress", "")) as run:
                self.assertEqual(analyze_image(image, "prompt", output), "42")
                self.assertIn(str(image), run.call_args.args[0])
                self.assertIn("read-only", run.call_args.args[0])
                command = run.call_args.args[0]
                self.assertEqual(command[command.index("--model") + 1], "gpt-6-luna")
                self.assertEqual(command[command.index("--config") + 1], 'model_reasoning_effort="medium"')
                self.assertEqual(run.call_args.kwargs["input"], "prompt")

    def test_timeout_is_readable(self):
        with patch("app.backend.subprocess.run", side_effect=subprocess.TimeoutExpired("codex", 120)):
            with self.assertRaisesRegex(RuntimeError, "Zeitlimit"):
                analyze_image(Path("image.jpg"), "prompt", Path("answer.txt"))

    def test_failure_does_not_return_diagnostics_as_answer(self):
        with patch("app.backend.subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "private diagnostics")):
            with self.assertRaisesRegex(RuntimeError, "fehlgeschlagen"):
                analyze_image(Path("image.jpg"), "prompt", Path("answer.txt"))
