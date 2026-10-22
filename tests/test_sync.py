import zipfile

import pytest

from tum_moodle_dl.sync import sync_zip


def make_zip(path, files):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return path


def test_first_sync_adds_everything(tmp_path):
    archive = make_zip(tmp_path / "a.zip", {"Woche 1/folien.pdf": b"v1", "index.html": b"<i>"})
    report = sync_zip(archive, tmp_path / "course")
    assert (tmp_path / "course/Woche 1/folien.pdf").read_bytes() == b"v1"
    assert len(report.added) == 2 and report.unchanged == 0


def test_second_sync_changes_nothing(tmp_path):
    archive = make_zip(tmp_path / "a.zip", {"folien.pdf": b"v1"})
    sync_zip(archive, tmp_path / "course")
    report = sync_zip(archive, tmp_path / "course")
    assert report.summary() == "0 new, 0 updated, 1 unchanged"


def test_updates_files_you_have_not_touched(tmp_path):
    dest = tmp_path / "course"
    sync_zip(make_zip(tmp_path / "a.zip", {"folien.pdf": b"v1"}), dest)
    report = sync_zip(make_zip(tmp_path / "b.zip", {"folien.pdf": b"v2"}), dest)
    assert (dest / "folien.pdf").read_bytes() == b"v2"
    assert report.updated == [dest / "folien.pdf"]


def test_keeps_your_edits_and_saves_moodles_version_next_to_them(tmp_path):
    dest = tmp_path / "course"
    sync_zip(make_zip(tmp_path / "a.zip", {"folien.pdf": b"v1"}), dest)
    (dest / "folien.pdf").write_bytes(b"v1 with my notes")

    report = sync_zip(make_zip(tmp_path / "b.zip", {"folien.pdf": b"v2"}), dest)
    assert (dest / "folien.pdf").read_bytes() == b"v1 with my notes"
    assert (dest / "folien (moodle).pdf").read_bytes() == b"v2"
    assert [c.remote_copy.name for c in report.conflicts] == ["folien (moodle).pdf"]

    # Your file stays yours on later runs too.
    sync_zip(make_zip(tmp_path / "c.zip", {"folien.pdf": b"v3"}), dest)
    assert (dest / "folien.pdf").read_bytes() == b"v1 with my notes"
    assert (dest / "folien (moodle).pdf").read_bytes() == b"v3"


def test_files_already_there_are_never_overwritten(tmp_path):
    dest = tmp_path / "course"
    dest.mkdir()
    (dest / "folien.pdf").write_bytes(b"from the old downloader, then edited")
    sync_zip(make_zip(tmp_path / "a.zip", {"folien.pdf": b"v1"}), dest)
    assert (dest / "folien.pdf").read_bytes() == b"from the old downloader, then edited"


def test_never_deletes(tmp_path):
    dest = tmp_path / "course"
    sync_zip(make_zip(tmp_path / "a.zip", {"old.pdf": b"x", "new.pdf": b"y"}), dest)
    (dest / "my-notes.md").write_text("mine")
    sync_zip(make_zip(tmp_path / "b.zip", {"new.pdf": b"y"}), dest)
    assert (dest / "old.pdf").exists() and (dest / "my-notes.md").exists()


def test_refuses_paths_outside_the_course_folder(tmp_path):
    archive = make_zip(tmp_path / "a.zip", {"../evil.sh": b"rm -rf"})
    with pytest.raises(ValueError, match="outside the course folder"):
        sync_zip(archive, tmp_path / "course")
    assert not (tmp_path / "evil.sh").exists()


class UnflaggedInfo(zipfile.ZipInfo):
    """Writes the name's raw bytes without the UTF-8 flag, like some zip tools do."""

    def _encodeFilenameFlags(self):
        return self.filename.encode("cp437"), self.flag_bits & ~0x800


def test_utf8_names_without_the_utf8_flag(tmp_path):
    archive = tmp_path / "a.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(UnflaggedInfo("Übung.pdf".encode().decode("cp437")), b"x")
    with zipfile.ZipFile(archive) as zf:
        assert zf.infolist()[0].flag_bits & 0x800 == 0
    sync_zip(archive, tmp_path / "course")
    assert (tmp_path / "course/Übung.pdf").exists()
