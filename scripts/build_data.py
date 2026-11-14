#!/usr/bin/env python3
"""Turn the Excel table into data/table.json, the only file the website reads.

Run it from anywhere, after every change to the Excel file:

    python3 scripts/build_data.py

The Excel file is only read, never changed. If something in it does not make
sense (a misspelled result, a missing medicine ID), the script stops with a
clear message and leaves the old table.json alone, so a typo can never reach
patients.
"""
import json
import re
import sys
from pathlib import Path

try:
    import openpyxl
except ImportError:
    sys.exit("Missing a helper. Run this once, then try again:  pip3 install openpyxl")

ROOT = Path(__file__).resolve().parent.parent
XLSX = ROOT / "Kidney_Med_Checker_Verified_Table_v2.xlsx"
OUT = ROOT / "data" / "table.json"

# Website stage name -> start of that column's heading in the Medicines tab.
STAGE_COLUMNS = {
    "G1-2": "G1–2",
    "G3a": "G3a",
    "G3b": "G3b",
    "G4": "G4",
    "G5": "G5",
    "Dialysis": "Dialysis",
    "Transplant": "Kidney transplant",
    "Unknown": "Stage unknown",
}

APPLIES = {"Prescribed": "Prescribed", "Self-bought or considering": "Self", "Any": "Any"}


def fail(message):
    sys.exit(f"STOPPED - please fix the Excel file: {message}\ndata/table.json was not changed.")


def text(value):
    return "" if value is None else re.sub(r"\s+", " ", str(value)).strip()


def straight(value):
    return value.replace("’", "'")


def sheet(workbook, name_start):
    for ws in workbook.worksheets:
        if ws.title.startswith(name_start):
            return ws
    fail(f"no tab whose name starts with '{name_start}'")


def rows_with_ids(ws, id_pattern):
    """Each data row as {heading: text}. Rows without a matching ID (notes) are skipped."""
    rows = list(ws.iter_rows(values_only=True))
    headings = [text(h) for h in rows[0]]
    return headings, [
        dict(zip(headings, (text(c) for c in row)))
        for row in rows[1:]
        if re.fullmatch(id_pattern, text(row[0]))
    ]


def heading(headings, start, tab):
    for h in headings:
        if h.startswith(start):
            return h
    fail(f"tab '{tab}' has no column starting with '{start}'")


def family_key(name):
    """Rows for the same medicine share a key: 'Ibuprofen' + 'Ibuprofen (prescribed)'.

    High-dose and low-dose aspirin deliberately do NOT share one, because they
    are different rules, not the same drug bought two ways.
    """
    key = re.sub(r"\s*\([^)]*\)", "", name)
    key = re.sub(r",\s*(self-started|doctor recommended)\s*$", "", key)
    key = re.sub(r"\s+[–-]\s+(prescribed|high dose)\s*$", "", key)
    key = re.sub(r"\s+supplements\s*$", "", key)       # "Vitamin D supplements (not prescribed)" pairs with "Vitamin D – prescribed"
    return key.strip().lower()


def family_label(rows):
    if len(rows) == 1:
        return rows[0]["name"]
    name = next((r["name"] for r in rows if r["applies"] != "Prescribed"), rows[0]["name"])
    name = re.sub(r",\s*(self-started|doctor recommended)\s*$", "", name)
    name = re.sub(r"\s+[–-]\s+high dose \(not prescribed\)\s*$", "", name)
    name = re.sub(r"\s*\((not )?prescribed\)\s*$", "", name)
    return re.sub(r"\s+supplements\s*$", "", name)


def mentions(words, value):
    return any(re.search(r"\b" + re.escape(w) + r"\b", value, re.I) for w in words)


def contained(alerts, value, use_brands=False):
    """Which double-up ingredients does this text mention?"""
    found = []
    for alert in alerts:
        for ingredient in alert["ingredients"]:
            words = [ingredient] + (alert["brands"].get(ingredient, []) if use_brands else [])
            if mentions(words, value) and ingredient not in found:
                found.append(ingredient)
    return found


