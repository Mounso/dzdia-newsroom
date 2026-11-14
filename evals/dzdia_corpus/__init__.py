"""Local copy of the published Dzdia articles (ADR-0015): `python -m evals.dzdia_corpus <step>`.

A style reference only. The post records go to `evals/.data/dzdia_corpus/` (gitignored); the
manifest (no title, no text, no author) and the fetch log go to `evals/datasets/dzdia_corpus/`.
No step prints a title or article text.
"""


class CorpusError(RuntimeError):
    """A step cannot go on; the message says why. `main` prints it and exits."""
