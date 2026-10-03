"""Portable demo cache cleanup; also usable when Make is unavailable."""
from pathlib import Path
import shutil
import sys


def clean(root=None):
    root = Path(root or Path(__file__).resolve().parents[1]).resolve()
    target = root / ".cache" / "stopslop-demo"
    if target.is_symlink() or not target.resolve().is_relative_to(root):
        raise ValueError("Refusing to delete a cache outside the project")
    if target.exists():
        shutil.rmtree(target)
    print("Demo cache cleared. The next scenario reruns policies and regenerates replies.")


if __name__ == "__main__":
    if sys.argv[1:] != ["clean"]:
        raise SystemExit("Usage: python tools/demo_tasks.py clean")
    clean()
