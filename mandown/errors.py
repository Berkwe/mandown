class MandownError(Exception):
    pass


class SourceResponseError(MandownError):
    """A source returned an unusable HTTP response or payload."""


class AniListError(MandownError):
    """Base class for AniList client failures."""


class AniListNetworkError(AniListError):
    """The AniList request could not reach the network."""


class AniListTimeoutError(AniListNetworkError):
    """The AniList request exceeded its configured timeout."""


class AniListHTTPError(AniListError):
    """AniList returned an unsuccessful HTTP status."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


class AniListRateLimitError(AniListHTTPError):
    """AniList rejected a request because its rate limit was exceeded."""

    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        retry_after: int | None = None,
        reset_at: int | None = None,
    ):
        super().__init__(status_code, message)
        self.retry_after = retry_after
        self.reset_at = reset_at


class AniListResponseError(AniListError):
    """AniList returned invalid JSON or an unexpected response shape."""


class AniListGraphQLResponseError(AniListError):
    """AniList returned one or more GraphQL errors."""

    def __init__(self, message: str, graphql_errors: tuple[object, ...]):
        super().__init__(message)
        self.graphql_errors = graphql_errors


class NoImagesFoundError(MandownError):
    pass


class ImageDownloadError(MandownError):
    pass


class ChapterImageCountMismatchError(MandownError):
    pass
