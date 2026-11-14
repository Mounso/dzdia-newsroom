"""File layout of the Dzdia corpus (ADR-0015, points 4 and 5)."""

from dataclasses import dataclass
from pathlib import Path

_EVALS_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class CorpusPaths:
    dataset_dir: Path  # committed: manifest, fetch log
    data_dir: Path  # gitignored: post records as returned by the API, category names

    @classmethod
    def default(cls) -> "CorpusPaths":
        return cls(
            dataset_dir=_EVALS_DIR / "datasets" / "dzdia_corpus",
            data_dir=_EVALS_DIR / ".data" / "dzdia_corpus",
        )

    # --- data_dir (gitignored) ---

    @property
    def posts_dir(self) -> Path:
        return self.data_dir / "posts"

    def post_file(self, post_id: int) -> Path:
        return self.posts_dir / f"{post_id}.json"

    @property
    def categories(self) -> Path:
        return self.data_dir / "categories.json"

    # --- dataset_dir (committed) ---

    @property
    def manifest(self) -> Path:
        return self.dataset_dir / "manifest.jsonl"

    @property
    def fetch_log(self) -> Path:
        return self.dataset_dir / "fetches.jsonl"
