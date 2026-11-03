"""One-time, transactional copy into an EMPTY PostgreSQL database.
Stop writes and back up the source first. Never prints database credentials.
"""
import argparse
import os
from pathlib import Path
from sqlalchemy import create_engine, select, func, text, inspect
from database import Base
import models  # register every table


def migrate(source_path, target_url):
    source_path = Path(source_path).resolve()
    if not source_path.is_file():
        raise ValueError("SQLite source file does not exist.")
    if target_url.startswith("postgres://"):
        target_url = target_url.replace("postgres://", "postgresql+psycopg://", 1)
    elif target_url.startswith("postgresql://"):
        target_url = target_url.replace("postgresql://", "postgresql+psycopg://", 1)
    if not target_url.startswith("postgresql+psycopg://"):
        raise ValueError("TARGET_DATABASE_URL must be a PostgreSQL connection URL.")
    source = create_engine("sqlite:///" + source_path.as_posix())
    target = create_engine(target_url, pool_pre_ping=True)
    counts = {}
    try:
        with source.connect() as src, target.begin() as dst:
            Base.metadata.create_all(dst)
            for table in Base.metadata.sorted_tables:
                if dst.scalar(select(func.count()).select_from(table)):
                    raise ValueError("Target must be empty. No existing target records will be overwritten.")
            source_tables = set(inspect(src).get_table_names())
            for table in Base.metadata.sorted_tables:
                if table.name not in source_tables:
                    counts[table.name] = 0
                    continue
                # Typed ORM metadata preserves booleans, datetimes and enum values.
                rows = [dict(row) for row in src.execute(select(table)).mappings()]
                self_refs = [c for c in table.columns if c.nullable and any(f.column.table is table for f in c.foreign_keys)]
                deferred = []
                for row in rows:
                    refs = {c.name: row[c.name] for c in self_refs if row[c.name] is not None}
                    if refs:
                        deferred.append((row['id'], refs))
                        for key in refs: row[key] = None
                for offset in range(0, len(rows), 500):
                    dst.execute(table.insert(), rows[offset:offset+500])
                for row_id, refs in deferred:
                    dst.execute(table.update().where(table.c.id == row_id).values(**refs))
                counts[table.name] = len(rows)
                if dst.scalar(select(func.count()).select_from(table)) != len(rows):
                    raise RuntimeError("Migration row-count verification failed.")
                if 'id' in table.c and table.c.id.primary_key:
                    sequence = dst.scalar(text("SELECT pg_get_serial_sequence(:table, 'id')"), {'table':table.name})
                    if sequence:
                        maximum = dst.scalar(select(func.max(table.c.id)))
                        dst.execute(text("SELECT setval(CAST(:seq AS regclass), :value, :used)"),
                                    {'seq':sequence,'value':maximum or 1,'used':maximum is not None})
        return counts
    finally:
        source.dispose()
        target.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, help='Path to a backed-up SQLite database')
    args = parser.parse_args()
    try:
        result = migrate(args.source, os.environ.get('TARGET_DATABASE_URL', ''))
    except Exception as exc:
        # Driver errors can contain connection details or data; keep them out of console logs.
        print('Migration failed (' + type(exc).__name__ + '). Target transaction rolled back. Check source schema, target emptiness, and connection settings.')
        raise SystemExit(1)
    for table, count in result.items():
        print(f'{table}: {count} rows verified')
