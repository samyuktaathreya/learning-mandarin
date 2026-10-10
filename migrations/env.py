from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from app.core.config.data import MANDARIN_APP_DB  # <-- change to wherever your paths config lives
from app.auth import models as auth_models
from app.session import models as session_models

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

USER_MODEL_MODULES = (auth_models, session_models)

# Works whether these modules share one Base or each have their own
target_metadata = list(
    {id(m.Base.metadata): m.Base.metadata for m in USER_MODEL_MODULES}.values()
)

# Only tables defined in those modules belong to mandarin_app.db
USER_TABLES = {
    obj.__tablename__
    for mod in USER_MODEL_MODULES
    for obj in vars(mod).values()
    if isinstance(obj, type)
    and getattr(obj, "__tablename__", None)
    and obj.__module__ == mod.__name__
}


def include_object(obj, name, type_, reflected, compare_to):
    if type_ == "table":
        return name in USER_TABLES
    return True


engine = create_engine(f"sqlite:///{MANDARIN_APP_DB}")
with engine.connect() as connection:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        render_as_batch=True,  # lets SQLite handle column changes
    )
    with context.begin_transaction():
        context.run_migrations()