from __future__ import annotations

import argparse
from pathlib import Path

from app.web.api import create_app


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Execute the current project's explicit AI modification operation."
    )
    parser.add_argument("--outputs-root", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--operation-id", required=True)
    args = parser.parse_args()

    app = create_app(Path(args.outputs_root).expanduser().resolve())
    app.state.revision_operation_service.execute(
        args.project_id,
        args.operation_id,
    )


if __name__ == "__main__":
    main()
