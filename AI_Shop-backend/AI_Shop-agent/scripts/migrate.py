"""Apply the single current Agent database schema."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from app.db.migrations import run_migrations

    run_migrations()


if __name__ == "__main__":
    main()