def parse_aliases(cell, alerts):
    aliases = []
    for raw in re.split(r",(?![^()]*\))", cell):
        raw = raw.strip()
        if not raw:
            continue
        display = re.sub(r"\s*\([^)]*\)", "", raw).strip()
        search = [display]
        for note in re.findall(r"\(([^)]*)\)", raw):
            # "(empagliflozin)" is another name, so it is searchable. Notes such as
            # "(more than in a daily multivitamin)" are not: someone typing
            # "multivitamin" must not be matched to a HIGH-DOSE vitamin rule.
            if not re.match(r"store-bought|more than|with\b", note, re.I):
                search.append(note)
        aliases.append({"display": display, "search": search, "contains": contained(alerts, raw, True)})
    return aliases


def parse_followup(cell, row_id, code_for):
    match = re.search(
        r"eGFR (\d+(?:\.\d+)?) or higher → ([^.]+)\.\s*Below \1 → ([^.]+)\.\s*Don't know → ([^.]+)\.?",
        straight(cell),
    )
    if not match:
        fail(f"{row_id}: cannot read the 'eGFR follow-up' wording: {cell!r}")
    return {
        "threshold": float(match.group(1)),
        "at_or_above": code_for(match.group(2), row_id),
        "below": code_for(match.group(3), row_id),
        "unknown": code_for(match.group(4), row_id),
    }


def read_categories(ws):
    categories, safety = {}, None
    for row in ws.iter_rows(min_row=2, values_only=True):
        code, label, headline, explanation = (text(c) for c in row[:4])
        if code:
            categories[code] = {"label": label, "headline": headline, "explanation": explanation}
        elif label.startswith("Added to every prescribed"):
            safety = headline
    if not safety:
        fail("'Result categories' tab: prescribed-medicine safety message not found")
    return categories, safety


def read_transplant_message(ws):
    for row in ws.iter_rows(values_only=True):
        if text(row[0]) == "Kidney transplant":
            match = re.search(r"'([^']+)'", text(row[1]))
            if match:
                return match.group(1)
    fail("'How to review' tab: the 'Kidney transplant' wording was not found")


def alert_phrase(row):
    """'...one medicine that contains an NSAID or aspirin.' -> 'an NSAID or aspirin'"""
    match = re.search(r"that contains ([^.]+)\.", row["Alert shown to patient"])
    if not match:
        fail(f"{row['ID']}: the alert text no longer says 'that contains ...', so its heading cannot be written")
    return match.group(1).strip()


def read_alerts(ws, code_for):
    _, rows = rows_with_ids(ws, r"D\d+")
    alerts = []
    for row in rows:
        counted = row["Ingredients counted"]
        names = [n.strip().lower() for n in re.sub(r"\([^)]*\)", "", counted).split(",") if n.strip()]
        brands = []
        for note in re.findall(r"\(([^)]*)\)", counted):
            brands += [b.strip() for b in note.split(",") if not re.match(r"\s*(many|including)\b", b)]
        alerts.append({
            "id": row["ID"],
            "ingredients": names,
            "brands": {names[0]: brands} if len(names) == 1 else {},
            "code": code_for(re.sub(r"\s*\(.*\)", "", row["Result"]), row["ID"]),
            "text": row["Alert shown to patient"],
            "phrase": alert_phrase(row),
        })
    return alerts


