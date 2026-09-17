"""`.hamilton/phase` -- read and written in one place."""

from hamilton_core import phase


def test_a_missing_phase_file_reads_as_none(tmp_path):
    assert phase.read(str(tmp_path)) is None


def test_the_phase_round_trips_without_surrounding_whitespace(tmp_path):
    (tmp_path / ".hamilton").mkdir()
    phase.write(str(tmp_path), "build")
    assert phase.read(str(tmp_path)) == "build"
    (tmp_path / ".hamilton" / "phase").write_text("spec\n")
    assert phase.read(str(tmp_path)) == "spec"
