"""Public AniList search and metadata client."""

from .client import (
    ANILIST_ENDPOINT,
    ANILIST_MAX_PER_PAGE,
    DEFAULT_PER_PAGE,
    MANDOWN_PER_PAGE_LIMIT,
    AniListClient,
    extract_supported_sources,
)
from .fields import AniListField, AniListFieldSet
from .models import (
    AniListCoverImage,
    AniListExternalLink,
    AniListGraphQLError,
    AniListManga,
    AniListMangaSummary,
    AniListPageInfo,
    AniListSearchResponse,
    AniListSupportedSource,
    AniListTitle,
)

__all__ = [
    "ANILIST_ENDPOINT",
    "ANILIST_MAX_PER_PAGE",
    "DEFAULT_PER_PAGE",
    "MANDOWN_PER_PAGE_LIMIT",
    "AniListClient",
    "AniListCoverImage",
    "AniListExternalLink",
    "AniListField",
    "AniListFieldSet",
    "AniListGraphQLError",
    "AniListManga",
    "AniListMangaSummary",
    "AniListPageInfo",
    "AniListSearchResponse",
    "AniListSupportedSource",
    "AniListTitle",
    "extract_supported_sources",
]
