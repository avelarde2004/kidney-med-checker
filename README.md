# Kidney-Safe Medication Checker

> **Draft for review – not for patient use. Educational tool, not medical advice.**
> Never stop or change a prescribed medicine without talking to your doctor.

**Live site:** https://avelarde2004.github.io/kidney-med-checker/
**Tests:** https://avelarde2004.github.io/kidney-med-checker/tests.html

## What it does

A phone-friendly website that tells people with chronic kidney disease (CKD) whether a medicine they take needs a second look. The visitor:

1. picks their kidney stage (G1–2, G3a, G3b, G4, G5, dialysis, kidney transplant, or "I don't know"),
2. types the medicines they take, by generic or brand name (for example "Advil", "Aleve PM", "Bactrim"),
3. gets a plain-language result for each one:
   - **No change needed**
   - **Dose may need adjusting – check with your clinician**
   - **Consult your doctor**
   - **Not recommended**
   - **This cannot be assessed** (not enough information to give guidance)

   For a few medicines the answer depends on the exact eGFR number, so the site asks for it as a follow-up question. Visitors can also enter their eGFR instead of choosing a stage.

It also warns about combination products (cold and flu medicines, PM pain relievers and so on) and about taking two products with the same ingredient.

The current table covers 46 medicine rules across 41 searchable medicines, plus 103 combination products.

## Who it's for

- **Patients with CKD and their caregivers**, as a prompt to ask their care team about a medicine. It does not replace that conversation.
- **Kidney clinics** that want a simple handout link. This is not ready for that yet (see *Before clinic use*).

## How it works

**A verified table, not live AI.** Every answer comes from one file, [`data/table.json`](data/table.json). It is built from a medication table that a nephrologist reviewed line by line, and every patient-facing sentence was approved by the nephrologist. AI tools helped build the website, check the drug labels, and draft the wording, but **no AI runs while a patient uses the site**. The page looks answers up in the fixed table, so the same question always gets the same answer.

**Checked against FDA drug labels.** [`scripts/fetch_labels.py`](scripts/fetch_labels.py) downloads official drug labels from the public [openFDA](https://open.fda.gov/apis/drug/label/) API and pulls out every sentence that mentions the kidneys. Each table row was compared with those sentences, and every difference went to the nephrologist to decide. The `source` notes in the table (for example "FDA label") record where each rule came from.

**Private by design.** The site is plain HTML, CSS and JavaScript with no server code, accounts, cookies, analytics, or tracking. Nothing a visitor types is sent anywhere. If the visitor asks it to, the site keeps their medicine list in their own browser only.

| Path | What it is |
|---|---|
| `index.html`, `css/`, `js/` | The website. `js/engine.js` makes the medical decisions, `js/text.js` holds the wording, `js/app.js` draws the screens. |
| `data/table.json` | The verified table the site reads |
| `tests.html` | The automated tests (see below) |
| `scripts/build_data.py` | Builds `data/table.json` from the review spreadsheet |
| `scripts/fetch_labels.py` | Downloads the openFDA labels for the label check |

### What's kept private

The review spreadsheets (the nephrologist's line-by-line notes, comments, sign-offs, and the label-check worksheet), the raw label downloads, and the scripts that work on those spreadsheets are **not** in this repository. They hold clinical reviewer notes that were never meant to be published. The public `data/table.json` contains only what patients see, plus the source of each rule. `build_data.py` and `fetch_labels.py` are included so you can see how the table and label check are built, but `build_data.py` needs the private spreadsheet to run.

## Run it locally

You need Python 3 (built into macOS) to start a small local web server. The site can't be opened by double-clicking `index.html`, because browsers block it from reading `data/table.json` that way.

```
git clone https://github.com/avelarde2004/kidney-med-checker.git
cd kidney-med-checker
python3 -m http.server 8000
```

Open **http://localhost:8000**. To try it on a phone on the same Wi-Fi, open `http://YOUR-COMPUTER-IP:8000` (on a Mac, find the IP with `ipconfig getifaddr en0`). Stop the server with Control+C.

## How the tests work

Open **http://localhost:8000/tests.html**, or the [live tests page](https://avelarde2004.github.io/kidney-med-checker/tests.html). The page runs on its own and shows a pass/fail total at the top. It has two parts:

1. **Medical rules.** It checks the decision engine against the real table: expected results for each stage, combination products, double-up warnings, eGFR follow-up questions, and the exact wording patients see.
2. **The real site at phone width.** It loads the actual website in a 360-pixel-wide frame and clicks through it the way a patient would. It checks that buttons are big enough to tap (at least 48 px), that nothing scrolls sideways, and that results, the saved list, and the disclaimer behave correctly.

The tests save and restore any medicine list stored in that browser, so running them won't erase your own list.

## Limitations

- **Draft only.** Not validated for clinical use, and not a medical device.
- **Limited coverage.** Only the medicines and products in the table. Anything else shows "Not on our list yet. Ask your pharmacist or doctor."
- **Stage-based, not dose-based.** It uses kidney stage (and eGFR for a few medicines), not weight, age, other conditions, drug interactions, or exact doses.
- **Labels change.** The FDA label check reflects the labels downloaded in November 2026.
- **U.S. focus.** Brand names and labels are from the U.S. market.
- **English only.**

## Before clinic use

- For the AI Passport project (November 2026), Dr. Pramod approved all result-category texts. Before any clinic rollout, she should review in depth the result-category texts and any rows she approved in bulk rather than one by one.
- Descovy (M46) on dialysis is set to "Dose check": the label allows use on hemodialysis after each session, and peritoneal dialysis isn't covered. Confirm this with Dr. Pramod.
- Remove the "Draft for review" banner and the `noindex` tag only after these items are done and the clinic approves.

## Credits

Built by **Andrew Velarde**, with clinical review by **Dr. Pramod** (nephrology) and guidance from **Dr. Baslanti**.
**UF AI Passport Program, Group 4.**
