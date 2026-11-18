# Test results

**Result: All 142 tests passed (112 rules, 30 on the real site). 0 failed.**

| | |
|---|---|
| Date run | November 17, 2026 |
| Page | http://localhost:8000/tests.html (local copy, before publishing) |
| Browser | Google Chrome 157 (headless), on macOS |
| Data | `data/table.json` built from the final nephrologist-approved table (v2) |

## What the tests cover

- **Medical rules (112):** expected results for every stage, combination products, double-up warnings (NSAID or aspirin, acetaminophen), eGFR follow-up questions, Alka-Seltzer "Does it list aspirin?" answers, search (brand names, misspellings), and the exact wording patients see.
- **The real site at phone width (30):** the actual website loaded in a 360-pixel-wide frame and clicked through like a patient would. These check tap targets of at least 48 px, no sideways scrolling, the results-page order, editing and removing medicines, the keep-on-this-phone option, the printout, the text-size button, that typed text can never run as code, and the newest-first order of results, list and printout (including after an edit, with one medicine, and with lists saved before that change).

## Run them yourself

Open the [live tests page](https://avelarde2004.github.io/kidney-med-checker/tests.html), or run the site locally (see the README) and open http://localhost:8000/tests.html. The pass/fail total appears at the top within a few seconds.
