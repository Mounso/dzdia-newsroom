"""File layout of the comparison (ADR-0013, point 1). Later steps add their paths here."""

from dataclasses import dataclass
from pathlib import Path

from evals.baseline_v1.paths import Paths as BaselinePaths

_EVALS_DIR = Path(__file__).resolve().parent.parent.parent


@dataclass(frozen=True)
class ComparePaths:
    dataset_dir: Path  # committed: labels, export log, report
    data_dir: Path  # gitignored: model outputs, extractions, elements, the sheet
    baseline: BaselinePaths  # read only: sample, snapshot hashes and texts, page_text runs

    @classmethod
    def default(cls) -> "ComparePaths":
        return cls(
            dataset_dir=_EVALS_DIR / "datasets" / "v2_compare",
            data_dir=_EVALS_DIR / ".data" / "v2_compare",
            baseline=BaselinePaths.default(),
        )

    @property
    def runs(self) -> Path:
        return self.data_dir / "runs.jsonl"
