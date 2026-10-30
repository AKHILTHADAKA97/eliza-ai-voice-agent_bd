"""Database engine and session factory for the Eliza AI access-control system.

Uses SQLAlchemy with SQLite by default. To switch to PostgreSQL, set
DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/dbname
"""
import os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# SQLAlchemy 2.0+ moved declarative_base to sqlalchemy.orm
# SQLAlchemy 1.x has it in sqlalchemy.ext.declarative
try:
    from sqlalchemy.orm import declarative_base
except ImportError:
    from sqlalchemy.ext.declarative import declarative_base

# Ensure SQLite directory exists regardless of current working directory
BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = BACKEND_DIR / "data"
DEFAULT_DATA_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_DB_FILE = (DEFAULT_DATA_DIR / "eliza_access.db").as_posix()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"sqlite:///{DEFAULT_DB_FILE}",
)

# Also ensure any custom sqlite file directory is created
if DATABASE_URL.startswith("sqlite:///"):
    sqlite_path = DATABASE_URL.replace("sqlite:///", "")
    if sqlite_path and sqlite_path != ":memory:":
        Path(sqlite_path).parent.mkdir(parents=True, exist_ok=True)


connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI dependency that yields a database session and closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()