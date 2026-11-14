/* The medical logic. No screens here, so tests.html can check exactly the code
   patients use. All medical content comes from data/table.json. */
window.Engine = (function () {
  "use strict";

  // Strictest first. "NA" sits above "OK" so that a product can never read
  // "No change" when part of it could not be assessed.
  var STRICTNESS = ["AVOID", "ASK", "DOSE", "NA", "OK"];
  var TEXT = window.KMC_TEXT || {};
  var NOT_ON_LIST = TEXT.notOnList;
  var DOSE_LABEL = TEXT.doseLabel;
  var MAX_SUGGESTIONS = 3;
  var ANSWERS = ["yes", "no", "unsure"];

  function compact(text) {
    return String(text).toLowerCase().replace(/[^a-z0-9]/g, "");
  }

  function stageFromEgfr(egfr) {
    if (egfr >= 60) return "G1-2";
    if (egfr >= 45) return "G3a";
    if (egfr >= 30) return "G3b";
    if (egfr >= 15) return "G4";
    return "G5";
  }

  /* --- Search --- */

  function score(query, term) {
    var target = compact(term);
    if (!target) return 0;
    if (target === query) return 100;
    if (target.indexOf(query) === 0) return 80;
    if (target.indexOf(query) > 0) return 60;
    // "ibuprofen 200 mg" should still find "ibuprofen".
    if (target.length >= 4 && query.indexOf(target) >= 0) return 40;
    return 0;
  }

  function search(table, text) {
    var query = compact(text);
    if (query.length < 2) return [];
    var hits = [];

    table.families.forEach(function (family) {
      var best = score(query, family.label) && score(query, family.label) + 5;
      var via = null;
      family.aliases.forEach(function (alias) {
        alias.search.forEach(function (term) {
          var s = score(query, term);
          if (s > best) { best = s; via = alias; }
        });
      });
      if (best) {
        hits.push({
          kind: "med",
          key: family.key,
          title: via ? via.display : family.label,
          detail: via ? family.label : family.aliases.map(function (a) { return a.display; }).join(", "),
          aliasContains: via ? via.contains : [],
          score: best
        });
      }
    });

    table.combinations.forEach(function (combo) {
      var best = [combo.product].concat(combo.also_search).reduce(function (top, term) {
        return Math.max(top, score(query, term));
      }, 0);
      if (best) {
        hits.push({ kind: "combo", key: combo.id, title: combo.product,
                    detail: "Contains: " + combo.ingredients, aliasContains: [], score: best });
      }
    });

    // An exact name hides names that only contain it in the middle:
    // "acyclovir" must not offer "valacyclovir", a different medicine.
    var exact = hits.some(function (h) { return h.score >= 100; });
    var direct = hits.filter(function (h) { return h.score >= (exact ? 80 : 60); });
    return (direct.length ? direct : hits).sort(function (a, b) { return b.score - a.score; });
  }

  function exactMatch(table, text) {
    var top = search(table, text)[0];
    return top && top.score >= 100 ? top : null;
  }

  /* --- "Did you mean…?" Only ever offers choices to tap. search() and
     exactMatch() never use this, so a misspelling can't turn into a medicine
     unless the patient picks it. --- */

  // Letters that must change to turn one word into the other. Swapping two
  // neighboring letters counts as one change.
  function editDistance(a, b) {
    var d = [];
    for (var i = 0; i <= a.length; i++) {
      d[i] = [i];
      for (var j = 1; j <= b.length; j++) {
        if (i === 0) { d[i][j] = j; continue; }
        var cost = a[i - 1] === b[j - 1] ? 0 : 1;
        d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost);
        if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) {
          d[i][j] = Math.min(d[i][j], d[i - 2][j - 2] + 1);
        }
      }
    }
    return d[a.length][b.length];
  }

  // Kept tight because many real medicine names differ by only a few letters.
  function allowedEdits(length) {
    if (length < 4) return 0;
    if (length <= 6) return 1;
    if (length <= 11) return 2;
    return 3;
  }

  // The closest this name comes to the query. Matching only the first word
  // ("exedrin" for "Excedrin Extra Strength") ranks just behind a whole name.
  function closeness(query, name) {
    var first = name.trim().split(/\s+/)[0];
    return Math.min(editDistance(query, compact(name)),
                    first === name.trim() ? Infinity : editDistance(query, compact(first)) + 0.5);
  }

  function suggest(table, text) {
    var query = compact(text);
    var found = search(table, text);
    // A normal search match is already on screen, so there is nothing to guess.
    if (found.some(function (h) { return h.score >= 60; })) return [];
    var limit = allowedEdits(query.length) + 0.5;
    var shown = found.map(function (h) { return h.kind + h.key; });
    var ideas = [];

    function consider(kind, key, title, detail, aliasContains, name) {
      var distance = compact(name).length >= 4 ? closeness(query, name) : Infinity;
      if (distance > limit || shown.indexOf(kind + key) >= 0) return;
      // People rarely get the first letter wrong. Without this, fluoxetine (a
      // different medicine) would suggest duloxetine.
      if (distance >= 2 && compact(name)[0] !== query[0]) return;
      var existing = ideas.filter(function (i) { return i.kind === kind && i.key === key; })[0];
      if (existing && existing.distance <= distance) return;
      if (existing) ideas.splice(ideas.indexOf(existing), 1);
      ideas.push({ kind: kind, key: key, title: title, detail: detail, aliasContains: aliasContains, distance: distance });
    }

    table.families.forEach(function (family) {
      // "Aspirin – high dose (325 mg or more)" is compared as just "Aspirin".
      var base = family.label.split(/\s[–-]\s|\s*\(/)[0];
      var aliasNames = family.aliases.map(function (a) { return a.display; }).join(", ");
      consider("med", family.key, family.label, aliasNames, [], base);
      family.aliases.forEach(function (alias) {
        alias.search.forEach(function (term) {
          consider("med", family.key, alias.display, family.label, alias.contains, term);
        });
      });
    });
    table.combinations.forEach(function (combo) {
      [combo.product].concat(combo.also_search).forEach(function (term) {
        consider("combo", combo.id, combo.product, "Contains: " + combo.ingredients, [], term);
      });
    });

    return ideas.sort(function (a, b) { return a.distance - b.distance; }).slice(0, MAX_SUGGESTIONS);
  }

  function typedItem(text) {
    return { kind: "typed", key: compact(text), title: String(text).trim(), detail: "", aliasContains: [] };
  }

  /* --- Which row(s) of the table apply --- */

  function chooseRow(table, family, source) {
    var rows = family.rows.map(function (id) { return table.medicines[id]; });
    var wanted = source === "prescribed" ? "Prescribed" : "Self";
    return rows.filter(function (r) { return r.applies === wanted; })[0] ||
           rows.filter(function (r) { return r.applies === "Any"; })[0] ||
           rows[0];
  }

  function familyFor(table, item) {
    return table.families.filter(function (f) { return f.key === item.key; })[0];
  }

  function comboFor(table, item) {
    return item.kind === "combo" ? table.combinations.filter(function (c) { return c.id === item.key; })[0] : null;
  }

  function rowsFor(table, item) {
    if (item.kind === "med") {
      var family = familyFor(table, item);
      return family ? [chooseRow(table, family, item.source)] : [];
    }
    var combo = comboFor(table, item);
    return combo ? combo.rules.map(function (id) { return table.medicines[id]; }) : [];
  }

  // The question some products need before they can be checked (Alka-Seltzer: does it contain aspirin?).
  function questionFor(table, item) {
    var combo = comboFor(table, item);
    return combo && combo.followup ? combo.followup.question : null;
  }

  function answerFor(combo, item) {
    var key = ANSWERS.indexOf(item.answer) >= 0 ? item.answer : "unsure";
    return { key: key, rule: combo.followup.answers[key] };
  }

  function codeFor(row, ctx) {
    var code = row.stages[ctx.stage];
    if (code !== "EGFR") return code;
    var rule = row.egfr_followup;
    if (ctx.egfr === null || ctx.egfr === undefined) return rule.unknown;
    return ctx.egfr >= rule.threshold ? rule.at_or_above : rule.below;
  }

  function needsEgfr(table, ctx, items) {
    // Decision 7: eGFR can't be measured accurately on dialysis, so never ask for it there.
    if (ctx.stage === "Dialysis" || ctx.stage === "Transplant") return false;
    if ((ctx.egfr !== null && ctx.egfr !== undefined) || ctx.egfrUnknown) return false;
    return items.some(function (item) {
      return rowsFor(table, item).some(function (r) { return r.stages[ctx.stage] === "EGFR"; }) &&
             !(comboFor(table, item) && (comboFor(table, item).stages || comboFor(table, item).followup));
    });
  }

  /* --- One result per medicine --- */

  function strictest(codes) {
    return STRICTNESS.filter(function (c) { return codes.indexOf(c) >= 0; })[0];
  }

  // A row can have its own note for one result (vitamins: "High dose may be unsafe…" when Consult doctor).
  function noteFor(row, code) {
    return (row.notes_by_result && row.notes_by_result[code]) || row.note;
  }

  function addNote(notes, note) {
    if (note && notes.indexOf(note) < 0) notes.push(note);
  }

  function evaluate(table, ctx, item) {
    var rows = rowsFor(table, item);
    var combo = comboFor(table, item);
    var code, notes = [], answer = null;

    if (combo && combo.followup) {
      // The patient's answer decides. A kidney transplant still can't be assessed (Decision 7).
      answer = answerFor(combo, item);
      code = ctx.stage === "Transplant" ? "NA" : answer.rule.code;
      if (answer.key !== "no") rows.forEach(function (r) { addNote(notes, r.note); });
    } else if (combo && combo.stages) {
      // This product has its own stage results; its ingredient notes still explain why.
      code = combo.stages[ctx.stage];
      rows.forEach(function (r) { addNote(notes, r.note); });
    } else if (rows.length) {
      var codes = rows.map(function (r) { return codeFor(r, ctx); });
      code = strictest(codes);
      rows.forEach(function (row, i) {
        // For combinations, explain the ingredient(s) behind the result. Otherwise
        // Excedrin's card could say acetaminophen is "the safest" right under a warning.
        if (codes[i] === code) addNote(notes, noteFor(row, codes[i]));
      });
    } else if (combo) {
      code = combo.stages ? combo.stages[ctx.stage] : "NA";
    } else {
      code = "NA";
      notes.push(NOT_ON_LIST);
    }
    var category = table.categories[code];

    return {
      item: item,
      code: code,
      label: category.label,
      headline: code === "NA" && ctx.stage === "Transplant" ? table.transplant_message : category.headline,
      explanation: category.explanation,
      notes: notes,
      alert: combo && combo.alert ? combo.alert : null,
      answer: answer ? answer.key : null,
      safety: item.source === "prescribed" && code !== "OK" ? table.safety_message : null,
      checkedAs: item.kind === "med" && rows[0] && rows[0].name !== item.title ? rows[0].name : null
    };
  }

  /* --- Double-up alerts --- */

  // What an item contains for the double-up alerts. "maybe" holds ingredients it might
  // contain (Alka-Seltzer answered "Not sure"), so the heading can say "may contain".
  function ingredientsOf(table, item) {
    var sure = item.aliasContains || [], maybe = [];
    if (item.kind === "med") {
      var family = familyFor(table, item);
      if (family) sure = sure.concat(family.contains);
    } else if (item.kind === "combo") {
      var combo = comboFor(table, item);
      if (combo) {
        sure = sure.concat(combo.contains);
        if (combo.followup) {
          var rule = answerFor(combo, item).rule;
          sure = sure.concat(rule.counts || []);
          maybe = rule.maybe || [];
        }
      }
    }
    return { sure: sure, maybe: maybe };
  }

  // "an NSAID or aspirin" narrows to "an NSAID" or "aspirin" when that is all
  // the listed items actually share.
  function sharedPhrase(alert, ingredients) {
    var options = alert.phrase.split(" or ");
    function namesIngredient(option, ingredient) { return option.toLowerCase().indexOf(ingredient) >= 0; }
    var generic = options.filter(function (o) {
      return !alert.ingredients.some(function (i) { return namesIngredient(o, i); });
    })[0];
    var used = [];
    ingredients.forEach(function (ingredient) {
      var phrase = options.filter(function (o) { return namesIngredient(o, ingredient); })[0] || generic || alert.phrase;
      if (used.indexOf(phrase) < 0) used.push(phrase);
    });
    return used.length === 1 ? used[0] : alert.phrase;
  }

  function doubleUps(table, items) {
    var alerts = [];
    function inAlert(alert, list) {
      return list.filter(function (i) { return alert.ingredients.indexOf(i) >= 0; });
    }
    table.alerts.forEach(function (alert) {
      var ingredients = [], parts = [], anyMaybe = false;
      var matched = items.filter(function (item) {
        var found = ingredientsOf(table, item);
        var sure = inAlert(alert, found.sure);
        var maybe = sure.length ? [] : inAlert(alert, found.maybe);
        if (!sure.length && !maybe.length) return false;
        ingredients = ingredients.concat(sure, maybe);
        anyMaybe = anyMaybe || maybe.length > 0;
        parts.push(item.title + (sure.length ? " contains " + sharedPhrase(alert, sure) : " may contain " + sharedPhrase(alert, maybe)));
        return true;
      });
      if (matched.length < 2) return;
      var names = matched.map(function (i) { return i.title; }).join(" + ");
      alerts.push({
        id: alert.id,
        code: alert.code,
        label: table.categories[alert.code].label,
        text: alert.text,
        heading: anyMaybe
          // "Advil + Alka-Seltzer: Advil contains an NSAID, and Alka-Seltzer may contain aspirin"
          ? names + ": " + parts.slice(0, -1).join(", ") + (parts.length > 2 ? "," : "") + " and " + parts[parts.length - 1]
          : names + ": " + (matched.length === 2 ? "both" : "all") + " contain " + sharedPhrase(alert, ingredients)
      });
    });
    return alerts;
  }

  return { DOSE_LABEL: DOSE_LABEL, stageFromEgfr: stageFromEgfr, search: search, exactMatch: exactMatch,
           suggest: suggest, typedItem: typedItem, questionFor: questionFor, needsEgfr: needsEgfr,
           evaluate: evaluate, doubleUps: doubleUps };
})();
