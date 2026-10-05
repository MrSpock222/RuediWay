"""Small, synchronous CLI adapter; no shell interpolation."""
import os
from pathlib import Path
import subprocess


def analyze_image(image: Path, prompt: str, output: Path) -> str:
    return analyze_images([image], prompt, output)


def analyze_images(images: list[Path], prompt: str, output: Path) -> str:
    if not 1 <= len(images) <= 8:
        raise ValueError("Ein bis acht Analysebilder erforderlich.")
    command = [
        os.environ.get("CODEX_BIN", "codex"),
        "exec", "--ignore-user-config", "--ephemeral",
        "--model", "gpt-6-luna", "--config", 'model_reasoning_effort="medium"',
        "--sandbox", "read-only", "--skip-git-repo-check",
        "--color", "never", "--output-last-message", str(output),
    ]
    for image in images:
        command.extend(["--image", str(image)])
    command.append("-")
    try:
        result = subprocess.run(
            command, input=prompt, text=True, encoding="utf-8",
            errors="replace", capture_output=True, cwd=images[0].parent,
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
