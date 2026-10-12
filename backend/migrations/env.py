from alembic import context
from sqlalchemy import create_engine

from app.config import settings
from app.db import Base, alembic_include_object


def migrate(connection):
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True,
                      include_object=alembic_include_object)
    with context.begin_transaction():
        context.run_migrations()


# Callers may provide an isolated connection (tests, deployment tooling). Normal
# CLI operation follows the same environment/.env database setting as the API.
connection = context.config.attributes.get('connection')
database_url = context.config.attributes.get('database_url', settings.database_url)

if context.is_offline_mode():
    context.configure(url=database_url, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
elif connection is not None:
    migrate(connection)
else:
    engine = create_engine(database_url)
    with engine.connect() as connection:
        migrate(connection)
    engine.dispose()
