from pathlib import Path

from exlibris.book_paths import keeper_path
from exlibris.cleanup import build_duplicate_groups


def test_keeper_path_prefers_longest_basename() -> None:
    short = Path("/books/a.epub")
    long = Path("/books/much-longer-title.epub")
    assert keeper_path([short, long]) == long


def test_keeper_path_tiebreaks_on_full_path() -> None:
    a = Path("/books/same-name.epub")
    b = Path("/archive/deep/nested/same-name.epub")
    assert keeper_path([a, b]) == b


def test_build_duplicate_groups_repoints_moved_file() -> None:
    from exlibris.cleanup import BookRecord

    book = BookRecord(
        id=42,
        file_path="/books/old/missing-title.epub",
        file_name="missing-title.epub",
        content_hash="deadbeef",
        is_missing=True,
    )
    new_path = Path("/books/new/much-longer-moved-title.epub")
    groups = build_duplicate_groups(
        {"deadbeef": [new_path]},
        {"deadbeef": book},
    )
    assert len(groups) == 1
    assert groups[0].repoint_only is True
    assert groups[0].keeper == new_path
    assert groups[0].book_id == 42
    assert groups[0].remove == []


def test_build_duplicate_groups_updates_db_keeper() -> None:
    from exlibris.cleanup import BookRecord

    book = BookRecord(
        id=1,
        file_path="/books/short.epub",
        file_name="short.epub",
        content_hash="abc",
        is_missing=False,
    )
    unindexed = {
        "abc": [Path("/books/much-longer-title.epub")],
    }
    groups = build_duplicate_groups(unindexed, {"abc": book})
    assert len(groups) == 1
    assert groups[0].keeper == Path("/books/much-longer-title.epub")
    assert groups[0].book_id == 1
