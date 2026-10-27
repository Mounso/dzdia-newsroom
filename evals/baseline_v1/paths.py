"""File layout: committed dataset files vs. local-only data (copyrighted text, raw outputs)."""

from dataclasses import dataclass
from pathlib import Path

_EVALS_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Paths:
    dataset_dir: Path  # committed: sample, snapshot hashes, human labels, report
    data_dir: Path  # gitignored: HTML/text snapshots, model outputs, extracted elements

    @classmethod
    def default(cls) -> "Paths":
        return cls(
            dataset_dir=_EVALS_DIR / "datasets" / "baseline_v1",
            data_dir=_EVALS_DIR / ".data" / "baseline_v1",
        )

    @property
    def feeds(self) -> Path:
        return self.dataset_dir / "feeds.toml"

    @property
    def sample(self) -> Path:
        return self.dataset_dir / "sample.jsonl"

    @property
    def snapshots_meta(self) -> Path:
        return self.dataset_dir / "snapshots.jsonl"

    @property
    def labels(self) -> Path:
        return self.dataset_dir / "labels.jsonl"

    @property
    def report(self) -> Path:
        return self.dataset_dir / "report.md"

    @property
    def snapshot_dir(self) -> Path:
        return self.data_dir / "snapshots"

    @property
    def runs(self) -> Path:
        return self.data_dir / "runs.jsonl"

    @property
    def extractions(self) -> Path:
        return self.data_dir / "extractions.jsonl"

    @property
    def elements(self) -> Path:
        return self.data_dir / "elements.jsonl"

    @property
    def label_sheet(self) -> Path:
        return self.data_dir / "to_label.csv"
