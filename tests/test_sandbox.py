import os

import pytest

from jarvis.sandbox import Sandbox, SandboxError


@pytest.fixture
def box(tmp_path):
    root = tmp_path / "JARVIS-Sandbox"
    root.mkdir()
    (root / "photo.jpg").write_bytes(b"jpg")
    (root / "notes.md").write_text("hello")
    (root / "song.mp3").write_bytes(b"mp3")
    (root / ".hidden").write_text("x")
    return Sandbox(root)


def test_list_skips_hidden_and_symlinks(box, tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    os.symlink(outside, box.root / "link.txt")
    names = [f["path"] for f in box.list_files()]
    assert names == ["notes.md", "photo.jpg", "song.mp3"]


def test_auto_plan_sorts_by_type(box):
    plan = {m["source"]: m["destination"] for m in box.auto_plan()}
    assert plan == {"notes.md": "Documents/notes.md", "photo.jpg": "Images/photo.jpg", "song.mp3": "Audio/song.mp3"}


def test_execute_and_verify(box):
    plan = box.validate([{"source": "photo.jpg", "destination": "Pictures/"}])
    assert plan[0]["destination"] == "Pictures/photo.jpg"
    done, error = box.execute(plan)
    assert error is None and len(done) == 1
    assert (box.root / "Pictures/photo.jpg").read_bytes() == b"jpg"
    assert box.verify(done) == []


@pytest.mark.parametrize("move", [
    {"source": "../escape.txt", "destination": "a.txt"},
    {"source": "photo.jpg", "destination": "../photo.jpg"},
    {"source": "photo.jpg", "destination": "/etc/photo.jpg"},
    {"source": "/etc/passwd", "destination": "passwd"},
    {"source": "photo.jpg", "destination": "notes.md"},  # would overwrite
    {"source": "photo.jpg", "destination": "photo.jpg"},
    {"source": "missing.jpg", "destination": "x.jpg"},
    {"source": ".hidden", "destination": "visible"},
    {"source": "photo.jpg", "destination": "x/.ssh/photo.jpg"},
    {"source": "photo.jpg"},
    {"source": "photo.jpg", "destination": "a.jpg", "extra": 1},
])
def test_refuses_unsafe_moves(box, move):
    with pytest.raises(SandboxError):
        box.validate([move])


def test_refuses_symlinked_destination_folder(box, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, box.root / "Linked")
    with pytest.raises(SandboxError):
        box.validate([{"source": "photo.jpg", "destination": "Linked/photo.jpg"}])


def test_refuses_duplicate_destinations(box):
    with pytest.raises(SandboxError):
        box.validate([{"source": "photo.jpg", "destination": "a"}, {"source": "notes.md", "destination": "a"}])


def test_execute_revalidates(box):
    plan = box.validate([{"source": "photo.jpg", "destination": "Images/photo.jpg"}])
    (box.root / "Images").mkdir()
    (box.root / "Images/photo.jpg").write_text("someone else's file")
    done, error = box.execute(plan)
    assert done == [] and "already exists" in error
    assert (box.root / "photo.jpg").exists()
    assert (box.root / "Images/photo.jpg").read_text() == "someone else's file"


def test_verify_reports_missing(box):
    plan = box.validate([{"source": "photo.jpg", "destination": "Images/photo.jpg"}])
    assert box.verify(plan) == ["photo.jpg is still there", "Images/photo.jpg is missing"]
