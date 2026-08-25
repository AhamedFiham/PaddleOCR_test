from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# SQLite for testing: a single file, zero setup, no credentials needed.
# Swap DATABASE_URL back to a mysql+pymysql://... string whenever you're
# ready to move to MySQL for production - nothing else in the app needs
# to change, SQLAlchemy handles the difference.
DATABASE_URL = "sqlite:///./invoices.db"

# check_same_thread=False is required specifically for SQLite used with
# FastAPI, since FastAPI's threadpool means requests can come from
# different threads than the one that created the connection.
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()