def read_medicines(ws, code_for, alerts):
    headings, rows = rows_with_ids(ws, r"M\d+")
    columns = {stage: heading(headings, start, "Medicines") for stage, start in STAGE_COLUMNS.items()}
    aliases_col = heading(headings, "Also called", "Medicines")
    note_col = heading(headings, "Patient note", "Medicines")
    # Optional: a different note for one result, e.g. "Consult doctor → High dose may be unsafe. …"
    by_result_col = next((h for h in headings if h.startswith("Patient note for a specific result")), None)
    followup_col = heading(headings, "eGFR follow-up", "Medicines")
    decision_col = heading(headings, "Dr. Pramod: OK", "Medicines")

    medicines, groups = {}, {}
    for row in rows:
        row_id = row["ID"]
        if row["Applies when"] not in APPLIES:
            fail(f"{row_id}: unknown 'Applies when' value {row['Applies when']!r}")
        if row[decision_col].lower() == "remove":
            print(f"NOTE: {row_id} {row['Medicine']} is marked 'Remove' but is still included.")
        stages = {stage: code_for(row[col], row_id) for stage, col in columns.items()}
        if "EGFR" in (stages["Dialysis"], stages["Transplant"]):
            fail(f"{row_id}: 'Needs eGFR' is not allowed for dialysis or transplant (Decision 7: never ask for eGFR on dialysis)")
        needs_followup = "EGFR" in stages.values()
        if needs_followup and not row[followup_col]:
            fail(f"{row_id}: says 'Needs eGFR' but the 'eGFR follow-up' column is empty")
        medicines[row_id] = {
            "id": row_id,
            "name": row["Medicine"],
            "applies": APPLIES[row["Applies when"]],
            "stages": stages,
            "egfr_followup": parse_followup(row[followup_col], row_id, code_for) if needs_followup else None,
            "note": row[note_col],
            "notes_by_result": notes_by_result(row.get(by_result_col, ""), row_id, code_for),
        }
        groups.setdefault(family_key(row["Medicine"]), []).append((medicines[row_id], row[aliases_col]))

    families = []
    for key, members in groups.items():
        family_rows = [m for m, _ in members]
        kinds = [r["applies"] for r in family_rows]
        if len(set(kinds)) != len(kinds):
            fail(f"'{key}' has two rows that apply in the same situation: {[r['id'] for r in family_rows]}")
        aliases, seen = [], set()
        for _, cell in members:
            for alias in parse_aliases(cell, alerts):
                if alias["display"].lower() not in seen:
                    seen.add(alias["display"].lower())
                    aliases.append(alias)
        families.append({
            "key": key,
            "label": family_label(family_rows),
            "rows": [r["id"] for r in family_rows],
            "aliases": aliases,
            "contains": contained(alerts, " ".join(r["name"] for r in family_rows)),
        })
    return medicines, families


def notes_by_result(cell, row_id, code_for):
    """'Consult doctor → High dose may be unsafe. …' (one per line) → {"ASK": "High dose may be unsafe. …"}"""
    notes = {}
    for line in filter(None, (l.strip() for l in (cell or "").splitlines())):
        match = re.fullmatch(r"([^→]+?)\s*→\s*(.+)", line)
        if not match:
            fail(f"{row_id}: can't read the note-for-a-result {line!r} (format: 'Consult doctor → note')")
        notes[code_for(match.group(1), row_id)] = match.group(2).strip()
    return notes


def parse_answers(cell, row_id, code_for, alerts):
    """'Yes → Consult doctor (counts as aspirin).  No → No change.  Not sure → Consult doctor.'"""
    answers = {}
    keys = {"yes": "yes", "no": "no", "not sure": "unsure"}
    # "(counts as aspirin)" = it contains it; "(may contain aspirin and acetaminophen)" = it might.
    for match in re.finditer(r"(Yes|No|Not sure) → ([^.(]+?)\s*(?:\((counts as|may contain) ([^)]+)\))?\.", cell):
        named = [i for a in alerts for i in a["ingredients"] if match.group(4) and i in match.group(4).lower()]
        sure = match.group(3) == "counts as"
        answers[keys[match.group(1).lower()]] = {"code": code_for(match.group(2), row_id),
                                                 "counts": named if sure else [], "maybe": [] if sure else named}
    if set(answers) != {"yes", "no", "unsure"}:
        fail(f"{row_id}: can't read the follow-up answers {cell!r} (need Yes, No and Not sure)")
    return answers


def own_stages(row, row_id, code_for):
    cells = {stage: next((v for h, v in row.items() if h.startswith(start)), "") for stage, start in STAGE_COLUMNS.items()}
    if not any(cells.values()):
        return None
    if not all(cells.values()):
        fail(f"{row_id}: fill in all of its own stage results, or none of them")
    return {stage: code_for(value, row_id) for stage, value in cells.items()}


def read_combinations(ws, medicines, alerts, misspellings, code_for):
    _, rows = rows_with_ids(ws, r"C\d+")
    combinations = []
    for row in rows:
        rules = [r.strip() for r in row["Uses rule(s) for"].split(",") if r.strip()]
        missing = [r for r in rules if r not in medicines]
        if missing or not rules:
            fail(f"{row['ID']}: 'Uses rule(s) for' points to unknown medicine IDs: {missing or 'none listed'}")
        entry = {
            "id": row["ID"],
            "product": row["Product"],
            "ingredients": row["Active ingredients"],
            "rules": rules,
            "alert": row["Alert shown to patient"],
            "contains": contained(alerts, row["Active ingredients"]),
            "also_search": [typo for typo, product in misspellings.items() if product == row["Product"]],
        }
        stages = own_stages(row, row["ID"], code_for)
        if stages:
            entry["stages"] = stages
        if row.get("Follow-up question"):
            # What it contains depends on the patient's answer, not on the "varies" text.
            entry["contains"] = []
            entry["followup"] = {"question": row["Follow-up question"],
                                 "answers": parse_answers(row.get("Follow-up answers", ""), row["ID"], code_for, alerts)}
        combinations.append(entry)
    return combinations


