"""Delete disposable runtime state inside the workspace, preserving configuration."""
import os
from pathlib import Path
from dotenv import load_dotenv


def clean(root):
    root = Path(root).resolve()
    load_dotenv(root / ".env", override=False)
    configured = Path(os.getenv("STOPSLOP_STATE_FILE", "stopslop.sqlite3"))
    configured = (root / configured).resolve()
    if not configured.is_relative_to(root):
        raise ValueError("STOPSLOP_STATE_FILE must be inside the workspace for just clean")
    if configured.suffix not in {".sqlite3", ".sqlite", ".db"}:
        raise ValueError("Configured state file must have a SQLite database extension")
    session_dirs = []
    protected = {".git", ".venv", ".uv-cache", ".codex", ".agents", "node_modules"}
    targets = {configured, *(Path(str(configured) + suffix) for suffix in ("-journal", "-wal", "-shm"))}
    disposable = {"metrics.json", "stopslop.log", "incidents.jsonl", "policy.dyn.json", "policy.dyn.toml"}
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if name not in protected and not (Path(directory) / name).is_symlink()]
        folder = Path(directory)
        if folder.name.endswith(".json.sessions"):
            session_dirs.append(folder)
        legacy_sessions = any(parent.name.endswith(".json.sessions") for parent in (folder, *folder.relative_to(root).parents))
        for name in files:
            path = folder / name
            if name in disposable or name.endswith((".sqlite3", ".sqlite3-journal", ".sqlite3-wal", ".sqlite3-shm")) or legacy_sessions:
                targets.add(path)
    for path in targets:
        if not path.resolve().is_relative_to(root) or any(part in protected for part in path.relative_to(root).parts):
            raise ValueError(f"Refusing to remove protected or external state: {path}")
    removed = 0
    for path in sorted(targets):
        if path.is_file() or path.is_symlink():
            path.unlink()
            print(f"Removed {path.relative_to(root)}")
            removed += 1
    for path in sorted(session_dirs, key=lambda path: len(path.parts), reverse=True):
        if not any(path.iterdir()):
            path.rmdir()
    print(f"Removed {removed} state files.")


if __name__ == "__main__":
    clean(Path(__file__).resolve().parents[1])
