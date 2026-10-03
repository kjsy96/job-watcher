"""Job board sources. Each module turns one platform's public API into Postings."""

from jobwatcher.models import SourceName
from jobwatcher.sources.ashby import AshbySource
from jobwatcher.sources.base import Source
from jobwatcher.sources.greenhouse import GreenhouseSource
from jobwatcher.sources.lever import LeverSource

# One instance per platform. Sources hold no state, so sharing is safe.
SOURCES: dict[SourceName, Source] = {
    SourceName.GREENHOUSE: GreenhouseSource(),
    SourceName.LEVER: LeverSource(),
    SourceName.ASHBY: AshbySource(),
}
