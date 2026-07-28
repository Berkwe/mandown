from .anilist import (
    ANILIST_ENDPOINT,
    ANILIST_MAX_PER_PAGE,
    DEFAULT_PER_PAGE,
    MANDOWN_PER_PAGE_LIMIT,
    AniListClient,
    AniListCoverImage,
    AniListExternalLink,
    AniListField,
    AniListFieldSet,
    AniListGraphQLError,
    AniListManga,
    AniListMangaSummary,
    AniListPageInfo,
    AniListSearchResponse,
    AniListSupportedSource,
    AniListTitle,
    extract_supported_sources,
)
from .api import (
    ConvertFormats,
    convert,
    convert_progress,
    download,
    download_progress,
    init_parse_comic,
    load,
    process,
    process_progress,
    query,
    save_metadata,
    search,
)
from .base import BaseChapter, BaseMetadata
from .comic import BaseComic
from .errors import (
    AniListError,
    AniListGraphQLResponseError,
    AniListHTTPError,
    AniListNetworkError,
    AniListRateLimitError,
    AniListResponseError,
    AniListTimeoutError,
    SourceResponseError,
)
from .io import MD_METADATA_FILE
from .processor import (
    ProcessConfig,
    ProcessOps,
    ProcessOptionMismatchError,
    Processor,
)
from .processor.profiles import SupportedProfiles, all_profiles
from .search import SearchItem, SearchResults, search_all

__version__ = (1, 12, 2)
__version_str__ = ".".join(map(str, __version__))
