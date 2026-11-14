#!/usr/bin/env python3
"""Download FDA drug labels from openFDA and pull out every kidney-related sentence.

    python3 scripts/fetch_labels.py

Works on Python 3.9. Network, JSON and CSV use only the standard library. The one
outside package is openpyxl, the same one build_data.py uses, to read the Excel file.

Each response is saved in labels/raw/ and reused next time. To pull fresh labels,
delete that folder first. The Excel table and the website are never changed.

Writes to labels/:
  label_excerpts.csv        every kidney sentence from each chosen label
  labels_picked.csv         which label was chosen for each medicine, and why
  combination_products.csv  what each combination product's label really contains
  alka_seltzer_products.csv each distinct Alka-Seltzer product found
  candidate_additions.csv   other OTC products Dr. Pramod might add
"""
import csv
import datetime
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

try:
    import openpyxl
except ImportError:
    sys.exit("Missing a helper. Run this once, then try again:  pip3 install openpyxl")

ROOT = Path(__file__).resolve().parent.parent
XLSX = ROOT / "Kidney_Med_Checker_Verified_Table_v2.xlsx"
LABELS = ROOT / "labels"
RAW = LABELS / "raw"
API = "https://api.fda.gov/drug/label.json"
PAUSE_SECONDS = 0.3
PAGE_SIZE = 25
MAX_PAGES = 4
CANDIDATE_CAP = 60
MIN_PER_INGREDIENT = 4

RX = "HUMAN PRESCRIPTION DRUG"
OTC = "HUMAN OTC DRUG"
NO_LABEL_SUPPLEMENT = "No FDA drug label (dietary supplement)"

SECTIONS = [
    "boxed_warning", "contraindications", "dosage_and_administration",
    "use_in_specific_populations", "warnings_and_cautions", "warnings", "precautions",
    "drug_interactions", "do_not_use", "ask_doctor", "stop_use", "active_ingredient",
]

# Word boundaries matter: a plain search for "renal" would also match "adrenal".
KIDNEY = re.compile(
    r"\brenal(?:ly)?\b|\bkidneys?\b|creatinine\s+clearance|\bCrCl\b|\bCLcr\b|\beGFR\b|\bGFR\b|dialysis",
    re.I,
)
# Homeopathic products are listed as OTC under names like "sodium phosphate" at
# tiny dilutions (6X, 30C). They are not the medicines we are checking.
HOMEOPATHIC = re.compile(r"\bHPUS\b|\bhomeopath|\b\d+\s*(?:X|C|CH|LM|MK)\b", re.I)


def target(terms, substances=None, route="ORAL", strength=None, prefer=None,
           allow_other_type=True, extras=(), note="", required=None, search_field="generic_name"):
    """One label to look for. `terms` are generic-name words that must all match."""
    terms = [terms] if isinstance(terms, str) else terms
    return {"terms": terms, "substances": [s.upper() for s in (substances or terms)], "route": route,
            "strength": strength, "prefer": prefer, "allow_other_type": allow_other_type,
            "extras": extras, "note": note, "required": required, "search_field": search_field}


