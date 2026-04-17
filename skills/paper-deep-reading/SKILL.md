---
name: paper-deep-reading
description: Use when users ask for complete paper reading, deep interpretation, critical analysis, or reproducibility assessment from provided full text/PDF text, especially requests like "完整研读", "深度解读", "批判性分析", and "复现评估". Generate 7-section Chinese reports, save MD+HTML beside source PDF, and enforce evidence anchors, bilingual key-term explanations, and lightweight interactive HTML.
---

# Paper Deep Reading

## Overview

Produce an evidence-grounded, beginner-friendly deep-reading report for one paper using only user-provided content. Keep the report in the fixed 7-section backbone, write outputs to the source paper directory, render lightweight HTML accordion blocks, then validate before claiming completion.

## Input Contract

Require:
- `paper_source_path` (absolute or resolvable path to source PDF)
- paper full text or high-coverage extracted text

Optional:
- `focus_questions`
- `output_language` (default Chinese)

Derived outputs (default overwrite):
- `{paper_dir}/{paper_stem}_report.md`
- `{paper_dir}/{paper_stem}_report.html`

## Workflow

1. Parse and validate `paper_source_path`; ensure file exists and is readable.
2. Build original paper section tree (level-1 + level-2; create equivalent level-2 blocks if missing).
3. Draft report by loading the required references (see map below).
4. Generate Markdown report with fixed 7 sections.
5. Run deterministic HTML render script.
6. Run deterministic validation script.
7. If validation hard checks pass, return paths and summary; otherwise return failures and required fixes.

## Reference Loading Map

Always load:
- `references/report-template.md` (7-section backbone + section-4 slot template)
- `references/output-structure-spec.md` (output contract + canonical prompt + HTML contract)

Load by need:
- `references/analysis-taxonomy.md`: choose paper-type branch (model design / experiment / dataset / protocol).
- `references/writing-style-guide.md`: apply beginner-friendly language and bilingual key-term rules.
- `references/quality-checklist.md`: check hard/soft acceptance criteria.
- `references/failure-patterns.md`: diagnose and repair failed outputs.

## Deterministic Scripts

Render HTML:
```powershell
pwsh skills/paper-deep-reading/scripts/render_report_html.ps1 -paper_source_path <paper.pdf>
```

Validate report:
```powershell
pwsh skills/paper-deep-reading/scripts/validate_report.ps1 -paper_source_path <paper.pdf>
```

Behavior:
- Render script normalizes `术语解释：` / `证据锚点：` lines, injects accordion safely, forbids empty panels, and keeps low-visual-weight styling.
- Validate script enforces hard checks (sections/order, structure, anchors, length, bilingual terms, HTML link/accordion/a11y/no-empty-panel) and writes JSON results.
- Default policy: hard-check failure => non-zero exit.

## Guardrails

- Use only user-provided text; do not add external facts.
- Keep `术语解释：` and `证据锚点：` on standalone lines.
- Distinguish `论文原文陈述` vs `推断`.
- Mark missing evidence as `信息不足/不确定性` and explain impact.
- Do not write outside source paper directory unless user explicitly overrides.

## Compatibility Note

Legacy entrypoint remains available:
- `scripts/render-ai4research-report-html.ps1`
- It forwards to skill render script to preserve existing workflow.
