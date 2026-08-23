import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

project_root_path = Path(__file__).parents[1].resolve()
default_checkpoint_db_path = project_root_path / "data" / "checkpoints.sqlite"


def get_checkpoint_db_path() -> Path:
    configured_path = os.getenv("CHECKPOINT_DB_PATH")
    db_path = Path(configured_path).expanduser() if configured_path else default_checkpoint_db_path
    if not db_path.is_absolute():
        db_path = project_root_path / db_path
    return db_path.resolve()


@asynccontextmanager
async def open_sqlite_checkpointer() -> AsyncIterator[AsyncSqliteSaver]:
    db_path = get_checkpoint_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    async with AsyncSqliteSaver.from_conn_string(str(db_path)) as checkpointer:
        await checkpointer.setup()
        yield checkpointer