def read_alka_products(ws, code_for):
    """Specific Alka-Seltzer products: their label ingredients decide the result, so no question is needed."""
    _, rows = rows_with_ids(ws, r"A\d+")
    products = []
    for row in rows:
        code = code_for(row["Result (every stage except kidney transplant)"], row["ID"])
        contains = [name for name, flag in (("aspirin", row["Contains aspirin"]), ("acetaminophen", row["Contains acetaminophen"]))
                    if flag == "Yes"]
        products.append({
            "id": row["ID"], "product": row["Product"], "ingredients": row["Ingredients (openFDA label)"], "rules": [],
            "alert": row["Alert shown to patient"], "contains": contains, "also_search": [],
            "stages": {stage: ("NA" if stage == "Transplant" else code) for stage in STAGE_COLUMNS},
        })
    return products


def read_misspellings(ws):
    """Decision 2: "'Acacin'" -> "Read as Anacin" makes the misspelling searchable."""
    found = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        typo = re.fullmatch(r"'([^']+)'", text(row[1]))
        reading = re.match(r"Read as (\w+)", text(row[2]))
        if typo and reading:
            found[typo.group(1)] = reading.group(1)
    return found


def main():
    if not XLSX.exists():
        sys.exit(f"Cannot find the Excel file at {XLSX}")
    workbook = openpyxl.load_workbook(XLSX, data_only=True)

    categories, safety = read_categories(sheet(workbook, "Result categories"))
    codes = {straight(c["label"]).lower(): code for code, c in categories.items()}

    def code_for(label, where):
        code = codes.get(straight(label).strip().lower())
        if not code:
            fail(f"{where}: {label!r} is not one of the results in the 'Result categories' tab")
        return code

    for needed in ("OK", "DOSE", "ASK", "AVOID", "NA", "EGFR"):
        if needed not in categories:
            fail(f"'Result categories' tab is missing the code {needed}")

    alerts = read_alerts(sheet(workbook, "Double-up"), code_for)
    medicines, families = read_medicines(sheet(workbook, "Medicines"), code_for, alerts)
    misspellings = read_misspellings(sheet(workbook, "Decisions"))
    combinations = read_combinations(sheet(workbook, "Combination"), medicines, alerts, misspellings, code_for)
    combinations += read_alka_products(sheet(workbook, "Alka-Seltzer"), code_for)

    table = {
        "source_file": XLSX.name,
        "categories": categories,
        "safety_message": safety,
        "transplant_message": read_transplant_message(sheet(workbook, "How to review")),
        "medicines": medicines,
        "families": families,
        "combinations": combinations,
        "alerts": [{k: a[k] for k in ("id", "ingredients", "code", "text", "phrase")} for a in alerts],
    }

    OUT.parent.mkdir(exist_ok=True)
    temp = OUT.with_suffix(".tmp")
    temp.write_text(json.dumps(table, indent=1, ensure_ascii=False), encoding="utf-8")
    temp.replace(OUT)
    print(
        f"Done: {len(medicines)} medicine rows ({len(families)} searchable medicines), "
        f"{len(combinations)} combination products, {len(alerts)} double-up alerts."
    )
    print(f"Wrote {OUT}")
    stamp_versions()


def stamp_versions():
    """Add ?v=<fingerprint> to the site's css/js addresses. When a file changes, its address changes,
    so a browser can never keep running an old copy next to new ones."""
    import hashlib
    for page in (ROOT / "index.html", ROOT / "tests.html"):
        html = page.read_text(encoding="utf-8")

        def versioned(match):
            path = ROOT / match.group(2)
            digest = hashlib.sha1(path.read_bytes()).hexdigest()[:10] if path.exists() else "missing"
            return f'{match.group(1)}="{match.group(2)}?v={digest}"'

        updated = re.sub(r'(src|href)="((?:css|js)/[\w.-]+\.(?:js|css))(?:\?v=[^"]*)?"', versioned, html)
        if updated != html:
            page.write_text(updated, encoding="utf-8")


if __name__ == "__main__":
    main()
