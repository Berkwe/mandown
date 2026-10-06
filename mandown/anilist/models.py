"""Typed public models returned by the AniList client."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AniListTitle:
    romaji: str | None = None
    english: str | None = None
    native: str | None = None


@dataclass(frozen=True, slots=True)
class AniListCoverImage:
    extra_large: str | None = None
    large: str | None = None
    medium: str | None = None
    color: str | None = None


@dataclass(frozen=True, slots=True)
class AniListExternalLink:
    site: str
    url: str
    type: str | None = None
    language: str | None = None
    is_disabled: bool | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class AniListCreator:
    id: int | None = None
    role: str | None = None
    full_name: str | None = None
    native_name: str | None = None
    alternative_names: tuple[str, ...] = ()

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                name
                for name in (self.full_name, self.native_name, *self.alternative_names)
                if name
            )
        )


@dataclass(frozen=True, slots=True)
class AniListMangaSummary:
    id: int
    id_mal: int | None = None
    title: AniListTitle = AniListTitle()
    synonyms: tuple[str, ...] = ()
    format: str | None = None
    status: str | None = None
    country_of_origin: str | None = None
    popularity: int | None = None
    average_score: int | None = None
    cover_image: AniListCoverImage | None = None
    site_url: str | None = None


@dataclass(frozen=True, slots=True)
class AniListManga(AniListMangaSummary):
    description: str | None = None
    chapters: int | None = None
    volumes: int | None = None
    genres: tuple[str, ...] = ()
    external_links: tuple[AniListExternalLink, ...] = ()
    start_year: int | None = None
    creators: tuple[AniListCreator, ...] = ()


@dataclass(frozen=True, slots=True)
class AniListPageInfo:
    current_page: int | None = None
    last_page: int | None = None
    has_next_page: bool = False
    per_page: int | None = None
    total: int | None = None


@dataclass(frozen=True, slots=True)
class AniListSearchResponse:
    items: tuple[AniListMangaSummary | AniListManga, ...]
    page_info: AniListPageInfo


@dataclass(frozen=True, slots=True)
class AniListGraphQLError:
    message: str
    status: int | None = None
    locations: tuple[dict, ...] = ()
    path: tuple[str | int, ...] = ()


@dataclass(frozen=True, slots=True)
class AniListSupportedSource:
    provider: str
    url: str
    language: str | None
    site: str
    type: str | None
    is_disabled: bool
