"""Enforce the source-file weight policy without treating line count as design quality."""

from __future__ import annotations

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = (REPOSITORY_ROOT / "backend" / "app", REPOSITORY_ROOT / "frontend" / "src")
SOURCE_SUFFIXES = {".py", ".ts", ".vue", ".css"}
REVIEW_THRESHOLD = 700
HARD_LIMIT = 1000
TEMPORARY_LIMITS = {
    "frontend/src/styles.css": 5084,
}


def source_files() -> list[Path]:
    """Return hand-maintained product source files in stable path order."""
    files: list[Path] = []
    for root in SOURCE_ROOTS:
        files.extend(
            path
            for path in root.rglob("*")
            if path.is_file()
            and path.suffix in SOURCE_SUFFIXES
            and "generated" not in path.relative_to(REPOSITORY_ROOT).parts
        )
    return sorted(files)


def line_count(path: Path) -> int:
    """Count logical text lines using the repository UTF-8 convention."""
    with path.open(encoding="utf-8") as source:
        return sum(1 for _line in source)


def main() -> int:
    """Report review warnings and fail only on a hard or ratcheted limit."""
    failures: list[str] = []
    warnings: list[str] = []
    seen_temporary_limits: set[str] = set()

    for path in source_files():
        relative = path.relative_to(REPOSITORY_ROOT).as_posix()
        count = line_count(path)
        allowed = TEMPORARY_LIMITS.get(relative, HARD_LIMIT)
        if relative in TEMPORARY_LIMITS:
            seen_temporary_limits.add(relative)
        if count > allowed:
            failures.append(f"{relative}: {count} lines exceeds allowed {allowed}")
        elif count > REVIEW_THRESHOLD:
            warnings.append(f"{relative}: {count} lines requires responsibility review")

    missing = sorted(set(TEMPORARY_LIMITS) - seen_temporary_limits)
    if missing:
        failures.extend(f"remove obsolete temporary limit for {path}" for path in missing)

    for warning in warnings:
        print(f"WARNING: {warning}")
    for failure in failures:
        print(f"ERROR: {failure}")
    if failures:
        return 1
    print(f"Source-size policy passed ({len(warnings)} files require responsibility review).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
