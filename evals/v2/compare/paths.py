"""File layout of the comparison (ADR-0013, point 1). Later steps add their paths here."""

from dataclasses import dataclass
from pathlib import Path

from evals.baseline_v1.paths import Paths as BaselinePaths

_EVALS_DIR = Path(__file__).resolve().parent.parent.parent
_REPO_DIR = _EVALS_DIR.parent
LABELING_GUIDE = _REPO_DIR / "docs" / "evals" / "v2-labeling-guide.md"


@dataclass(frozen=True)
class ComparePaths:
    dataset_dir: Path  # committed: labels, export log, check log, report
    data_dir: Path  # gitignored: model outputs, extractions, elements, the sheet, the key map
    baseline: BaselinePaths  # read only: sample, snapshot hashes and texts, page_text files
    # The comparison's labeling guide (ADR-0013, point 6): its hash goes into the export log.
    labeling_guide: Path = LABELING_GUIDE

    @classmethod
    def default(cls) -> "ComparePaths":
        return cls(
            dataset_dir=_EVALS_DIR / "datasets" / "v2_compare",
            data_dir=_EVALS_DIR / ".data" / "v2_compare",
            baseline=BaselinePaths.default(),
        )

    # --- data_dir (gitignored) ---

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
        return self.data_dir / "sheet.csv"

    @property
    def key_map(self) -> Path:
        return self.data_dir / "keys.jsonl"

    # --- dataset_dir (committed) ---

    @property
    def labels(self) -> Path:
        return self.dataset_dir / "labels.jsonl"

    @property
    def export_log(self) -> Path:
        return self.dataset_dir / "exports.jsonl"

    @property
    def check_log(self) -> Path:
        return self.dataset_dir / "checks.jsonl"
