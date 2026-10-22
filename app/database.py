from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker
from .config import settings

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()


def migrate_legacy_schema(target_engine: Engine = engine) -> None:
    """Add columns introduced after the original paper-trading schema."""
    additions = {
        "portfolios": {
            "initial_cash": "NUMERIC(28, 10)",
        },
        "positions": {
            "portfolio_id": "INTEGER",
            "stop_loss": "NUMERIC(28, 10)",
            "take_profit": "NUMERIC(28, 10)",
        },
        "orders": {
            "portfolio_id": "INTEGER",
            "client_order_id": "VARCHAR(100)",
            "order_type": "VARCHAR(10) NOT NULL DEFAULT 'MARKET'",
            "fee": "NUMERIC(28, 10) NOT NULL DEFAULT 0",
            "stop_loss": "NUMERIC(28, 10)",
            "take_profit": "NUMERIC(28, 10)",
            "reason": "VARCHAR(50) NOT NULL DEFAULT 'MANUAL'",
            "decision": "JSON",
        },
        "trades": {
            "fee": "NUMERIC(28, 10) NOT NULL DEFAULT 0",
        },
    }

    with target_engine.begin() as connection:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        for table, columns in additions.items():
            if table not in tables:
                continue
            existing = {column["name"] for column in inspector.get_columns(table)}
            for column, definition in columns.items():
                if column not in existing:
                    connection.execute(text(
                        f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}'
                    ))

        if "portfolios" in tables and "positions" in tables:
            connection.execute(text(
                "UPDATE portfolios SET initial_cash = cash WHERE initial_cash IS NULL"
            ))
            connection.execute(text(
                "UPDATE positions "
                "SET portfolio_id = (SELECT id FROM portfolios ORDER BY id LIMIT 1) "
                "WHERE portfolio_id IS NULL"
            ))

        if "orders" in tables:
            order_inspector = inspect(connection)
            has_unique_order_id = any(
                constraint.get("column_names") == ["client_order_id"]
                for constraint in order_inspector.get_unique_constraints("orders")
            ) or any(
                index.get("unique")
                and index.get("column_names") == ["client_order_id"]
                for index in order_inspector.get_indexes("orders")
            )
            if not has_unique_order_id:
                connection.execute(text(
                    'CREATE UNIQUE INDEX "uq_orders_client_order_id" '
                    'ON "orders" ("client_order_id")'
                ))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
