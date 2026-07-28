"""Trusted GraphQL field selections for AniList manga queries."""

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar


class AniListField(str, Enum):
    ID = "id"
    ID_MAL = "idMal"
    TITLE = "title"
    SYNONYMS = "synonyms"
    FORMAT = "format"
    STATUS = "status"
    COUNTRY_OF_ORIGIN = "countryOfOrigin"
    POPULARITY = "popularity"
    AVERAGE_SCORE = "averageScore"
    COVER_IMAGE = "coverImage"
    SITE_URL = "siteUrl"
    DESCRIPTION = "description"
    CHAPTERS = "chapters"
    VOLUMES = "volumes"
    GENRES = "genres"
    EXTERNAL_LINKS = "externalLinks"


FIELD_FRAGMENTS: dict[AniListField, str] = {
    AniListField.ID: "id",
    AniListField.ID_MAL: "idMal",
    AniListField.TITLE: "title { romaji english native }",
    AniListField.SYNONYMS: "synonyms",
    AniListField.FORMAT: "format",
    AniListField.STATUS: "status",
    AniListField.COUNTRY_OF_ORIGIN: "countryOfOrigin",
    AniListField.POPULARITY: "popularity",
    AniListField.AVERAGE_SCORE: "averageScore",
    AniListField.COVER_IMAGE: "coverImage { extraLarge large medium color }",
    AniListField.SITE_URL: "siteUrl",
    AniListField.DESCRIPTION: "description",
    AniListField.CHAPTERS: "chapters",
    AniListField.VOLUMES: "volumes",
    AniListField.GENRES: "genres",
    AniListField.EXTERNAL_LINKS: (
        "externalLinks { site url type language isDisabled notes }"
    ),
}


@dataclass(frozen=True, slots=True)
class AniListFieldSet:
    """An immutable selection made only from supported GraphQL fields."""

    values: frozenset[AniListField]

    LIGHT: ClassVar["AniListFieldSet"]
    CARD: ClassVar["AniListFieldSet"]
    DETAIL: ClassVar["AniListFieldSet"]
    FULL: ClassVar["AniListFieldSet"]

    def __init__(self, values=()):
        object.__setattr__(self, "values", frozenset(AniListField(value) for value in values))

    def with_fields(self, *fields: AniListField) -> "AniListFieldSet":
        return AniListFieldSet(self.values.union(fields))

    def without_fields(self, *fields: AniListField) -> "AniListFieldSet":
        return AniListFieldSet(self.values.difference(fields))

    def graphql(self) -> str:
        selected = self.values.union({AniListField.ID})
        return "\n".join(
            FIELD_FRAGMENTS[field] for field in AniListField if field in selected
        )


AniListFieldSet.LIGHT = AniListFieldSet(
    {AniListField.ID, AniListField.TITLE, AniListField.SITE_URL}
)
AniListFieldSet.CARD = AniListFieldSet(
    {
        AniListField.ID,
        AniListField.ID_MAL,
        AniListField.TITLE,
        AniListField.SYNONYMS,
        AniListField.FORMAT,
        AniListField.STATUS,
        AniListField.COUNTRY_OF_ORIGIN,
        AniListField.POPULARITY,
        AniListField.AVERAGE_SCORE,
        AniListField.COVER_IMAGE,
        AniListField.SITE_URL,
    }
)
AniListFieldSet.DETAIL = AniListFieldSet.CARD.with_fields(
    AniListField.DESCRIPTION,
    AniListField.CHAPTERS,
    AniListField.VOLUMES,
    AniListField.GENRES,
    AniListField.EXTERNAL_LINKS,
)
AniListFieldSet.FULL = AniListFieldSet.DETAIL
