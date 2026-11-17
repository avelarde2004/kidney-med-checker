# Label check summary

Each of the 42 original medicine rows was compared with its official FDA drug label. Labels were downloaded from [openFDA](https://open.fda.gov/apis/drug/label/) on November 8, 2026.

**Method.** A script chose the most recent label for each row: prescription labels for prescribed rows, and over-the-counter labels for store-bought rows. It pulled out every sentence that mentions the kidneys, word for word. An AI reviewer compared those sentences with the table's stage results and recorded a verdict with exact quotes. A separate check confirmed that every quote appears in the label text. The nephrologist made every final decision.

| Verdict | Medicine rows | What it means |
|---|---|---|
| Agrees | 20 | The label supports the table's stage results. |
| Label less strict (nephrologist's rule is stricter) | 13 | The label allows more than the table. The table keeps the nephrologist's more cautious rule. |
| Label silent | 4 | The label neither supports nor contradicts the table. |
| No label found | 3 | No matching FDA label (high-dose vitamin A, C and D supplements). |
| Label stricter | 2 | The label is more cautious than the draft table (metformin, SGLT2 inhibitors). |
| **Total** | **42** | |

These counts were confirmed against the label-check workbook by `scripts/evaluate.py` (see [evaluation_summary.md](evaluation_summary.md)). The workbook itself, with the reviewer's notes and decisions, is kept private.

**Limits.** Labels usually state kidney limits as creatinine clearance (CrCl), while this tool uses eGFR stages, so the two are only roughly comparable. Labels for the same drug can differ between manufacturers. A label is not a treatment guideline.
