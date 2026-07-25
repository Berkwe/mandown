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
from .search import SearchItem, SearchResults
from .io import MD_METADATA_FILE
from .processor import (
    ProcessConfig,
    ProcessOps,
    ProcessOptionMismatchError,
    Processor,
)
from .processor.profiles import SupportedProfiles, all_profiles

__version__ = (1, 12, 2)
__version_str__ = ".".join(map(str, __version__))
