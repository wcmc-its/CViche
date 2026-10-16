"""OutputManager: the stage 1a/1b/2 paths a CV's stage modules write, which
both drivers then look up by document_uid = Path(cv_path).stem (#1910)."""

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import run_full_pipeline  # noqa: E402
from unified_pipeline.core.output_manager import OutputManager  # noqa: E402

#: The processing suffixes the handle used to strip (#1910).
_FORMER_SUFFIXES = ("_signature_segmented", "_segmented", "_mapped_v2", "_mapped_gpt51",
                    "_mapped", "_delimiters", "_entries", "_parsed", "_classified", "_three_pass")


@pytest.mark.parametrize("suffix", _FORMER_SUFFIXES)
def test_cv_named_with_a_processing_suffix_gets_its_own_outputs(tmp_path, suffix):
    """"Smith{suffix}.docx" and "Smith.docx" are two CVs: their stage
    outputs must not share a path, or one overwrites the other."""
    plain = OutputManager(str(tmp_path / "Smith.docx"), base_output_dir=tmp_path)
    suffixed = OutputManager(str(tmp_path / f"Smith{suffix}.docx"), base_output_dir=tmp_path)
    for getter in ("get_stage1a_json_path", "get_stage1a_txt_path",
                   "get_stage1b_path", "get_stage2_path"):
        assert getattr(plain, getter)() != getattr(suffixed, getter)(), getter


@pytest.mark.parametrize("name", ["Smith_entries.docx", "ABC123.docx", "Jones_Cv.pdf"])
def test_stage_paths_are_where_the_cli_driver_looks(tmp_path, name):
    """The CLI keys every stage input by Path(cv_path).stem; the paths the
    stage modules write must be those same files."""
    om = OutputManager(str(tmp_path / name), base_output_dir=tmp_path)
    expected = run_full_pipeline.get_output_paths(Path(name).stem)
    assert om.get_stage1a_json_path().name == expected["1a"].name
    assert om.get_stage1b_path().name == expected["1b"].name
    assert om.get_stage2_path().name == expected["2"].name


@pytest.mark.parametrize("path", ["", "/"])
def test_path_with_no_file_name_is_refused(tmp_path, path):
    with pytest.raises(ValueError, match="No file handle"):
        OutputManager(path, base_output_dir=tmp_path)


def test_construction_creates_only_the_three_stage_dirs(tmp_path):
    OutputManager(str(tmp_path / "cv.docx"), base_output_dir=tmp_path / "out")
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == [
        "stage_1a_segmentation", "stage_1b_hierarchy_mapping", "stage_2_entry_extraction"]
