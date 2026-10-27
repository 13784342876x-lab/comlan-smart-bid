from __future__ import annotations

import argparse
from pathlib import Path

from app.web.api import create_app


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Execute one persisted, lineage-bound Stage9 Writing Task."
    )
    parser.add_argument("--outputs-root", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--generation-id", required=True)
    args = parser.parse_args()

    app = create_app(Path(args.outputs_root).expanduser().resolve())
    app.state.writing_generation_service.execute(
        args.project_id,
        args.generation_id,
    )


if __name__ == "__main__":
    main()
