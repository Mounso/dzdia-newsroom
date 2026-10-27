"""Deterministic first pass: is each critical element present in the reference text? (ADR-001)

- numbers: every number of the element must appear in the source, compared on a canonical form
  ("1 500 000" == "1500000", "1,5" == "1.5");
- dates: exact token sequence, else all their numbers + month words present;
- quotes: exact token sequence (punctuation and quote marks ignored);
- names: exact token sequence, else fuzzy ratio against same-length word windows of the source
  (windows, not substrings, so "Alger" does not match inside "Algérie").

"found" is a strong hint, not a verdict: humans audit a sample of found elements, and label every
"not_found" one (translations and paraphrases are expected there).
"""

import re
import unicodedata
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

from evals.baseline_v1.schemas import ElementType

DEFAULT_NAME_THRESHOLD = 90.0

_QUOTES_AND_DASHES = str.maketrans(
    {
        "’": "'", "‘": "'", "´": "'", "`": "'",
        "“": '"', "”": '"', "«": '"', "»": '"',
        "–": "-", "—": "-", " ": " ", " ": " ",
    }
)  # fmt: skip
_NUMBER = re.compile(r"\d+(?:[ .]\d{3})*(?:[.,]\d+)?")
_WORD = re.compile(r"\w+")


def normalize(text: str) -> str:
    """Casefold, strip diacritics, unify typography and whitespace."""
    text = unicodedata.normalize("NFKD", text.translate(_QUOTES_AND_DASHES))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text.casefold()).strip()


def tokens(text: str) -> list[str]:
    return _WORD.findall(normalize(text))


def canonical_number(raw: str) -> str:
    compact = raw.replace(" ", "")
    if "," in compact:  # French decimal comma; dots are thousands separators
        compact = compact.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d+(\.\d{3})+", compact):  # dots as thousands separators
        compact = compact.replace(".", "")
    if "." in compact:
        return compact.rstrip("0").rstrip(".") or "0"
    return str(int(compact))


def numbers_in(text: str) -> set[str]:
    return {canonical_number(m) for m in _NUMBER.findall(normalize(text))}


@dataclass
class SourceIndex:
    """Precomputed views of one reference text, shared by all elements matched against it."""

    text: str
    token_list: list[str] = field(init=False)
    token_string: str = field(init=False)
    numbers: set[str] = field(init=False)
    _windows: dict[int, list[str]] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        self.token_list = tokens(self.text)
        self.token_string = f" {' '.join(self.token_list)} "
        self.numbers = numbers_in(self.text)

    def contains_tokens(self, value_tokens: list[str]) -> bool:
        return bool(value_tokens) and f" {' '.join(value_tokens)} " in self.token_string

    def windows(self, size: int) -> list[str]:
        if size not in self._windows:
            self._windows[size] = [
                " ".join(self.token_list[i : i + size])
                for i in range(max(0, len(self.token_list) - size + 1))
            ]
        return self._windows[size]


@dataclass(frozen=True)
class MatchResult:
    found: bool
    score: float  # 100 for exact matches, fuzzy ratio for names, 0 when not found


_EXACT = MatchResult(found=True, score=100.0)
_MISS = MatchResult(found=False, score=0.0)


def _match_number(value: str, index: SourceIndex) -> MatchResult:
    value_numbers = numbers_in(value)
    if value_numbers and value_numbers <= index.numbers:
        return _EXACT
    return _EXACT if index.contains_tokens(tokens(value)) else _MISS


def _match_date(value: str, index: SourceIndex) -> MatchResult:
    value_tokens = tokens(value)
    if index.contains_tokens(value_tokens):
        return _EXACT
    words = [t for t in value_tokens if not t.isdigit() and len(t) >= 3]  # month names, "fin", ...
    value_numbers = numbers_in(value)
    numbers_ok = bool(value_numbers) and value_numbers <= index.numbers
    words_ok = all(index.contains_tokens([w]) for w in words)
    return _EXACT if numbers_ok and words_ok else _MISS


def _match_name(value: str, index: SourceIndex, threshold: float) -> MatchResult:
    value_tokens = tokens(value)
    if not value_tokens:
        return _MISS
    if index.contains_tokens(value_tokens):
        return _EXACT
    best = process.extractOne(
        " ".join(value_tokens),
        index.windows(len(value_tokens)),
        scorer=fuzz.ratio,
        score_cutoff=threshold,
    )
    return MatchResult(found=True, score=float(best[1])) if best else _MISS


def match_element(
    element_type: ElementType,
    value: str,
    index: SourceIndex,
    name_threshold: float = DEFAULT_NAME_THRESHOLD,
) -> MatchResult:
    if element_type == "number":
        return _match_number(value, index)
    if element_type == "date":
        return _match_date(value, index)
    if element_type == "quote":
        return _EXACT if index.contains_tokens(tokens(value)) else _MISS
    return _match_name(value, index, name_threshold)
