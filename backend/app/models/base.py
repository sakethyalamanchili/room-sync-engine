from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    # don't use create_all, alembic handles the schema
    pass
