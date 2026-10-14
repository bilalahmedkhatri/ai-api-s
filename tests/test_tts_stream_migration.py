"""Preview schema upgrade preserves existing checkpoint rows."""
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def test_frame_count_upgrade_and_downgrade_preserve_checkpoint():
    path = Path(__file__).parents[1] / 'alembic/versions/f53c9e012d74_tts_stream_frame_counts.py'
    spec = importlib.util.spec_from_file_location('preview_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine('sqlite://')
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE tts_chunks (id INTEGER PRIMARY KEY, text TEXT)'))
        connection.execute(text("INSERT INTO tts_chunks VALUES (1, 'Keep this checkpoint')"))
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        assert 'frame_count' in {c['name'] for c in inspect(connection).get_columns('tts_chunks')}
        assert connection.execute(text('SELECT text, frame_count FROM tts_chunks')).one() == ('Keep this checkpoint', None)
        migration.downgrade()
        assert connection.execute(text('SELECT text FROM tts_chunks')).scalar_one() == 'Keep this checkpoint'
    engine.dispose()
