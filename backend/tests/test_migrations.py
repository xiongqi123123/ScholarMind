from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from scholarmind.db import Base, engine


def test_models_match_migrations(schema):
    with engine().connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