# The real generic names behind each row of the Medicines tab.
TARGETS = {
    "M01": [target("ibuprofen")],
    "M02": [target("ibuprofen")],
    "M03": [target("naproxen")],
    "M04": [target("naproxen")],
    "M05": [target("meloxicam")],
    "M06": [target("diclofenac potassium", ["diclofenac"])],
    "M07": [target("diclofenac sodium", ["diclofenac"], route="TOPICAL")],
    "M08": [target("celecoxib")],
    "M09": [target("aspirin", strength=r"\b325\s*mg")],
    "M10": [target("aspirin", strength=r"\b81\s*mg")],
    "M11": [target("aspirin", strength=r"\b81\s*mg",
                   note="Low-dose aspirin is sold OTC even when a doctor recommends it.")],
    "M12": [target("acetaminophen", prefer=OTC)],
    "M13": [target("magnesium hydroxide")],
    "M14": [target("magnesium citrate")],
    "M15": [target(["aluminum hydroxide", "magnesium hydroxide"], extras=("DIMETHICONE", "SIMETHICONE"))],
    "M16": [target("sodium phosphate", route="RECTAL", prefer=OTC, note="Fleet-type enema"),
            target("sodium phosphate", prefer=RX, allow_other_type=False, note="OsmoPrep-type oral bowel prep")],
    "M17": [target("pseudoephedrine")],
    "M20": [target("ergocalciferol"), target("calcitriol")],
    "M22": [target("gabapentin")],
    "M23": [target("pregabalin")],
    "M24": [target("oxycodone hydrochloride", ["oxycodone"])],
    "M25": [target("tramadol hydrochloride", ["tramadol"])],
    "M26": [target("meperidine hydrochloride", ["meperidine"])],
    "M27": [target("codeine sulfate", ["codeine"])],
    "M28": [target("morphine sulfate", ["morphine"])],
    "M29": [target("baclofen"),
            target("ozobax", ["baclofen"], search_field="brand_name",
                   note="Second label: newer-format baclofen brand named in our table")],
    "M30": [target("duloxetine")],
    "M31": [target(["sulfamethoxazole", "trimethoprim"])],
    "M32": [target("ciprofloxacin")],
    "M33": [target("levofloxacin")],
    "M34": [target("nitrofurantoin")],
    "M35": [target("valacyclovir")],
    "M45": [target("acyclovir")],
    "M46": [target("descovy", ["emtricitabine", "tenofovir alafenamide"], search_field="brand_name")],
    "M43": [target("phenylephrine")],
    "M44": [target("metformin", note="Same drug as M36; this row is for people not yet taking it.")],
    "M36": [target("metformin")],
    "M37": [target("glyburide")],
    "M38": [target("empagliflozin"), target("dapagliflozin"), target("canagliflozin")],
    "M39": [target(["emtricitabine", "tenofovir disoproxil"])],
    "M40": [target("allopurinol")],
    "M41": [target("colchicine")],
    "M42": [target("lithium carbonate", ["lithium"]),
            target("lithium carbonate", ["lithium"], required=r"30 to 89",
                   note="Second label: the most recent lithium label that states numeric kidney cutoffs")],
}
SUPPLEMENTS = {"M18", "M19", "M21"}

# Search words for each combination product, and how to recognize the right one.
COMBINATIONS = {
    "C01": (["advil sinus"], r"sinus congestion"),
    "C02": (["advil allergy"], r"allergy (and|&) congestion"),
    "C03": (["advil cold"], r"cold (and|&) sinus"),
    "C04": (["advil pm"], r"advil pm"),
    "C05": (["motrin pm"], r"motrin pm"),
    "C06": (["advil dual"], r"dual action"),
    "C07": (["aleve pm"], r"aleve pm"),
    "C08": (["aleve-d"], r"sinus (and|&) cold"),
    "C09": (["excedrin extra strength", "excedrin"], r"^excedrin extra strength( pain reliever| geltabs| caplets)?$"),
    "C10": (["anacin"], r"anacin"),
    "C11": (["bc powder", "bc"], r"^b\.?c\b"),
    "C12": (["goody's", "goodys", "goody"], r"good.*extra strength"),
    "C13": (["alka-seltzer"], r"alka"),
    "C14": (["bayer pm", "bayer"], r"bayer.*\bpm\b"),
    "C15": (["fleet"], r"fleet"),
    "C16": (["excedrin migraine"], r"^excedrin migraine$"),
}
# Words removed so "NAPROXEN SODIUM" on a label matches "naproxen" in our table.
SALT_WORDS = (r"\b(hydrochloride|hcl|sodium|citrate|maleate|hydrobromide|sulfate|tartrate|potassium|"
              r"unspecified form|heptahydrate|anhydrous|monohydrate|dibasic|monobasic)\b")

