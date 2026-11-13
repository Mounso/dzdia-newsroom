"""The v2 comparison (ADR-0010, ADR-0013): `python -m evals.v2.compare <step>`.

It reads the baseline's files and never writes them. Local files (model outputs) go to
`evals/.data/v2_compare/`; committed files to `evals/datasets/v2_compare/`.
"""


class CompareError(RuntimeError):
    """A step cannot go on; the message says why. `main` prints it and exits."""
