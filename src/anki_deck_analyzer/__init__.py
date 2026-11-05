"""Score an Anki deck against the Languages on Fire deck-quality framework."""

__version__ = "1.8.0"

from .analyze import Analysis, analyze, preview  # noqa: E402,F401
from .apkg import Deck, DeckReadError, read_package  # noqa: E402,F401
from .criteria import CRITERIA  # noqa: E402,F401
from .llm import Analyzer  # noqa: E402,F401
from .metrics import Metrics, compute  # noqa: E402,F401
from .scoring import Result, combine  # noqa: E402,F401

__all__ = [
    "__version__",
    "Analysis",
    "analyze",
    "preview",
    "Deck",
    "DeckReadError",
    "read_package",
    "CRITERIA",
    "Analyzer",
    "Metrics",
    "compute",
    "Result",
    "combine",
]
