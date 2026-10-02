"""runs_admin_query: filter parsing and the cascade each facet's counts follow."""
from datetime import datetime

import pytest
from fastapi import HTTPException

from app.models import Run, User
from app.services.runs_admin_query import (
    InputFormatFilter, RunFilters, build_filter_options, filtered_runs_query, parse_run_filters,
)


class TestParseRunFilters:
    def test_feedback_filter_values(self):
        assert parse_run_filters(None, None, None, "given").feedback.value == "given"
        assert parse_run_filters(None, None, None, "needed").feedback.value == "needed"
        assert parse_run_filters(None, None, None, "").feedback is None

    def test_bad_feedback_is_422(self):
        with pytest.raises(HTTPException) as exc:
            parse_run_filters(None, None, None, "maybe")
        assert exc.value.status_code == 422

    def test_input_format_filter_values(self):
        for value in ("wcm", "other", "unknown"):
            assert parse_run_filters(None, None, None, None, value).input_format.value == value
        assert parse_run_filters(None, None, None, None, "").input_format is None

    def test_bad_input_format_is_422(self):
        with pytest.raises(HTTPException) as exc:
            parse_run_filters(None, None, None, None, "pdf")
        assert exc.value.status_code == 422

    def test_blank_means_no_filter(self):
        assert parse_run_filters("", "", None) == RunFilters()

    def test_self_and_user_id(self):
        assert parse_run_filters("self", None, None).run_by_self is True
        assert parse_run_filters("42", None, None).run_by_user_id == 42

    def test_bad_run_by_is_422(self):
        with pytest.raises(HTTPException) as exc:
            parse_run_filters("someone", None, None)
        assert exc.value.status_code == 422


@pytest.fixture
def seeded(db):
    alice = User(email="alice@example.com", display_name="Alice Tester", department="Medicine")
    bob = User(email="bob@example.com", display_name="Bob Tester", department="Library")
    db.add_all([alice, bob])
    db.commit()
    specs = [
        (alice, "Jane Testperson", "own_cv"),
        (alice, "Jane Testperson", "authorized_admin"),
        (bob, "Jane Testperson", "authorized_admin"),
        (bob, "Omar Testperson", "authorized_admin"),
        (None, "Omar Testperson", "authorized_admin"),
    ]
    for i, (user, owner, sub_type) in enumerate(specs):
        db.add(Run(id=f"Q0000{i}", user_id=user.id if user else None, status="complete",
                   filename="cv.docx", file_type="docx", cv_owner_name=owner,
                   submission_type=sub_type, started_at=datetime(2026, 9, 1 + i)))
    db.commit()
    return alice, bob


def _pairs(options):
    return ([(d.value, d.count) for d in options.departments],
            [(f.value, f.count) for f in options.faculty],
            [(r.display_name, r.count) for r in options.run_by])


def test_department_filter_narrows_faculty_and_run_by_but_not_departments(db, seeded):
    options = build_filter_options(db, RunFilters(department="Medicine"))
    departments, faculty, run_by = _pairs(options)
    assert departments == [("Library", 2), ("Medicine", 2)]  # own filter ignored
    assert faculty == [("Jane Testperson", 2)]
    assert run_by == [("Alice Tester", 2)]  # her own_cv run counts under her name too
    assert options.self_count == 1


def test_faculty_filter_narrows_departments_and_run_by_but_not_faculty(db, seeded):
    options = build_filter_options(db, RunFilters(faculty="Omar Testperson"))
    departments, faculty, run_by = _pairs(options)
    assert departments == [("Library", 1)]  # the user-less run has no department
    assert faculty == [("Omar Testperson", 2), ("Jane Testperson", 3)]  # newest run first
    assert run_by == [("Bob Tester", 1)]
    assert options.self_count == 0


def test_run_by_filter_narrows_departments_and_faculty_but_not_run_by(db, seeded):
    alice, _ = seeded
    options = build_filter_options(db, RunFilters(run_by_user_id=alice.id))
    departments, faculty, run_by = _pairs(options)
    assert departments == [("Medicine", 2)]  # includes her own_cv run
    assert faculty == [("Jane Testperson", 2)]
    assert run_by == [("Alice Tester", 2), ("Bob Tester", 2)]


def test_self_filter_is_own_cv_runs(db, seeded):
    options = build_filter_options(db, RunFilters(run_by_self=True))
    departments, faculty, _ = _pairs(options)
    assert departments == [("Medicine", 1)]
    assert faculty == [("Jane Testperson", 1)]
    assert [r.id for r in filtered_runs_query(db, RunFilters(run_by_self=True)).all()] == ["Q00000"]


def test_faculty_options_are_most_recently_run_first(db, seeded):
    faculty = build_filter_options(db, RunFilters()).faculty
    # Omar's newest run (Sep 5) is later than Jane's (Sep 3).
    assert [f.value for f in faculty] == ["Omar Testperson", "Jane Testperson"]
    assert faculty[0].last_run_at == datetime(2026, 9, 5)


def test_filtered_query_loads_the_user_without_a_lazy_load(db, seeded):
    runs = filtered_runs_query(db, RunFilters(department="Library")).all()
    assert {r.user.display_name for r in runs} == {"Bob Tester"}  # raise_on_sql would raise


def test_input_format_filter_selects_by_column_and_unknown_means_null(db, seeded):
    for run_id, fmt in [("Q00000", "wcm"), ("Q00001", "other"), ("Q00002", "wcm")]:
        db.query(Run).filter(Run.id == run_id).update({Run.input_format: fmt})
    db.commit()

    def ids(value):
        query = filtered_runs_query(db, RunFilters(input_format=value))
        return sorted(r.id for r in query.all())

    assert ids(InputFormatFilter.WCM) == ["Q00000", "Q00002"]
    assert ids(InputFormatFilter.OTHER) == ["Q00001"]
    assert ids(InputFormatFilter.UNKNOWN) == ["Q00003", "Q00004"]
    assert len(ids(None)) == 5

