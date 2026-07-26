class MandownError(Exception):
    pass


class SourceResponseError(MandownError):
    """A source returned an unusable HTTP response or payload."""


class NoImagesFoundError(MandownError):
    pass


class ImageDownloadError(MandownError):
    pass


class ChapterImageCountMismatchError(MandownError):
    pass
