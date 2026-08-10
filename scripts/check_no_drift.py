"""Fail if the models and the migrated schema have drifted apart.

The failure this catches: someone edits `app/models.py`, the tests pass because
the test database was built by `create_all` somewhere, and the migration that
production actually runs never gets written. Comparing the live schema against
the model metadata turns that into a red build.
"""

import sys

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from app.config import get_settings
from app.db import engine
from app.models import Base

# Alembic reports these as changes on a schema it did not create itself; they
# are noise rather than drift.
IGNORED_PREFIXES = ("add_index", "remove_index")


def main() -> int:
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        diff = compare_metadata(context, Base.metadata)

    real = [d for d in diff if not str(d[0]).startswith(IGNORED_PREFIXES)]

    if not real:
        print("schema matches models - no drift")
        return 0

    print(f"schema has drifted from the models ({len(real)} differences):", file=sys.stderr)
    for item in real:
        print(f"  {item}", file=sys.stderr)
    print(
        "\nWrite a migration: alembic revision --autogenerate -m '<what changed>'",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    print(f"comparing against {get_settings().database_url.split('@')[-1]}")
    sys.exit(main())