# Other OTC products to look for. Names in the second part are generic, not brands.
CANDIDATE_INGREDIENTS = ["ibuprofen", "naproxen", "aspirin", "sodium phosphate", "magnesium hydroxide", "magnesium citrate"]
GENERIC_WORDS = set("""ibuprofen naproxen sodium aspirin magnesium hydroxide citrate phosphate dibasic monobasic
    tablets tablet caplets caplet capsules capsule softgels softgel liquid gels gel oral solution suspension
    mg usp otc low dose strength extra regular adult chewable coated enteric safety pain reliever relief
    nsaid fever reducer and hcl hydrochloride acetaminophen diphenhydramine milk of magnesia saline enema
    laxative antacid famotidine pseudoephedrine caffeine childrens children infants infant junior concentrated
    dye free suspension orange cherry grape berry ec chewables minis pm""".split())
# Store-brand names: "Equate Ibuprofen" is plain ibuprofen, not a separate product to review.
RETAILER_WORDS = set("""good sense basic care dg health equate equaline leader foster thrive neighbor pharmacy one
    topcare rugby sunmark quality choice kirkland walgreens cvs rite aid up publix kroger major signature select
    amazon wal itch""".split())

SSL_CONTEXT = ssl.create_default_context()   # always verifies certificates
last_request = 0.0


def slug(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:150]


