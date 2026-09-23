"""Small, synchronous CLI adapter; no shell interpolation."""
import os
from pathlib import Path
import subprocess


def analyze_image(image: Path, prompt: str, output: Path) -> str:
    command = [
        os.environ.get("CODEX_BIN", "codex"),
        "exec", "--ignore-user-config", "--ephemeral",
        "--model", "gpt-6-luna", "--config", 'model_reasoning_effort="medium"',
        "--sandbox", "read-only", "--skip-git-repo-check",
        "--color", "never", "--output-last-message", str(output),
        "--image", str(image), "-",
    ]
    try:
        result = subprocess.run(
            command, input=prompt, text=True, encoding="utf-8",
            errors="replace", capture_output=True, cwd=image.parent,
            timeout=int(os.environ.get("RUEDIWAY_TIMEOUT", "120")),
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Codex CLI fehlt. CODEX_BIN oder PATH prüfen.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Codex hat das Zeitlimit überschritten.") from exc
    if result.returncode:
        raise RuntimeError("Codex-Anfrage fehlgeschlagen. Anmeldung und Kontingent am PC prüfen.")
    answer = output.read_text(encoding="utf-8").strip() if output.exists() else ""
    if not answer:
        raise RuntimeError("Codex hat keine Textantwort geliefert.")
    return answer
