from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime

import pytest

from jobwatcher.models import Posting, Remote, SourceName


def make_posting(**overrides: object) -> Posting:
    fields: dict[str, object] = {
        "source": SourceName.GREENHOUSE,
        "board": "exampleco",
        "source_job_id": "4012345",
        "company": "Example Industrial AI Co",
        "title": "Implementation Engineer",
        "location": "Denver, CO",
        "remote": Remote.UNKNOWN,
        "url": "https://boards.greenhouse.io/exampleco/jobs/4012345",
        "description_text": "Commissioning and data validation at mining sites.",
        "published_at": None,
    }
    fields.update(overrides)
    return Posting(**fields)  # type: ignore[arg-type]


def test_id_combines_source_board_and_job_id() -> None:
    assert make_posting().id == "greenhouse:exampleco:4012345"


def test_same_job_number_on_different_sources_gives_different_ids() -> None:
    gh = make_posting(source=SourceName.GREENHOUSE)
    lever = make_posting(source=SourceName.LEVER)
    assert gh.id != lever.id


def test_source_and_remote_are_plain_strings() -> None:
    # StrEnum values can go straight into SQLite and TOML comparisons.
    assert isinstance(SourceName.ASHBY, str)
    assert str(SourceName.ASHBY) == "ashby"
    assert f"{Remote.UNKNOWN}" == "unknown"
    assert SourceName("lever") is SourceName.LEVER  # parses config values


@pytest.mark.parametrize("field_name", ["board", "source_job_id", "title", "url"])
@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_required_field_is_rejected(field_name: str, blank: str) -> None:
    with pytest.raises(ValueError, match=field_name):
        make_posting(**{field_name: blank})


def test_naive_published_at_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        make_posting(published_at=datetime(2026, 10, 1, 9, 0))


def test_aware_published_at_is_accepted() -> None:
    when = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
    assert make_posting(published_at=when).published_at == when


def test_empty_location_and_description_are_allowed() -> None:
    # Boards can omit these; the filters flag them rather than the model rejecting them.
    posting = make_posting(location="", description_text="")
    assert posting.location == ""


def test_posting_is_immutable() -> None:
    posting = make_posting()
    with pytest.raises(FrozenInstanceError):
        posting.title = "Changed"  # type: ignore[misc]
    assert replace(posting, title="Changed").title == "Changed"