def fetch(query, name):
    """Return the saved response if there is one; otherwise download and save it."""
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / (slug(name) + ".json")
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            print(f"  Re-downloading damaged file {path.name}")

    global last_request
    time.sleep(max(0.0, PAUSE_SECONDS - (time.time() - last_request)))
    url = API + "?" + urllib.parse.urlencode(query, quote_via=urllib.parse.quote)
    request = urllib.request.Request(url, headers={"User-Agent": "kidney-med-checker-label-check"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=60, context=SSL_CONTEXT) as response:
                body = response.read().decode("utf-8")
            break
        except urllib.error.HTTPError as error:
            if error.code == 404:      # openFDA's way of saying "no matches"
                body = json.dumps({"results": [], "not_found": True})
                break
            if error.code not in (429, 500, 502, 503) or attempt == 3:
                raise
            time.sleep(5 * (attempt + 1))
        except urllib.error.URLError as error:
            if isinstance(error.reason, ssl.SSLError):
                sys.exit(f"SSL certificate problem: {error.reason}")
            if attempt == 3:
                raise
            time.sleep(5 * (attempt + 1))
    last_request = time.time()
    data = json.loads(body)
    # Write to a temporary name first, so a full disk can't leave half a file in the cache.
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_text(body, encoding="utf-8")
        temporary.replace(path)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        sys.exit(f"Could not save {path.name}: {error}. Free up disk space and run this again.")
    return data


def search_labels(search, name, page=0, sort=True, limit=PAGE_SIZE):
    query = {"search": search, "limit": limit, "skip": page * limit}
    if sort:
        query["sort"] = "effective_time:desc"
    return fetch(query, f"{name}_p{page}").get("results", [])


def collapse(text):
    return re.sub(r"\s+", " ", text).strip()


ABBREVIATIONS = re.compile(r"(?:\be\.g|\bi\.e|\bvs|\bDr|\bapprox|\bFig|\bNo|\bInc|\betc)\.$", re.I)


def sentences(text):
    """Split into sentences. Every piece is an exact slice of the label's text."""
    pieces, start = [], 0
    for match in re.finditer(r"(?<=[.!?])\s+(?=[A-Z0-9(\[•])", text):
        if ABBREVIATIONS.search(text[start:match.start()]):
            continue
        pieces.append(text[start:match.start()])
        start = match.end()
    pieces.append(text[start:])
    return [p.strip() for p in pieces if p.strip()]


def kidney_sentences(label):
    found, seen = [], set()
    for section in SECTIONS:
        for block in label.get(section) or []:
            for sentence in sentences(collapse(block)):
                if KIDNEY.search(sentence) and (section, sentence) not in seen:
                    seen.add((section, sentence))
                    found.append((section, sentence))
    return found


def field(label, name):
    return label.get("openfda", {}).get(name) or []


def label_date(label):
    value = label.get("effective_time", "")
    return f"{value[:4]}-{value[4:6]}-{value[6:8]}" if len(value) == 8 else value


def dailymed(label):
    return "https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid=" + label.get("set_id", "")


def active_text(label):
    return collapse(" ".join(label.get("active_ingredient") or []))


def is_homeopathic(label):
    text = active_text(label) + " " + " ".join(label.get("indications_and_usage") or [])[:400]
    return bool(HOMEOPATHIC.search(text))


def matches_target(label, goal):
    if is_homeopathic(label):
        return False
    route = field(label, "route")
    if goal["route"] not in route:
        return False
    substances = field(label, "substance_name")
    if not substances:
        return False
    # The label must contain exactly these ingredients, so a combination
    # product is never used as the label for a single medicine.
    # "Starts with" matters: PREDNISOLONE SODIUM PHOSPHATE is not a sodium phosphate laxative.
    for substance in substances:
        if not any(substance.startswith(s) for s in goal["substances"]) and not any(e in substance for e in goal["extras"]):
            return False
    if not all(any(substance.startswith(s) for substance in substances) for s in goal["substances"]):
        return False
    if goal["strength"] and not re.search(goal["strength"], active_text(label), re.I):
        return False
    return True


def find_label(medicine_id, goal, product_type):
    """Most recent matching label of this type that contains kidney text.

    If no matching label mentions the kidneys, returns the most recent one with
    no excerpts, because "this label is silent" is a real finding.
    """
    words = " AND ".join(f'openfda.{goal["search_field"]}:"{t}"' for t in goal["terms"])
    search = f'{words} AND openfda.product_type:"{product_type}"'
    name = f"{medicine_id}_{'_'.join(goal['terms'])}_{goal['route']}_{'rx' if product_type == RX else 'otc'}"
    matching, without_kidney, newest = 0, 0, None
    for page in range(MAX_PAGES):
        results = search_labels(search, name, page)
        for label in results:
            if not matches_target(label, goal):
                continue
            matching += 1
            excerpts = kidney_sentences(label)
            if goal["required"] and not any(re.search(goal["required"], s) for _, s in excerpts):
                continue
            if excerpts:
                return label, excerpts, matching, without_kidney
            without_kidney += 1
            newest = newest or label
        if len(results) < PAGE_SIZE:
            break
    return newest, [], matching, without_kidney


def type_short(product_type):
    return {RX: "Rx", OTC: "OTC"}.get(product_type, product_type)


def read_medicines(workbook):
    rows = list(workbook["Medicines"].iter_rows(values_only=True))
    headings = [re.sub(r"\s+", " ", str(h or "")).strip() for h in rows[0]]
    return [dict(zip(headings, row)) for row in rows[1:] if re.fullmatch(r"M\d+", str(row[0] or ""))]


def read_combinations(workbook):
    rows = list(workbook["Combination products"].iter_rows(values_only=True))
    headings = [str(h or "").strip() for h in rows[0]]
    return [dict(zip(headings, row)) for row in rows[1:] if re.fullmatch(r"C\d+", str(row[0] or ""))]


def write_csv(name, columns, rows):
    with open(LABELS / name, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def part_a(medicines):
    excerpts, picked = [], []
    for row in medicines:
        medicine_id, name = row["ID"], row["Medicine"]
        base = {"medicine ID": medicine_id, "medicine": name}
        if medicine_id in SUPPLEMENTS:
            picked.append(dict(base, status=NO_LABEL_SUPPLEMENT, why="Dietary supplement; FDA drug labels do not cover it."))
            continue
        for goal in TARGETS[medicine_id]:
            preferred = goal["prefer"] or (OTC if row["Applies when"] == "Self-bought or considering" else RX)
            other = OTC if preferred == RX else RX
            label, found, matching, skipped = find_label(medicine_id, goal, preferred)
            used = preferred
            if not matching and goal["allow_other_type"]:
                label, found, matching, skipped = find_label(medicine_id, goal, other)
                used = other
            looked_for = " + ".join(goal["terms"]) + (f" ({goal['note']})" if goal["note"] else "")
            if not label:
                picked.append(dict(base, searched=looked_for, status="No label found",
                                   why=f"No {goal['route'].lower()} label with exactly these ingredients and kidney text."))
                print(f"  {medicine_id} {looked_for}: NO LABEL")
                continue
            if found:
                why = f"Most recent of the matching {type_short(used)} labels ({goal['route'].lower()}, exact ingredients) with kidney text"
                if skipped:
                    why += f"; skipped {skipped} newer label(s) with no kidney text"
            else:
                why = (f"None of the {matching} most recent matching {type_short(used)} labels mention the kidneys "
                       "in the sections checked, so the most recent one is recorded as silent")
            if used != preferred:
                why += f". No {type_short(preferred)} label qualified, so the {type_short(used)} label was used"
            if goal["note"]:
                why += f". {goal['note']}"
            details = {
                "label brand name": (field(label, "brand_name") or ["(no brand listed)"])[0],
                "product type": type_short(used),
                "set_id": label.get("set_id", ""),
                "label effective date": label_date(label),
                "DailyMed link": dailymed(label),
            }
            status = "Found" if found else "Found, no kidney text"
            picked.append(dict(base, searched=looked_for, status=status, why=why, sentences=len(found), **details))
            for section, sentence in found:
                excerpts.append(dict(base, section=section, sentence=sentence, **details))
            print(f"  {medicine_id} {looked_for}: {details['label brand name'][:40]} {details['label effective date']} ({len(found)} sentences)")

    write_csv("label_excerpts.csv", ["medicine ID", "medicine", "label brand name", "product type", "set_id",
                                     "label effective date", "DailyMed link", "section", "sentence"], excerpts)
    write_csv("labels_picked.csv", ["medicine ID", "medicine", "searched", "status", "label brand name", "product type",
                                    "set_id", "label effective date", "DailyMed link", "sentences", "why"], picked)


def strip_salts(name):
    name = re.sub(r"\([^)]*\)", " ", name.lower())
    return collapse(re.sub(r",", " ", re.sub(SALT_WORDS, " ", name)))


def compare_ingredients(table_text, substances):
    if not substances:
        return "Check by hand: this label lists no ingredient data"
    ours = [strip_salts(part) for part in re.sub(r"\([^)]*\)", "", table_text).split("+")]
    theirs = [strip_salts(s) for s in substances]
    if table_text.lower().startswith("sodium phosphate"):
        theirs = ["phosphate" for _ in theirs]
        ours = ["phosphate"]
    missing = [o for o in ours if not any(o in t or t in o for t in theirs)]
    extra = [t for t, raw in zip(theirs, substances) if not any(o in t or t in o for o in ours)]
    if not missing and not extra:
        return "Match"
    parts = []
    if missing:
        parts.append("in our table but not on the label: " + ", ".join(missing))
    if extra:
        parts.append("on the label but not in our table: " + ", ".join(sorted(set(extra))))
    return "MISMATCH - " + "; ".join(parts)


def part_b_products(workbook):
    rows, alka = [], {}
    for product in read_combinations(workbook):
        product_id, name = product["ID"], product["Product"]
        table_text = product["Active ingredients"]
        words, pattern = COMBINATIONS[product_id]
        found = []
        for word in words:
            for page in range(3):
                results = search_labels(f'openfda.brand_name:"{word}"', f"{product_id}_{word}", page, limit=100, sort=False)
                found += results
                if len(results) < 100:
                    break
            if any(re.search(pattern, b, re.I) for label in found for b in field(label, "brand_name")):
                break

        matches, seen = [], set()
        for label in found:
            brands = field(label, "brand_name")
            if label.get("set_id") not in seen and any(re.search(pattern, b, re.I) for b in brands) and not is_homeopathic(label):
                if product_id != "C15" or any("PHOSPHATE" in s for s in field(label, "substance_name")):
                    seen.add(label.get("set_id"))
                    matches.append(label)
        matches.sort(key=lambda l: l.get("effective_time", ""), reverse=True)

        base = {"product ID": product_id, "product": name, "our table's ingredients": table_text,
                "searched": ", ".join(words), "labels found": len(matches)}
        if product_id == "C13":
            for label in matches:
                brand = (field(label, "brand_name") or ["(no brand listed)"])[0]
                substances = field(label, "substance_name")
                key = brand.lower()
                current = alka.get(key)
                if not current or label.get("effective_time", "") > current["_date"]:
                    alka[key] = {"product": brand, "ingredients (openFDA)": "; ".join(sorted(substances)),
                                 "active ingredient text on label": active_text(label),
                                 "contains aspirin": "Yes" if "ASPIRIN" in active_text(label).upper() or "ASPIRIN" in substances else "No",
                                 "contains acetaminophen": "Yes" if "ACETAMINOPHEN" in active_text(label).upper() or "ACETAMINOPHEN" in substances else "No",
                                 "set_id": label.get("set_id", ""), "label effective date": label_date(label),
                                 "DailyMed link": dailymed(label), "_date": label.get("effective_time", "")}
            with_aspirin = sum(1 for a in alka.values() if a["contains aspirin"] == "Yes")
            with_apap = sum(1 for a in alka.values() if a["contains acetaminophen"] == "Yes")
            rows.append(dict(base, **{"status": f"Varies: {len(alka)} distinct products. {with_aspirin} contain aspirin, {with_apap} contain acetaminophen (see list)."}))
            continue
        if not matches:
            rows.append(dict(base, status="Not found in openFDA"))
            print(f"  {product_id} {name}: NOT FOUND")
            continue
        labeled = [l for l in matches if field(l, "substance_name")] or matches
        newest = labeled[0]
        substances = field(newest, "substance_name")
        status = compare_ingredients(table_text, substances)
        variants = {tuple(sorted(strip_salts(s) for s in field(l, "substance_name"))) for l in labeled}
        if len(variants) > 1:
            status += f" (labels differ: {len(variants)} ingredient combinations found)"
        rows.append(dict(base, **{
            "label brand name": (field(newest, "brand_name") or [""])[0],
            "product type": type_short((field(newest, "product_type") or [""])[0]),
            "set_id": newest.get("set_id", ""), "label effective date": label_date(newest),
            "DailyMed link": dailymed(newest), "ingredients on label (openFDA)": "; ".join(sorted(substances)),
            "active ingredient text on label": active_text(newest), "status": status}))
        print(f"  {product_id} {name}: {status}")

    write_csv("combination_products.csv", ["product ID", "product", "our table's ingredients", "searched", "labels found",
                                           "label brand name", "product type", "set_id", "label effective date", "DailyMed link",
                                           "ingredients on label (openFDA)", "active ingredient text on label", "status"], rows)
    columns = ["product", "ingredients (openFDA)", "active ingredient text on label", "contains aspirin",
               "contains acetaminophen", "set_id", "label effective date", "DailyMed link"]
    write_csv("alka_seltzer_products.csv", columns,
              [{k: a[k] for k in columns} for a in sorted(alka.values(), key=lambda a: a["product"].lower())])


def already_covered(workbook):
    names = set()
    for row in read_medicines(workbook):
        for part in re.split(r"[,()]", f"{row['Medicine']},{row['Also called (search terms)']}"):
            if part.strip():
                names.add(part.strip().lower())
    for product in read_combinations(workbook):
        names.add(str(product["Product"]).lower())
    return names


def is_generic_name(brand):
    words = re.findall(r"[a-z]+", brand.lower())
    return all(w in GENERIC_WORDS or w in RETAILER_WORDS for w in words)


def part_b_candidates(workbook):
    covered = already_covered(workbook)
    ranked = {}
    for ingredient in CANDIDATE_INGREDIENTS:
        query = {"search": f'openfda.product_type:"{OTC}" AND openfda.substance_name:"{ingredient}"',
                 "count": "openfda.brand_name.exact", "limit": 1000}
        for entry in fetch(query, f"candidates_count_{ingredient}").get("results", []):
            brand = re.split(r"\s+-\s*|- ", entry["term"])[0].strip()
            key = brand.lower()
            squeezed = re.sub(r"[^a-z0-9]", "", key)
            if not brand or is_generic_name(brand) or key.startswith("alka") or any(
                    key == c or key.startswith(c + " ") or re.sub(r"[^a-z0-9]", "", c).startswith(squeezed)
                    for c in covered if len(c) > 3):
                continue
            item = ranked.setdefault(key, {"brand": brand, "count": 0, "ingredient": ingredient, "term": entry["term"]})
            if entry["count"] > item["count"]:     # a brand can appear under two ingredients; don't double-count it
                item.update(count=entry["count"], ingredient=ingredient, term=entry["term"])

    # Always include a few of the rarer ingredients, then fill by how common each brand is.
    ordered = sorted(ranked.values(), key=lambda i: -i["count"])
    chosen, per_ingredient = [], {}

    def details(item):
        # Different companies reuse a name like "Migraine Relief", so require the ingredient too.
        results = search_labels(f'openfda.brand_name.exact:"{item["term"]}" AND openfda.product_type:"{OTC}" '
                                f'AND openfda.substance_name:"{item["ingredient"]}"',
                                f"candidate_{item['ingredient']}_{item['term']}", limit=5, sort=False)
        usable = [l for l in results if field(l, "substance_name") and not is_homeopathic(l)]
        if item["ingredient"] == "sodium phosphate":
            # Real saline laxatives have both phosphate salts; homeopathic "Nat Phos" has one.
            usable = [l for l in usable if len(field(l, "substance_name")) >= 2
                      and all(s.startswith("SODIUM PHOSPHATE") for s in field(l, "substance_name"))]
        return usable[0] if usable else None

    def add(item):
        label = details(item)
        if not label or any(c["brand"].lower() == item["brand"].lower() for c in chosen):
            return False
        # A single-ingredient product named after that ingredient or a brand we already list
        # ("Circle K Ibuprofen", "Children's Advil") adds nothing new for review.
        name = item["brand"].lower()
        if len(field(label, "substance_name")) == 1 and (item["ingredient"] in name or any(
                re.search(r"\b" + re.escape(c) + r"\b", name) for c in covered if len(c) > 3)):
            return False
        chosen.append({"brand": item["brand"], "matched ingredient": item["ingredient"], "labels in openFDA": item["count"],
                       "ingredients (openFDA)": "; ".join(sorted(field(label, "substance_name"))),
                       "active ingredient text on label": active_text(label), "label effective date": label_date(label),
                       "DailyMed link": dailymed(label)})
        per_ingredient[item["ingredient"]] = per_ingredient.get(item["ingredient"], 0) + 1
        return True

    for ingredient in CANDIDATE_INGREDIENTS:
        for item in [i for i in ordered if i["ingredient"] == ingredient]:
            if per_ingredient.get(ingredient, 0) >= MIN_PER_INGREDIENT:
                break
            add(item)
    for item in ordered:
        if len(chosen) >= CANDIDATE_CAP:
            break
        add(item)

    chosen.sort(key=lambda c: (-c["labels in openFDA"], c["brand"].lower()))
    write_csv("candidate_additions.csv", ["brand", "matched ingredient", "labels in openFDA", "ingredients (openFDA)",
                                          "active ingredient text on label", "label effective date", "DailyMed link"], chosen)
    print(f"  {len(chosen)} candidate products")


def main():
    workbook = openpyxl.load_workbook(XLSX, read_only=True, data_only=True)
    LABELS.mkdir(exist_ok=True)
    medicines = read_medicines(workbook)
    missing = [m["ID"] for m in medicines if m["ID"] not in TARGETS and m["ID"] not in SUPPLEMENTS]
    if missing:
        sys.exit(f"These rows have no search set up in TARGETS: {missing}")

    print("Part A: medicine labels")
    part_a(medicines)
    print("Part B: combination products")
    part_b_products(workbook)
    print("Part B: candidate additions")
    part_b_candidates(workbook)

    files = list(RAW.glob("*.json"))
    dates = sorted(datetime.date.fromtimestamp(f.stat().st_mtime).isoformat() for f in files)
    (LABELS / "run_info.json").write_text(
        json.dumps({"labels_pulled_first": dates[0], "labels_pulled_last": dates[-1], "raw_files": len(files)}, indent=1),
        encoding="utf-8")
    print(f"Done. {len(files)} saved responses in {RAW}")


if __name__ == "__main__":
    main()
