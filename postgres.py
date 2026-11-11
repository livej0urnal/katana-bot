from sqlalchemy import create_engine
from sqlalchemy.engine import make_url


def postgres_engine(database_url: str):
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        raise ValueError("DATABASE_URL должна указывать на PostgreSQL.")
    return create_engine(url.set(drivername="postgresql+psycopg"), pool_pre_ping=True)


class PostgresConnection:
    """Expose the transaction and query interface shared with SQLite storage."""

    def __init__(self, engine):
        self.engine = engine

    def __enter__(self):
        self.transaction = self.engine.begin()
        self.connection = self.transaction.__enter__()
        return self

    def __exit__(self, *exception):
        return self.transaction.__exit__(*exception)

    def execute(self, statement: str, parameters: tuple = ()):
        # Only SQL literals from storage.py use this interface. Values stay bound.
        return self.connection.exec_driver_sql(statement.replace("?", "%s"), parameters)
