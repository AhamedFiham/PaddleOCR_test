"""Database engine, session factory and declarative base.

Point DATABASE_URL at an already-created empty schema; the tables
themselves are created on first run by Base.metadata.create_all in main.
"""

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "mysql+pymysql://root:root@localhost:3306/invoice_ocr?charset=utf8mb4",
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()


def get_db():
    """FastAPI dependency yielding a session that always gets closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
