"""runs_admin_query: filter parsing and the cascade each facet's counts follow."""
from datetime import datetime

import pytest
from fastapi import HTTPException

from app.models import Run, User
from app.services.runs_admin_query import (
    InputFormatFilter, RunFilters, StatusFilter, build_filter_options, filtered_runs_query,
    my_status_counts, parse_run_filters, submission_split,
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

    def test_on_behalf_run_by(self):
        filters = parse_run_filters("on_behalf", None, None)
        assert filters.run_by_on_behalf is True and filters.run_by_user_id is None

    def test_status_filter_values(self):
        for value in ("running", "failed", "red"):
            assert parse_run_filters(None, None, None, None, None, value).status.value == value
        assert parse_run_filters(None, None, None, None, None, "").status is None

    def test_bad_status_is_422(self):
        with pytest.raises(HTTPException) as exc:
            parse_run_filters(None, None, None, None, None, "green")
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



def test_on_behalf_filter_is_authorized_admin_runs_and_counts_cascade(db, seeded):
    assert sorted(r.id for r in filtered_runs_query(db, RunFilters(run_by_on_behalf=True)).all()) == [
        "Q00001", "Q00002", "Q00003", "Q00004"]
    assert build_filter_options(db, RunFilters()).on_behalf_count == 4
    # Own filter ignored, the others apply: Library has two, both on someone's behalf.
    assert build_filter_options(db, RunFilters(department="Library", run_by_on_behalf=True)).on_behalf_count == 2
    assert build_filter_options(db, RunFilters(run_by_self=True)).on_behalf_count == 4


@pytest.fixture
def status_runs(db, seeded):
    """Jane's group (Q00000-2) has a failed rerun; Omar's has a queued and a running run."""
    for run_id, values in {
        "Q00001": {Run.status: "failed"},
        "Q00002": {Run.quality_band: "RED"},
        "Q00003": {Run.status: "running"},
        "Q00004": {Run.status: "queued"},
    }.items():
        db.query(Run).filter(Run.id == run_id).update(values)
    db.commit()


def _status_ids(db, status, **kwargs):
    return sorted(r.id for r in filtered_runs_query(db, RunFilters(status=status, **kwargs)).all())


def test_status_running_lists_queued_and_running_runs(db, status_runs):
    assert _status_ids(db, StatusFilter.RUNNING) == ["Q00003", "Q00004"]


def test_status_failed_lists_only_the_failed_runs(db, status_runs):
    # Jane's clean runs (Q00000, Q00002) stay out: the visible top row of a group is a failed run.
    assert _status_ids(db, StatusFilter.FAILED) == ["Q00001"]
    assert _status_ids(db, StatusFilter.FAILED, department="Library") == []


def test_status_red_is_the_red_score_band(db, status_runs):
    assert _status_ids(db, StatusFilter.RED) == ["Q00002"]


def test_status_composes_with_other_filters(db, status_runs):
    assert _status_ids(db, StatusFilter.RUNNING, department="Library") == ["Q00003"]
    assert _status_ids(db, StatusFilter.RUNNING, run_by_self=True) == []


def test_status_counts_ignore_status_but_apply_other_filters(db, status_runs):
    everything = build_filter_options(db, RunFilters(status=StatusFilter.RED)).status
    assert (everything.all, everything.running, everything.failed, everything.red) == (5, 2, 1, 1)
    assert everything.awaiting_feedback == 2  # Q00000 and Q00002 are complete with no feedback
    library = build_filter_options(db, RunFilters(department="Library")).status
    assert (library.all, library.running, library.failed, library.red, library.awaiting_feedback) == (2, 1, 0, 1, 1)


def test_my_status_counts_cover_only_that_users_runs(db, seeded, status_runs):
    alice, bob = seeded
    # Alice: Q00000 (own_cv, complete, no feedback) + Q00001 (failed).
    mine = my_status_counts(db, alice.id)
    assert (mine.all, mine.running, mine.failed, mine.red, mine.awaiting_feedback) == (2, 0, 1, 0, 1)
    theirs = my_status_counts(db, bob.id)
    assert (theirs.all, theirs.running, theirs.failed) == (2, 1, 0)  # Q00002 is RED but red is admin only


def test_other_facets_honour_the_status_filter(db, status_runs):
    options = build_filter_options(db, RunFilters(status=StatusFilter.RUNNING))
    assert [(f.value, f.count) for f in options.faculty] == [("Omar Testperson", 2)]
    assert options.feedback.needed == 0


class TestSubmissionSplit:
    def test_splits_own_and_on_behalf_overall_and_per_department(self, db, seeded):
        split = submission_split(db)
        assert (split.own_cv, split.on_behalf) == (1, 4)
        by_dept = {d.department: (d.own_cv, d.on_behalf) for d in split.departments}
        assert by_dept == {"Medicine": (1, 1), "Library": (0, 2), None: (0, 1)}

    def test_largest_department_first_and_unknown_last_on_a_tie(self, db, seeded):
        names = [d.department for d in submission_split(db).departments]
        assert names == ["Library", "Medicine", None]

    def test_runs_without_a_submission_type_are_not_counted(self, db, seeded):
        alice, _ = seeded
        db.add(Run(id="QOLD00", user_id=alice.id, status="complete", filename="cv.docx",
                   file_type="docx", submission_type=None, started_at=datetime(2026, 8, 1)))
        db.commit()
        assert submission_split(db).own_cv + submission_split(db).on_behalf == 5

    def test_department_matches_the_runs_department_filter(self, db, seeded):
        """A bar's count is exactly what Runs lists for department + run_by=on_behalf."""
        split = submission_split(db)
        for dept in split.departments:
            if dept.department is None:
                continue
            listed = filtered_runs_query(db, parse_run_filters("on_behalf", None, dept.department)).count()
            assert listed == dept.on_behalf

    def test_stats_endpoint_carries_the_split_for_admins_only(self, client, db, seeded):
        from types import SimpleNamespace
        from app.auth import get_current_user
        from app.main import app

        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(role="user", id=1)
        assert client.get("/api/admin/stats").status_code == 403
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(role="admin", id=1, email="a@example.com")
        body = client.get("/api/admin/stats").json()["submissions"]
        assert (body["own_cv"], body["on_behalf"]) == (1, 4)
        assert body["departments"][0] == {"department": "Library", "own_cv": 0, "on_behalf": 2}
