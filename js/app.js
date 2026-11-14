/* The screens. Medical decisions are made in engine.js; wording is in text.js.
   Nothing here sends data anywhere: the only network request is for the site's own data/table.json. */
(function () {
  "use strict";

  var T = window.KMC_TEXT;
  if (!T) throw new Error("js/text.js did not load");   // the message in index.html takes over

  // Where the visitor came from (?src=handout). Kept for future visit counting; not sent anywhere.
  var visitSource = new URLSearchParams(window.location.search).get("src");
  window.kmcVisitSource = visitSource;

  var STORAGE_KEY = "kidney-med-checker-v0";
  var STAGE_KEYS = ["G1-2", "G3a", "G3b", "G4", "G5", "Dialysis", "Transplant", "Unknown"];
  var STAGES = STAGE_KEYS.map(function (key, i) { return [key, T.stageOptions[i]]; });
  var SOURCES = ["prescribed", "bought", "considering"].map(function (key, i) { return [key, T.sourceOptions[i]]; });
  var ANSWERS = ["yes", "no", "unsure"];
  var ICONS = {
    OK: '<circle cx="12" cy="12" r="11" fill="currentColor"/><path d="M6.5 12.5l3.8 3.8 7.2-8" fill="none" stroke="#fff" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>',
    DOSE: '<g transform="rotate(-45 12 12)"><rect x="1" y="7" width="22" height="10" rx="5" fill="currentColor"/><path d="M12 7v10" stroke="#fff" stroke-width="2.2"/></g>',
    ASK: '<path d="M12 1.5L23.5 22H.5z" fill="currentColor"/><path d="M12 9v6.5" stroke="#fff" stroke-width="2.6" stroke-linecap="round"/><circle cx="12" cy="18.7" r="1.5" fill="#fff"/>',
    AVOID: '<path d="M7.4 1h9.2L23 7.4v9.2L16.6 23H7.4L1 16.6V7.4z" fill="currentColor"/><path d="M8 8l8 8M16 8l-8 8" stroke="#fff" stroke-width="2.8" stroke-linecap="round"/>',
    NA: '<circle cx="12" cy="12" r="11" fill="currentColor"/><path d="M8.8 9.2a3.3 3.3 0 1 1 5.2 2.7c-1.2.8-2 1.5-2 3" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round"/><circle cx="12" cy="18.3" r="1.5" fill="#fff"/>'
  };

  var table = null;
  var app = document.getElementById("app");
  var state = freshState();
  var stack = ["stage"];       // the screens the patient has walked through
  var pending = null;          // the medicine being added or edited right now
  var smallText = false;

  function freshState() {
    return { stage: null, stageChosen: null, egfr: null, egfrDate: "", egfrUnknown: false, items: [], remember: false };
  }

  /* --- Small helpers --- */

  // Builds page elements. Text always goes in as plain text, so whatever a
  // patient types can never act as code on the page.
  function h(tag, props) {
    var node = document.createElement(tag);
    Object.keys(props || {}).forEach(function (key) {
      if (key === "class") node.className = props[key];
      else if (key === "onclick" || key === "oninput" || key === "onsubmit" || key === "onchange") node[key] = props[key];
      else if (key === "checked" || key === "disabled") node[key] = props[key];
      else node.setAttribute(key, props[key]);
    });
    Array.prototype.slice.call(arguments, 2).forEach(function add(child) {
      if (child === null || child === undefined || child === false) return;
      if (Array.isArray(child)) return child.forEach(add);
      node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
    });
    return node;
  }

  function button(label, onclick, cls) {
    return h("button", { type: "button", class: "btn " + (cls || ""), onclick: onclick }, label);
  }

  // A choice button that shows the current answer when an item is being edited.
  function choice(label, chosen, onclick) {
    return h("button", { type: "button", class: "btn" + (chosen ? " selected" : ""), "aria-pressed": chosen ? "true" : "false",
                         onclick: onclick }, chosen ? "✓ " + label : label);
  }

  function icon(code) {
    var span = h("span", { class: "icon", "aria-hidden": "true" });
    span.innerHTML = '<svg viewBox="0 0 24 24" width="100%" height="100%">' + ICONS[code] + "</svg>";
    return span;
  }

  function title(text) {
    return h("h2", { id: "screen-title", tabindex: "-1" }, text);
  }

  function errorLine() {
    return h("p", { class: "error", role: "alert", hidden: "hidden" });
  }

  function showError(node, message) {
    node.textContent = message;
    node.hidden = false;
  }

  function stageName(stage) {
    var found = STAGES.filter(function (s) { return s[0] === stage; })[0];
    if (!found) return "";
    return stage === "Unknown" ? "Stage not known" : found[1];
  }

  function sourceName(source) {
    var found = SOURCES.filter(function (s) { return s[0] === source; })[0];
    return found ? found[1] : "";
  }

  function itemDetail(item) {
    var parts = [sourceName(item.source)];
    if (Engine.questionFor(table, item)) parts.push(T.answerShown[Math.max(0, ANSWERS.indexOf(item.answer || "unsure"))]);
    if (item.dose) parts.push(item.dose);
    return parts.join(" · ");
  }

  function niceDate(value) {
    var parts = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || "");
    var date = parts ? new Date(+parts[1], +parts[2] - 1, +parts[3]) : new Date();
    return date.toLocaleDateString("en-US", { year: "numeric", month: "long", day: "numeric" });
  }

  function todayValue() {
    var now = new Date();
    return now.getFullYear() + "-" + String(now.getMonth() + 1).padStart(2, "0") + "-" + String(now.getDate()).padStart(2, "0");
  }

  function stageLine() {
    var line = stageName(state.stage);
    if (state.egfr !== null) {
      line += " (from your eGFR of " + state.egfr + (state.egfrDate ? ", dated " + niceDate(state.egfrDate) : "") + ")";
    }
    return line;
  }

  // Returns the number, or null if the text is not a believable eGFR.
  function readEgfr(text) {
    var value = String(text).trim();
    if (!/^\d+(\.\d+)?$/.test(value)) return null;
    var number = parseFloat(value);
    return number > 0 && number <= 200 ? number : null;
  }

  /* --- Moving between screens. Each step is also a browser history entry, so
     the phone's own Back button does the same thing as ours. --- */

  function go(screen) {
    stack.push(screen);
    history.pushState({ depth: stack.length - 1 }, "");
    render();
  }

  // Replace the whole path, e.g. after "Check them again": stage → medicines → results.
  function resetTo(screens) {
    stack = screens.slice();
    history.replaceState({ depth: 0 }, "");
    for (var i = 1; i < stack.length; i++) history.pushState({ depth: i }, "");
    render();
  }

  window.addEventListener("popstate", function (event) {
    var depth = event.state && typeof event.state.depth === "number" ? event.state.depth : 0;
    stack.length = Math.min(stack.length, depth + 1);
    render();
  });

  function showResults() {
    go(Engine.needsEgfr(table, state, state.items) ? "followup" : "results");
  }

  /* --- Keep the list on this phone. Any of this can fail (private browsing,
     blocked storage), and the site must carry on normally if it does. --- */

  function storageWrite(value) {
    try {
      if (value === null) window.localStorage.removeItem(STORAGE_KEY);
      else window.localStorage.setItem(STORAGE_KEY, value);
      return true;
    } catch (e) {
      return false;
    }
  }

  function saveIfRemembering() {
    if (state.remember && !storageWrite(JSON.stringify(state))) state.remember = false;
  }

  function loadSaved() {
    try {
      var saved = JSON.parse(window.localStorage.getItem(STORAGE_KEY));
      var known = STAGES.some(function (s) { return saved && s[0] === saved.stage; });
      if (known && Array.isArray(saved.items) && saved.items.length) {
        state = Object.assign(freshState(), saved, { remember: true });
        return true;
      }
    } catch (e) {
      return false;
    }
    return false;
  }

  function clearEverything() {
    storageWrite(null);
    state = freshState();
    pending = null;
    resetTo(["stage"]);
  }

  /* --- The "My medicines" list, shown on the medicines screen and on the results page --- */

  function startEdit(index) {
    pending = Object.assign({}, state.items[index], { editIndex: index, returnDepth: stack.length - 1 });
    go(Engine.questionFor(table, pending) ? "answer" : "source");
  }

  function myMedicines() {
    var box = h("section", { class: "my-list", "aria-labelledby": "my-list-title" },
      h("h3", { id: "my-list-title" }, T.myMedicines + " (" + state.items.length + ")"));
    if (!state.items.length) box.appendChild(h("p", { class: "hint" }, T.emptyList));
    state.items.forEach(function (item, index) {
      box.appendChild(h("div", { class: "list-row" },
        h("div", { class: "list-name" }, h("strong", null, item.title), h("span", { class: "detail" }, itemDetail(item))),
        h("div", { class: "list-actions" },
          h("button", { type: "button", class: "btn small", "aria-label": "Edit " + item.title,
                        onclick: function () { startEdit(index); } }, "Edit"),
          h("button", { type: "button", class: "btn small", "aria-label": "Remove " + item.title, onclick: function () {
            state.items.splice(index, 1);
            saveIfRemembering();
            render();
          } }, "Remove"))));
    });
    return box;
  }

  /* --- Screens --- */

  var screens = {};

  screens.welcome = function () {
    var n = state.items.length;
    return [
      title(T.welcome.replace("{n}", n + (n === 1 ? " medicine" : " medicines"))),
      button(T.welcomeAgain, function () {
        stack = ["stage", "meds"];
        resetTo(["stage", "meds"]);
        showResults();
      }, "primary"),
      button(T.welcomeNew, clearEverything)
    ];
  };

  screens.stage = function () {
    var body = [title(T.stageQuestion)];
    STAGES.forEach(function (stage) {
      body.push(choice(stage[1], state.stage === stage[0] && state.egfr === null && state.items.length > 0, function () {
        state.stage = state.stageChosen = stage[0];
        state.egfr = null;
        state.egfrDate = "";
        state.egfrUnknown = false;
        saveIfRemembering();
        if (state.items.length) showResults();
        else go("meds");
      }));
    });
    body.push(h("p", { class: "or" }, T.egfrOffer));
    body.push(button(T.egfrOfferButton, function () { go("egfr"); }, "secondary"));
    return body;
  };

  screens.egfr = function () {
    var number = h("input", { id: "egfr", type: "text", inputmode: "decimal", autocomplete: "off",
                              value: state.egfr === null ? "" : String(state.egfr) });
    var date = h("input", { id: "egfr-date", type: "date", max: todayValue(), value: state.egfrDate });
    var error = errorLine();
    return [
      title(T.egfrQuestion),
      h("p", null, T.egfrIntro),
      h("p", { class: "hint" }, T.egfrDialysisNote),
      h("form", { novalidate: "novalidate", onsubmit: function (event) {
        event.preventDefault();
        var value = readEgfr(number.value);
        if (value === null) return showError(error, T.egfrError);
        state.egfr = value;
        state.egfrDate = date.value;
        state.egfrUnknown = false;
        state.stage = state.stageChosen = Engine.stageFromEgfr(value);
        saveIfRemembering();
        if (state.items.length) showResults();
        else go("meds");
      } },
        h("label", { for: "egfr" }, "eGFR number"), number,
        h("label", { for: "egfr-date" }, T.egfrDateLabel), date,
        error,
        h("button", { type: "submit", class: "btn primary" }, "Continue"))
    ];
  };

  function chooseMedicine(item) {
    pending = Object.assign({}, item, { returnDepth: stack.length - 1 });
    delete pending.score;
    go(Engine.questionFor(table, pending) ? "answer" : "source");
  }

  screens.meds = function () {
    var input = h("input", { id: "search", type: "search", autocomplete: "off", autocapitalize: "off",
                             spellcheck: "false", maxlength: "80" });
    var results = h("div", { class: "search-results", "aria-live": "polite" });
    var message = errorLine();

    function isListed(item) {
      return state.items.some(function (i) { return i.kind === item.kind && i.key === item.key && i.title === item.title; });
    }

    function pick(item) {
      if (isListed(item)) return showError(message, item.title + " is already on your list.");
      chooseMedicine(item);
    }

    function showMatches() {
      results.textContent = "";
      message.hidden = true;
      var text = input.value.trim();
      if (text.replace(/[^a-z0-9]/gi, "").length < 2) return;
      function addOption(hit) {
        results.appendChild(h("button", { type: "button", class: "btn result-option", onclick: function () { pick(hit); } },
          h("strong", null, hit.title),
          hit.detail ? h("span", { class: "detail" }, hit.detail) : null));
      }
      Engine.search(table, text).slice(0, 8).forEach(addOption);
      var ideas = Engine.suggest(table, text);
      if (ideas.length) {
        results.appendChild(h("h3", { class: "did-you-mean" }, T.didYouMean));
        results.appendChild(h("p", { class: "hint" }, T.didYouMeanHint));
        ideas.forEach(addOption);
      }
      results.appendChild(button(T.notListed.replace("{name}", text), function () {
        pick(Engine.exactMatch(table, text) || Engine.typedItem(text));
      }, "secondary"));
    }
    input.oninput = showMatches;

    return [
      h("p", { class: "stage-line" }, "Your kidney stage: ", h("strong", null, stageLine())),
      title(state.items.length ? T.searchQuestionMore : T.searchQuestion),
      h("form", { onsubmit: function (event) {
        event.preventDefault();
        var exact = Engine.exactMatch(table, input.value);
        if (exact) pick(exact);
        else input.blur();
      } },
        h("label", { for: "search" }, T.searchLabel), input),
      message, results,
      myMedicines(),
      state.items.length ? button(T.seeResults, showResults, "primary") : null
    ];
  };

  screens.answer = function () {
    var question = Engine.questionFor(table, pending);
    return [
      h("p", { class: "stage-line" }, h("strong", null, pending.title)),
      title(question),
      ANSWERS.map(function (key, i) {
        return choice(T.answerOptions[i], pending.editIndex !== undefined && pending.answer === key, function () {
          pending.answer = key;
          go("source");
        });
      })
    ];
  };

  screens.source = function () {
    var body = [
      h("p", { class: "stage-line" }, h("strong", null, pending.title)),
      title(T.sourceQuestion)
    ];
    SOURCES.forEach(function (source) {
      body.push(choice(source[1], pending.editIndex !== undefined && pending.source === source[0], function () {
        pending.source = source[0];
        go("dose");
      }));
    });
    return body;
  };

  screens.dose = function () {
    var input = h("input", { id: "dose", type: "text", autocomplete: "off", maxlength: "60", value: pending.dose || "" });
    var editing = pending.editIndex !== undefined;
    return [
      h("p", { class: "stage-line" }, h("strong", null, pending.title)),
      title(T.doseQuestion),
      h("form", { onsubmit: function (event) {
        event.preventDefault();
        var item = pending;
        item.dose = input.value.trim();
        var back = stack.length - 1 - item.returnDepth;
        if (editing) state.items[item.editIndex] = item;
        else state.items.push(item);
        delete item.editIndex;
        delete item.returnDepth;
        pending = null;
        saveIfRemembering();
        history.go(-back);     // back to the list (or results); the add/edit screens are finished
      } },
        h("label", { for: "dose" }, T.doseLabel), input,
        h("p", { class: "hint" }, T.doseHint),
        h("button", { type: "submit", class: "btn primary" }, editing ? "Save changes" : "Add to my list"))
    ];
  };

  screens.followup = function () {
    var number = h("input", { id: "egfr-followup", type: "text", inputmode: "decimal", autocomplete: "off",
                              value: state.egfr === null ? "" : String(state.egfr) });
    var error = errorLine();
    return [
      title(T.egfrQuestion),
      h("p", null, T.followupIntro),
      h("form", { novalidate: "novalidate", onsubmit: function (event) {
        event.preventDefault();
        var value = readEgfr(number.value);
        if (value === null) return showError(error, T.followupError);
        state.egfr = value;
        state.egfrUnknown = false;
        state.stage = Engine.stageFromEgfr(value);
        saveIfRemembering();
        go("results");
      } },
        h("label", { for: "egfr-followup" }, "eGFR number"), number,
        error,
        h("button", { type: "submit", class: "btn primary" }, "Continue")),
      button(T.dontKnow, function () {
        state.egfr = null;
        state.egfrDate = "";
        state.egfrUnknown = true;
        state.stage = state.stageChosen;
        saveIfRemembering();
        go("results");
      })
    ];
  };

  function outcomes() {
    return state.items.map(function (item) { return Engine.evaluate(table, state, item); });
  }

  function alertBox(alert) {
    return h("div", { class: "alert r-" + alert.code, role: "note" },
      h("h3", null, alert.heading),
      h("div", { class: "headline" }, icon(alert.code), h("strong", null, alert.label)),
      h("p", null, alert.text));
  }

  screens.results = function () {
    saveIfRemembering();
    var status = h("p", { class: "hint", role: "status" });
    // Results first; the list and the buttons come after them.
    var body = [
      title("Your results"),
      h("p", { class: "stage-line" }, "Your kidney stage: ", h("strong", null, stageLine())),
      h("button", { type: "button", class: "link-button top-add", onclick: function () { go("meds"); } }, T.addAnother)
    ];
    Engine.doubleUps(table, state.items).forEach(function (alert) { body.push(alertBox(alert)); });

    outcomes().forEach(function (result) {
      body.push(h("article", { class: "card r-" + result.code },
        h("h3", null, result.item.title),
        h("p", { class: "detail" }, itemDetail(result.item)),
        result.checkedAs ? h("p", { class: "detail" }, "Checked as: " + result.checkedAs) : null,
        h("div", { class: "headline" }, icon(result.code), h("strong", null, result.headline)),
        h("p", null, result.explanation),
        result.safety ? h("p", { class: "safety" }, result.safety) : null,
        result.alert ? h("p", null, h("strong", null, result.alert)) : null,
        result.notes.map(function (note) { return h("p", null, note); })));
    });

    body.push(myMedicines());
    body.push(button(T.addAnother, function () { go("meds"); }, "primary"));
    body.push(button(T.changeStage, function () { go("stage"); }, "secondary"));
    if (state.items.length) body.push(button(T.printButton, function () { go("summary"); }, "secondary"));
    body.push(h("div", { class: "keep" },
      h("label", { class: "check" },
        h("input", { type: "checkbox", checked: state.remember, onchange: function (event) {
          state.remember = event.target.checked;
          if (!state.remember) {
            storageWrite(null);
            status.textContent = "";
          } else if (!storageWrite(JSON.stringify(state))) {
            state.remember = false;
            event.target.checked = false;
            status.textContent = T.keepBlocked;
          }
        } }),
        h("span", null, T.keepLabel)),
      h("p", { class: "hint keep-line" }, T.keepLine),
      status));
    body.push(h("div", { class: "danger-zone" },
      h("button", { type: "button", class: "link-button", onclick: function () { go("clear"); } }, T.clearLink)));
    return body;
  };

  screens.clear = function () {
    return [
      title(T.clearQuestion),
      state.remember ? h("p", null, h("strong", null, T.clearSavedLine)) : null,
      button(T.clearYes, clearEverything, "danger"),
      button(T.clearNo, function () { history.back(); }, "primary")
    ];
  };

  screens.summary = function () {
    var list = h("div", { class: "summary-list" });
    outcomes().forEach(function (result) {
      list.appendChild(h("div", { class: "summary-row r-" + result.code },
        h("div", null, h("strong", null, result.item.title),
          h("span", { class: "detail" }, [result.checkedAs, itemDetail(result.item)].filter(Boolean).join(" · "))),
        h("div", { class: "headline" }, icon(result.code), h("strong", null, result.label))));
    });
    return [
      title("Summary for my doctor"),
      h("p", null, h("strong", null, "Date: "), niceDate()),
      h("p", null, h("strong", null, "Kidney stage: "), stageLine()),
      Engine.doubleUps(table, state.items).map(alertBox),
      list,
      button("Print this page", function () { window.print(); }, "primary no-print")
    ];
  };

  function render() {
    var name = stack[stack.length - 1];
    // A safety net for screens that cannot be shown any more (e.g. after clearing the list).
    if ((name === "source" || name === "dose" || name === "answer") && !pending) name = state.stage ? "meds" : "stage";
    if (["stage", "egfr", "welcome"].indexOf(name) < 0 && !state.stage) name = "stage";

    app.textContent = "";
    app.setAttribute("data-loaded", "yes");
    if (stack.length > 1) {
      app.appendChild(button("‹ Back", function () { history.back(); }, "back no-print"));
    }
    screens[name]().forEach(function add(node) {
      if (Array.isArray(node)) return node.forEach(add);
      if (node) app.appendChild(node);
    });
    window.scrollTo(0, 0);
    var heading = document.getElementById("screen-title");
    if (heading) heading.focus({ preventScroll: true });
  }

  /* --- Start up --- */

  document.getElementById("text-size").onclick = function () {
    smallText = !smallText;
    document.documentElement.classList.toggle("small-text", smallText);
    this.textContent = smallText ? "Larger text" : "Smaller text";
  };

  fetch("data/table.json", { cache: "no-cache" })
    .then(function (response) {
      if (!response.ok) throw new Error("status " + response.status);
      return response.json();
    })
    .then(function (data) {
      table = data;
      resetTo([loadSaved() ? "welcome" : "stage"]);
    })
    .catch(function () {
      app.textContent = "";
      app.setAttribute("data-loaded", "yes");
      app.appendChild(h("p", { class: "error" }, "Sorry, the medicine list did not load. Please refresh this page."));
    });
})();
