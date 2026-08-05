"""Export the FastAPI contract used by the frontend type generator.

The export is deliberately deterministic so a generated-client drift check can
compare the committed artifact without depending on process or dictionary order.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
DEFAULT_OUTPUT = REPOSITORY_ROOT / "frontend" / "src" / "api" / "generated" / "openapi.json"


def export_openapi(output: Path) -> None:
    """Write the current FastAPI OpenAPI document to ``output``."""
    sys.path.insert(0, str(BACKEND_ROOT))
    from app.main import app

    document = app.openapi()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="OpenAPI JSON destination (defaults to the frontend generated directory).",
    )
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else REPOSITORY_ROOT / args.output
    export_openapi(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
