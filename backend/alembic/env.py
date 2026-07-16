import os
from logging.config import fileConfig
from sqlalchemy import create_engine, pool
from alembic import context

# Import Base and all models so autogenerate can detect them
from app.platform.database import Base
from app.platform.alembic_autogenerate_policy import include_object
from app.models import *  # noqa: F401, F403

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
version_table_schema = config.attributes.get("version_table_schema")
supported_database_dialects = frozenset({"postgresql", "sqlite"})

# Use DATABASE_URL env var if available (Railway sets this)
database_url = os.environ.get("DATABASE_URL", config.get_main_option("sqlalchemy.url"))


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        include_object=include_object,
        version_table_schema=version_table_schema,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    supplied_connection = config.attributes.get("connection")

    def run(connection) -> None:
        dialect = str(connection.dialect.name)
        if dialect not in supported_database_dialects:
            raise RuntimeError(
                "Alembic supports only PostgreSQL and SQLite for this "
                f"application; refusing to migrate {dialect!r}."
            )
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            version_table_schema=version_table_schema,
        )
        with context.begin_transaction():
            context.run_migrations()

    if supplied_connection is not None:
        run(supplied_connection)
        return

    connectable = create_engine(database_url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        run(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
