---
paths:
  - "evals/**/*.py"
---

# Eval rules
- Human labels are the ground truth; LLM-judge verdicts are stored separately and never overwrite them.
- Every eval step is idempotent: re-running skips records whose deterministic id already exists.
- Record model name, date and prompt version with every generated output.
- Never commit article text or raw model outputs: they belong in `evals/.data/` (gitignored).
- Report rates with a confidence interval (cluster bootstrap by item), never a bare percentage.
