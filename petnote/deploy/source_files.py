"""Deterministic application source files shared by deployment packages."""
from pathlib import Path


def safe_source_file(root, relative):
    """Reject traversal, links and junctions before copying a source file."""
    root = Path(root).resolve()
    relative = Path(relative)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ValueError("Source paths must stay inside the application directory.")
    path = root
    for part in relative.parts:
        path /= part
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise ValueError(f"Unexpected link in application source: {relative}")
    if path.resolve() != root / relative:
        raise ValueError(f"Unexpected link in application source: {relative}")
    if not path.is_file():
        raise ValueError(f"Required application source is missing: {relative}")
    return path


def application_files(root):
    root = Path(root).resolve()
    suffixes = {
        "config": {".py"},
        "care": {".py"},
        "templates": {".html"},
        "static": {".js", ".css", ".svg", ".woff2", ".png", ".jpg", ".webp", ".ico"},
    }
    extras = {
        Path("care/data/breeds.json"),
        Path("static/care/fonts/Manrope-OFL.txt"),
        Path("static/care/fonts/Pretendard-OFL.txt"),
        Path("static/care/fonts/README.md"),
    }
    selected = []
    for folder, allowed in suffixes.items():
        directory = root / folder
        if directory.is_symlink() or getattr(directory, "is_junction", lambda: False)():
            raise ValueError(f"Unexpected link in application source: {folder}")
        for path in directory.rglob("*"):
            relative = path.relative_to(root)
            if any(part.startswith(".") or part in {"__pycache__", "tests"} for part in relative.parts):
                continue
            if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                raise ValueError(f"Unexpected link in application source: {relative}")
            if path.is_file() and (path.suffix.lower() in allowed or relative in extras):
                safe_source_file(root, relative)
                selected.append(relative)
    yield from sorted(selected, key=lambda value: value.as_posix())
