"""Structured record of a pipeline stage that raised (#745).

Both drivers -- ``run_full_pipeline.py`` (the CLI, which catches a stage's
exception and carries on) and ``web_interface/.../orchestrator.py`` (which
records it and fails the run) -- write the same record here, one file per
document, beside the ``stage_*`` output dirs. ``quality_score`` reads it for its
fatal-error gate, so a stage failure no longer has to leave a Python exception
*name* in some artifact's ``error`` string to be seen: before this, the only
signal the scorer had was ``FATAL_ERROR_PATTERN`` over those strings, and a
failure whose text carried no type name (``'int' object is not iterable``)
was invisible to it.

The file exists only once a stage has failed for that document; a clean run
leaves none, so a run without the file is either clean or predates it, and the
scorer falls back to the regex alone in both cases. A later successful attempt
at the same stage removes that stage's entry, so a retried or re-run stage does
not keep capping the score.

Imports nothing from the web backend (CODING_STANDARDS §1.4).
"""

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

#: Directory under the pipeline outputs root that holds the records, next to
#: the ``stage_*`` dirs -- so ``score_one.collect``'s ``*/<uid><suffix>`` glob
#: finds it the same way it finds every stage artifact.
STAGE_ERRORS_DIRNAME = "stage_errors"

#: Filename suffix of one document's record: ``<uid>_stage_errors.json``.
#: The scorer's collectors (``scripts/score_one.py``,
#: ``quality_score_service``) select artifacts by suffix, and the scorer
#: globs for it, so this is the one definition all three import.
STAGE_ERRORS_SUFFIX = "_stage_errors.json"

_RECORD_FIELDS = ("stage", "exception_type", "message", "fatal")


@dataclass(frozen=True)
class StageError:
    """One stage's failure: which stage, what raised, and whether the run's
    output can be trusted past it (``fatal``)."""

    stage: str
    exception_type: str
    message: str
    fatal: bool

    @classmethod
    def from_exception(cls, stage: str, exc: BaseException) -> "StageError":
        """A stage whose runner raised. Always fatal: the stage produced
        nothing, so whatever it owned is missing from the output."""
        return cls(stage=stage, exception_type=type(exc).__name__,
                   message=str(exc), fatal=True)


def stage_errors_path(outputs_root: Path, document_uid: str) -> Path:
    """Where ``document_uid``'s record lives under a pipeline outputs root."""
    return Path(outputs_root) / STAGE_ERRORS_DIRNAME / f"{document_uid}{STAGE_ERRORS_SUFFIX}"


def _parse_record(raw: object, path: Path) -> StageError:
    if not isinstance(raw, dict) or set(raw) != set(_RECORD_FIELDS):
        raise ValueError(f"{path.name}: not a stage-error record: {raw!r}")
    if not isinstance(raw["fatal"], bool):
        raise ValueError(f"{path.name}: 'fatal' is not a bool: {raw['fatal']!r}")
    return StageError(stage=str(raw["stage"]), exception_type=str(raw["exception_type"]),
                      message=str(raw["message"]), fatal=raw["fatal"])


def read_stage_errors(path: Path) -> list[StageError]:
    """Every record in ``path``.

    Raises ``OSError`` when it cannot be read and ``ValueError`` (including
    ``json.JSONDecodeError``) when it is not a list of records -- a malformed
    record is not silently read as "no errors".
    """
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, list):
        raise ValueError(f"{path.name}: expected a list of stage-error records")
    return [_parse_record(item, path) for item in raw]


def record_stage_outcome(path: Path, stage: str, error: StageError | None) -> bool:
    """Record one stage attempt's outcome in ``path``; True when it was written.

    ``error`` replaces any earlier entry for ``stage``; ``None`` (the stage
    succeeded) removes it. With no file yet and nothing to record, nothing is
    written -- a clean run leaves no artifact. The write goes through a temp
    file and ``os.replace`` so a reader never sees a half-written record.
    """
    if not path.exists():
        if error is None:
            return False
        kept: list[StageError] = []
    else:
        kept = [e for e in read_stage_errors(path) if e.stage != stage]
    if error is not None:
        kept.append(error)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps([asdict(e) for e in kept], indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return True
