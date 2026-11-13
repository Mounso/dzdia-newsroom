"""Evidence items: the page text cut into paragraphs with stable ids (ADR-0009, point 5).

The model cites ids it was given; code resolves them. Each item is an exact slice of the source
text, so a quote checked against an item is checked against the source.
"""

import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass

MAX_ITEM_CHARS = 600
# Same cap for every arm of the v2 eval (ADR-0010, point 3): apply `cap_text` once, upstream.
MAX_INPUT_CHARS = 30_000

# Candidate sentence end: terminal punctuation, optional closing quote or bracket, whitespace,
# then something that can start a sentence.
_SENTENCE_END = re.compile(r"[.!?…]+[»”\"')\]]*(?=\s+[«“\"(\[—–\-A-ZÀ-ÖØ-Þ0-9])")
# Tokens after which a period does not end a sentence ("M. Tebboune", "Dr. Benali", "etc.").
_ABBREVIATIONS = frozenset(
    {"m", "mm", "mme", "mmes", "mlle", "dr", "pr", "me", "mgr", "st", "ste", "cf", "av", "etc"}
)
_LAST_TOKEN = re.compile(r"([A-Za-zÀ-ÖØ-öø-ÿ]+)\.*$")
# Tags that would let page text close an evidence item or open a fake one (V-17).
_TAG = re.compile(r"<(/?\s*evidence)", re.IGNORECASE)


@dataclass(frozen=True)
class Evidence:
    id: str
    text: str
    sha256: str


@dataclass(frozen=True)
class EvidenceSet:
    items: tuple[Evidence, ...]
    truncated: bool = False  # the source was cut by `cap_text`; said in the sources message

    def get(self, evidence_id: str) -> Evidence | None:
        return next((item for item in self.items if item.id == evidence_id), None)

    @property
    def ids(self) -> frozenset[str]:
        return frozenset(item.id for item in self.items)

    def render_for_prompt(self) -> str:
        """One `<evidence id="En">…</evidence>` element per item. The text is untrusted: any
        `<evidence` or `</evidence` inside it is neutralized so it cannot close the element or
        forge an id. Only the prompt copy changes; checks use the original text."""
        return "\n".join(
            f'<evidence id="{item.id}">{_neutralize(item.text)}</evidence>' for item in self.items
        )


def _neutralize(text: str) -> str:
    return _TAG.sub(r"‹\1", text)


def cap_text(text: str, max_chars: int = MAX_INPUT_CHARS) -> tuple[str, bool]:
    """Cut `text` to at most `max_chars`, at the last paragraph break that fits, else at the
    last whitespace. Returns the text and whether it was cut."""
    if len(text) <= max_chars:
        return text, False
    head = text[:max_chars]
    cut = head.rfind("\n")
    if cut <= 0:
        cut = max(head.rfind(" "), 0) or max_chars
    return head[:cut].rstrip(), True


def build_evidence(text: str, *, max_item_chars: int = MAX_ITEM_CHARS) -> EvidenceSet:
    """Split `text` (already capped) into items `E1…En`, in document order.

    A paragraph is a non-empty line. A paragraph longer than `max_item_chars` is cut at
    sentence ends, packing whole sentences while they fit (option 1a); a cut never falls inside
    quotation marks, since a quote cites a single item. A sentence that is itself too long stays
    whole. An empty text gives an empty set: the caller reports that nothing is readable.
    """
    pieces = [
        piece
        for line in text.splitlines()
        if line.strip()
        for piece in _pack(line.strip(), max_item_chars)
    ]
    return EvidenceSet(
        items=tuple(
            Evidence(id=f"E{n}", text=piece, sha256=hashlib.sha256(piece.encode()).hexdigest())
            for n, piece in enumerate(pieces, start=1)
        )
    )


def _pack(paragraph: str, max_chars: int) -> list[str]:
    if len(paragraph) <= max_chars:
        return [paragraph]
    chunks: list[str] = []
    start = 0
    last_end = 0  # end of the last sentence that still fits in the current chunk
    for end in (*_sentence_ends(paragraph), len(paragraph)):
        if end - start > max_chars and last_end > start:
            chunks.append(paragraph[start:last_end].strip())
            start = last_end
        last_end = end
    chunks.append(paragraph[start:].strip())
    return [chunk for chunk in chunks if chunk]


def _sentence_ends(paragraph: str) -> Iterator[int]:
    """Offsets just after each sentence end that lies outside quotation marks."""
    depth = _quote_depths(paragraph)
    for match in _SENTENCE_END.finditer(paragraph):
        end = match.end()
        if depth[end] > 0:
            continue
        token = _LAST_TOKEN.search(paragraph[max(0, match.start() - 12) : match.start() + 1])
        if match.group().startswith(".") and token and _is_abbreviation(token.group(1)):
            continue
        yield end


def _is_abbreviation(word: str) -> bool:
    # A single capital is an initial ("A. Benali"); a lone lowercase letter is not.
    return word.lower() in _ABBREVIATIONS or (len(word) == 1 and word.isupper())


def _quote_depths(text: str) -> list[int]:
    """Quotation depth before each offset (index i: depth between text[i-1] and text[i])."""
    depths = [0]
    depth = 0
    inside_ascii = False
    for char in text:
        if char in "«“":
            depth += 1
        elif char in "»”":
            depth = max(0, depth - 1)
        elif char == '"':
            inside_ascii = not inside_ascii
            depth += 1 if inside_ascii else -1
        depths.append(max(depth, 0))
    return depths
