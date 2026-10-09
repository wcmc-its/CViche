"""Coverage for the hot-column indexes (#117).

Two angles:
  * create_all path (every install / the test suite) gets the named indexes from
    the model `index=True` declarations.
  * the Alembic migration (existing prod MySQL) applies and rolls back cleanly on
    SQLite -- the repo's CI never exercises alembic on SQLite, and migrations here
    have a documented history of being SQLite-only-tested, so this guards the
    dual-dialect contract from the SQLite side.
"""
from pathlib import Path

from sqlalchemy import create_engine, inspect

ALEMBIC_DIR = Path(__file__).resolve().parent.parent / "alembic"

EXPECTED_INDEXES = {
    "ix_runs_status",
    "ix_runs_started_at",
    "ix_steps_run_id",
    "ix_logs_run_id",
    "ix_llm_usage_run_id",
    # #1114: runs.batch_id, from the run_batches migration (c3d8e1f5a297).
    "ix_runs_batch_id",
}


def _index_names(engine_or_bind):
    insp = inspect(engine_or_bind)
    found = set()
    for table in ("runs", "steps", "logs", "llm_usage"):
        found |= {ix["name"] for ix in insp.get_indexes(table)}
    return found


def test_create_all_declares_hot_indexes(db):
    """Base.metadata.create_all (fresh installs + the test DB) builds the indexes."""
    missing = EXPECTED_INDEXES - _index_names(db.get_bind())
    assert not missing, f"create_all did not declare: {missing}"


def test_index_migration_roundtrips_on_sqlite(tmp_path, monkeypatch):
    """The alembic chain (incl. the new revision) upgrades + downgrades on SQLite.

    env.py online mode builds a MySQL+IAM engine via create_cviche_engine; point
    that at a throwaway SQLite file so the real migration path runs without RDS.
    """
    from alembic.config import Config

    from alembic import command

    sqlite_url = f"sqlite:///{tmp_path / 'mig.db'}"
    eng = create_engine(sqlite_url)
    # env.py does `from app.database_factory import create_cviche_engine` at exec
    # time (during command.upgrade), so patching the module attribute first wins.
    monkeypatch.setattr(
        "app.database_factory.create_cviche_engine", lambda **kwargs: eng
    )

    cfg = Config()
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    cfg.set_main_option("sqlalchemy.url", sqlite_url)

    command.upgrade(cfg, "head")
    after_upgrade = _index_names(eng)
    assert EXPECTED_INDEXES <= after_upgrade, (
        f"missing after upgrade: {EXPECTED_INDEXES - after_upgrade}"
    )

    # Downgrade THROUGH the index migration by targeting its parent revision,
    # not a relative "-1": "-1" only undoes whatever happens to be head, so it
    # silently stops testing the indexes the moment any newer head migration is
    # added on top of 5a74eb0f9645 (the index migration). f1a2b3c4d5e6 is that
    # migration's down_revision, so rolling back to it always exercises the
    # index drop regardless of later migrations stacked above.
    command.downgrade(cfg, "f1a2b3c4d5e6")
    leftover = EXPECTED_INDEXES & _index_names(eng)
    assert not leftover, f"indexes not dropped on downgrade: {leftover}"


def test_run_batches_downgrade_never_drops_an_index_a_foreign_key_needs(monkeypatch):
    """MariaDB refuses DROP INDEX on an index a foreign key uses (1553), and
    its DDL is not transactional, so a refusal mid-downgrade leaves the schema
    half-downgraded. SQLite (the roundtrip above) accepts it, so the order is
    pinned on the recorded operations: each FK goes before its index, and
    run_batches' own user_id index goes with DROP TABLE, never on its own."""
    import importlib.util
    from unittest.mock import MagicMock

    path = ALEMBIC_DIR / "versions" / "c3d8e1f5a297_add_run_batches.py"
    spec = importlib.util.spec_from_file_location("c3d8e1f5a297_add_run_batches", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    op = MagicMock()
    batch_op = op.batch_alter_table.return_value.__enter__.return_value
    monkeypatch.setattr(migration, "op", op)

    migration.downgrade()

    assert op.drop_index.call_count == 0, op.drop_index.call_args_list
    assert op.drop_table.call_args_list == [(("run_batches",),)]
    assert [name for name, _, _ in batch_op.mock_calls] == ["drop_constraint", "drop_index", "drop_column"]


def test_engine_sets_pool_recycle():
    """create_cviche_engine wires pool_recycle so stale RDS connections are dropped."""
    from app.database_factory import create_cviche_engine

    # Engine construction is lazy (the creator lambda only runs on connect), so
    # dummy values are safe and never touch the network.
    engine = create_cviche_engine(
        db_host="localhost", db_port="3306", db_name="test", db_user="test"
    )
    assert engine.pool._recycle == 1800


def test_migrations_have_a_single_head():
    """Two heads crash-looped prod once (the deploy's `alembic upgrade head`
    refuses to pick one): every new revision must extend the one chain."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config()
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    assert len(ScriptDirectory.from_config(cfg).get_heads()) == 1


def test_feedback_verdicts_migration_roundtrips_on_sqlite(tmp_path, monkeypatch):
    """#1587's child table: created with its run and feedback FKs on upgrade,
    gone on a downgrade to its parent, and the feedback table untouched."""
    from alembic.config import Config

    from alembic import command

    sqlite_url = f"sqlite:///{tmp_path / 'mig.db'}"
    eng = create_engine(sqlite_url)
    monkeypatch.setattr("app.database_factory.create_cviche_engine", lambda **kwargs: eng)
    cfg = Config()
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    cfg.set_main_option("sqlalchemy.url", sqlite_url)

    command.upgrade(cfg, "head")
    insp = inspect(eng)
    feedback_columns = {c["name"] for c in insp.get_columns("feedback")}
    assert {c["name"] for c in insp.get_columns("feedback_verdicts")} == {
        "id", "feedback_id", "run_id", "lint", "shape", "finding_count", "verdict", "created_at"}
    assert {fk["referred_table"] for fk in insp.get_foreign_keys("feedback_verdicts")} == {"feedback", "runs"}

    command.downgrade(cfg, "a7c2e9d4f150")
    insp = inspect(eng)
    assert "feedback_verdicts" not in insp.get_table_names()
    assert {c["name"] for c in insp.get_columns("feedback")} == feedback_columns
