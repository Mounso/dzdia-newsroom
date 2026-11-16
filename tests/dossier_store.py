"""An in-memory `DossierStore` that follows the contract of the Postgres repository
(`db/dossiers.py`), shared by the tool and worker tests."""

from collections.abc import Callable
from dataclasses import dataclass, field

from dzdia_newsroom.tools.dossier import (
    DOSSIER_TTL,
    Decision,
    DossierKey,
    DossierState,
    Refusal,
    Source,
    SourceKind,
    StoreResult,
)


@dataclass
class _Row:
    source: Source
    message_id: str
    link_index: int


@dataclass
class _Dossier:
    rows: list[_Row] = field(default_factory=list)
    last_activity: float = 0.0


class FakeStore:
    """The contract of `DossierStore` in memory. `now` is a clock in hours; a dossier whose
    last activity is older than the TTL is dropped by every operation on its key."""

    def __init__(self) -> None:
        self.dossiers: dict[DossierKey, _Dossier] = {}
        self.now = 0.0
        self.touches: list[DossierKey] = []

    def _live(self, key: DossierKey) -> _Dossier | None:
        dossier = self.dossiers.get(key)
        ttl_h = DOSSIER_TTL.total_seconds() / 3600
        if dossier is not None and self.now - dossier.last_activity > ttl_h:
            del self.dossiers[key]
            return None
        return dossier

    @staticmethod
    def _state(dossier: _Dossier | None) -> DossierState:
        if dossier is None:
            return DossierState()
        sources = [row.source for row in dossier.rows]
        return DossierState(
            count=len(sources),
            total_chars=sum(len(source.text) for source in sources),
            urls=frozenset(source.url for source in sources if source.url is not None),
        )

    async def add_source(
        self,
        key: DossierKey,
        *,
        message_id: str,
        link_index: int,
        decide: Callable[[DossierState], Decision],
    ) -> StoreResult:
        dossier = self._live(key)
        if dossier is None:
            dossier = self.dossiers[key] = _Dossier(last_activity=self.now)
        for row in dossier.rows:
            if (row.message_id, row.link_index) == (message_id, link_index):
                return StoreResult(row.source, self._state(dossier))
        decision = decide(self._state(dossier))
        if isinstance(decision, Refusal):
            return StoreResult(decision, self._state(dossier))
        source = Source(
            position=max((row.source.position for row in dossier.rows), default=0) + 1,
            kind=decision.kind,
            url=decision.url,
            text=decision.text,
            source_chars=decision.source_chars,
            truncated=decision.truncated,
            paywall_suspected=decision.paywall_suspected,
        )
        dossier.rows.append(_Row(source, message_id, link_index))
        dossier.last_activity = self.now
        return StoreResult(source, self._state(dossier))

    async def state(self, key: DossierKey) -> DossierState:
        return self._state(self._live(key))

    async def find_source(
        self, key: DossierKey, *, message_id: str, link_index: int
    ) -> Source | None:
        dossier = self._live(key)
        rows = dossier.rows if dossier is not None else []
        return next(
            (r.source for r in rows if (r.message_id, r.link_index) == (message_id, link_index)),
            None,
        )

    async def sources(self, key: DossierKey) -> list[Source]:
        dossier = self._live(key)
        rows = dossier.rows if dossier is not None else []
        return sorted((row.source for row in rows), key=lambda source: source.position)

    async def touch(self, key: DossierKey) -> None:
        self.touches.append(key)
        dossier = self._live(key)
        if dossier is not None:
            dossier.last_activity = self.now

    async def clear(self, key: DossierKey) -> int:
        dossier = self._live(key)
        if dossier is None:
            return 0
        del self.dossiers[key]
        return len(dossier.rows)


def stored(position: int, text: str, *, url: str | None = None) -> Source:
    return Source(
        position=position,
        kind=SourceKind.LINK if url else SourceKind.PASTED_TEXT,
        url=url,
        text=text,
        source_chars=len(text),
        truncated=False,
        paywall_suspected=False,
    )


def store_with(key: DossierKey, *sources: Source) -> FakeStore:
    store = FakeStore()
    store.dossiers[key] = _Dossier(
        rows=[_Row(source, f"m{source.position}", 0) for source in sources]
    )
    return store
