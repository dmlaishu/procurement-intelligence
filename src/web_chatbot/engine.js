/* Procurement assistant engine: a browser port of procurement_demo/chatbot.py.
 * Pure functions over the embedded data (no DOM), so it can be tested in Node.
 *   makeEngine(D) -> { ask(question), tools, verify, ... }
 * Every answer is a list of statements that cite evidence ids; verify() drops any statement whose numbers or quotes
 * are not in the evidence it cites (the same gate as notebooks/20_proc_lib.py). */
(function (root) {
  "use strict";

  // ── text helpers ──────────────────────────────────────────────────────────────────────────────
  const money = (x) => "US$" + Math.round(x).toLocaleString("en-US");
  const pct = (x) => Math.round(x * 100) + "%";
  const plural = (n, one, many) => `${n} ${n === 1 ? one : (many || one + "s")}`;
  const NUM_RE = /(?<![\w\[])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?![\w\]])/g;

  function numbersIn(text) {
    return [...String(text || "").matchAll(NUM_RE)].map((m) => parseFloat(m[1].replace(/,/g, "")));
  }

  function unsupportedNumbers(answer, evidenceTexts, relTol = 0.02) {
    const pool = evidenceTexts.flatMap(numbersIn);
    const bad = new Set();
    for (const x of numbersIn(String(answer || "").replace(/\[[^\]]*\]/g, " "))) {
      if (x < 10 && Number.isInteger(x)) continue;               // small counts and list numbering
      if (!pool.some((p) => Math.abs(x - p) <= Math.max(relTol * Math.abs(p), 0.051))) bad.add(x);
    }
    return [...bad].sort((a, b) => a - b);
  }

  const normText = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9%.]/g, " ").split(/\s+/).filter(Boolean).join(" ");
  const quoteInSource = (q, src) => { const a = normText(q); return a.length > 0 && normText(src).includes(a); };

  const LEGAL = /\b(limited|ltd|plc|llc|inc|incorporated|corp|corporation|co|company|sa|sarl|sas|srl|spa|gmbh|ag|bv|nv|pvt|private|pte|pty|jsc|ojsc|cjsc|ooo|llp|lp|kg|ab|as|oy|sl|cv|de|of|the|and|group|holdings?)\b/g;
  function normSupplier(name) {
    let s = String(name || "").toLowerCase();
    s = s.replace(/\(\s*\d+\s*\)/g, " ").replace(/^\s*m\s*\/\s*s\.?\s*/, " ").replace(/&/g, " and ");
    s = s.replace(/(?<=\b[a-z])\.(?=[a-z]\b)/g, "").replace(/[^a-z0-9 ]/g, " ").replace(LEGAL, " ");
    return s.split(/\s+/).filter(Boolean).join(" ");
  }

  // ── retrieval: BM25 (same tokeniser and stemmer as procurement_demo/retrieval.py) ─────────────
  const STOP = new Set(("a an and are as at be by for from has have in is it its of on or that the this to was were will with " +
    "which their there been not but they into than these those also such other more most only over under per any all each " +
    "project projects bank world").split(" "));
  function stem(w) {
    for (const [suf, rep] of [["ies", "y"], ["ing", ""], ["ed", ""], ["es", ""], ["s", ""]]) {
      if (w.length > suf.length + 3 && w.endsWith(suf)) return w.slice(0, -suf.length) + rep;
    }
    return w;
  }
  const tokens = (t) => (String(t || "").toLowerCase().match(/[a-z][a-z0-9\-]+/g) || []).filter((w) => !STOP.has(w)).map(stem);

  class BM25 {
    constructor(texts, k1 = 1.4, b = 0.75) {
      this.k1 = k1; this.b = b; this.inv = new Map(); this.len = [];
      const df = new Map();
      texts.forEach((t, i) => {
        const tf = new Map();
        for (const w of tokens(t)) tf.set(w, (tf.get(w) || 0) + 1);
        let n = 0;
        for (const [w, c] of tf) {
          n += c; df.set(w, (df.get(w) || 0) + 1);
          if (!this.inv.has(w)) this.inv.set(w, []);
          this.inv.get(w).push([i, c]);
        }
        this.len.push(n);
      });
      this.n = texts.length;
      this.avg = this.len.reduce((a, b) => a + b, 0) / Math.max(1, this.n);
      this.idf = new Map([...df].map(([w, c]) => [w, Math.log(1 + (this.n - c + 0.5) / (c + 0.5))]));
    }
    scores(q) {
      const s = new Float64Array(this.n);
      for (const w of new Set(tokens(q))) {
        const idf = this.idf.get(w);
        if (!idf) continue;
        for (const [i, c] of this.inv.get(w)) {
          s[i] += idf * c * (this.k1 + 1) / (c + this.k1 * (1 - this.b + this.b * this.len[i] / this.avg));
        }
      }
      return s;
    }
  }
  const topIdx = (s, k) => [...s.keys()].filter((i) => s[i] > 0).sort((a, b) => s[b] - s[a]).slice(0, k);
  function rrf(rankings, k = 60) {
    const score = new Map();
    for (const r of rankings) r.forEach((i, pos) => score.set(i, (score.get(i) || 0) + 1 / (k + pos + 1)));
    return [...score.keys()].sort((a, b) => score.get(b) - score.get(a));
  }

  // ── evidence registry and the faithfulness gate ───────────────────────────────────────────────
  class Evidence {
    constructor() { this.items = {}; this.n = {}; }
    add(kind, text, meta = {}) {
      this.n[kind] = (this.n[kind] || 0) + 1;
      const id = kind + this.n[kind];
      this.items[id] = { id, text, ...meta };
      return id;
    }
  }

  function verify(statements, ev) {
    const kept = [], log = [];
    for (const [section, text, ids] of statements) {
      const problems = [];
      const abstain = ids.length === 0 && /not enough|no verified|no matching/i.test(text);
      if (!ids.length && !abstain) problems.push("no citation");
      const missing = ids.filter((i) => !ev.items[i]);
      if (missing.length) problems.push("unknown citation " + missing.join(", "));
      const items = ids.filter((i) => ev.items[i]).map((i) => ev.items[i]);
      if (items.length) {
        const claims = text.replace(/\bpp?\.\s?\d+(?:\s?-\s?\d+)?|\bP\d{6,7}\b/g, " ");
        const bad = unsupportedNumbers(claims, items.map((it) => it.text));
        if (bad.length) problems.push("unsupported numbers " + bad.join(", "));
        for (const m of text.matchAll(/“([^”]+)”/g)) {
          if (!items.some((it) => quoteInSource(m[1], it.text))) problems.push("quote not in source");
        }
        const pages = new Set(items.filter((it) => it.page != null).map((it) => String(it.page)));
        for (const m of text.matchAll(/\bp\.\s?(\d+)/g)) if (pages.size && !pages.has(m[1])) problems.push(`page ${m[1]} not in evidence`);
      }
      if (problems.length) log.push({ section, text, problems });
      else kept.push({ section, text, cites: ids, abstention: abstain });
    }
    return { kept, log };
  }

  // ── the engine ────────────────────────────────────────────────────────────────────────────────
  function makeEngine(D) {
    const C = D.contracts;                               // lab and diagnostic contracts only
    const name = (iso3) => D.countryName[iso3] || iso3;
    const projects = new Map(D.projects.map((p) => [p.id, p]));
    const ratings = new Map(D.ratings.map((r) => [r.project_id, r]));
    const itemRes = D.itemTypes.map(([n, p, dom]) => [n, new RegExp(p, "i"), dom]);
    const outRes = Object.fromEntries(Object.entries(D.outOfDomainWords).map(([k, v]) => [k, new RegExp(v, "i")]));
    const lessonIx = new BM25(D.lessons.map((l) => l.quote + " " + l.detail));
    const passageIx = new BM25(D.passages.map((p) => `${p.doc_type} ${p.title} ${p.section} ${p.text}`));
    const supplierKeys = D.supplierKeys;                  // [normalised name, supplier key], longest first

    const INTENTS = [
      ["graph", /relationship|related|connect|linked|link between|network|graph|entity[- ]relationship|\berd\b|diagram|knowledge graph|map of/i],
      ["rank_low", /cheapest|lowest|smallest|least expensive|minimum|lowest[- ]value/i],
      ["rank_high", /largest|biggest|highest|most expensive|maximum|top \d+ contracts|largest contracts/i],
      ["suppliers", /which (vendor|supplier|compan|firm|manufacturer)|who (supplied|won|got|received|sold|delivered)|top (vendor|supplier)s?|\bvendors\b|\bsuppliers\b|market share|concentrat/i],
      ["timeline", /how long|how many days|timeline|duration|time to award|cycle time|lead time|days to award|how fast|how quickly/i],
      ["aggregate", /how many|how much|total|spend|spent|count|number of|breakdown|by country|by year|by region|trend|overview|summary of/i],
      ["lessons", /why|lesson|learn|problem|challenge|issue|risk|fail|success|recommend|went wrong|cause|delay|maintenance|after-sales|warranty|what should/i],
    ];
    const REGION_WORDS = { "africa": "Sub-Saharan Africa", "latin america": "Latin America & Caribbean", "south asia": "South Asia",
      "east asia": "East Asia & Pacific", "middle east": D.regions.find((r) => /middle east/i.test(r)) || "Middle East & North Africa",
      "europe": "Europe & Central Asia", "central asia": "Europe & Central Asia" };

    // understanding
    function parse(q) {
      const f = { countries: [], region: null, items: null, domainOnly: true, years: null, project: null, supplier: null };
      let ql = " " + q.toLowerCase() + " ";
      for (const [alias, iso3] of D.countryAlias) {
        const re = new RegExp("\\b" + alias.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\b");
        if (re.test(ql) && !f.countries.includes(iso3)) { f.countries.push(iso3); ql = ql.replace(re, " "); }
      }
      if (!f.countries.length) {
        for (const r of D.regions) {
          const core = r.toLowerCase().replace("&", "and").split(" (")[0];
          if (q.toLowerCase().includes(core) || Object.entries(REGION_WORDS).some(([w, rr]) => q.toLowerCase().includes(w) && rr === r)) f.region = r;
        }
      }
      const specific = itemRes.filter(([n, re, dom]) => dom ? (!n.startsWith("General") && re.test(q)) : (outRes[n] && outRes[n].test(q))).map(([n]) => n);
      if (specific.length) { f.items = specific; f.domainOnly = specific.every((n) => D.domainItems.includes(n)); }
      let m = q.match(/\b(?:since|after|from) (20\d\d)\b/);
      if (m) f.years = [+m[1], 2100];
      m = q.match(/\b(20\d\d)\s*(?:-|to|and)\s*(20\d\d)\b/);
      if (m) f.years = [+m[1], +m[2]];
      else if (!f.years && (m = q.match(/\bin (20\d\d)\b/))) f.years = [+m[1], +m[1]];
      m = q.toUpperCase().match(/\bP\d{6}\b/);
      if (m) f.project = m[0];
      const norm = " " + normSupplier(q) + " ";
      const hit = supplierKeys.find(([k]) => norm.includes(" " + k + " "));
      if (hit) f.supplier = hit[1];
      return f;
    }

    function intent(q, f) {
      for (const [n, re] of INTENTS) {
        if (re.test(q)) {
          if ((n === "suppliers" || n === "aggregate") && f.supplier && !/which|top|share/i.test(q)) return "supplier";
          return n;
        }
      }
      if (f.project) return "project";
      if (f.supplier) return "supplier";
      return "lessons";
    }

    function scopeText(f) {
      const bits = [];
      if (f.items) bits.push(f.items.join(" / "));
      else bits.push("lab and diagnostic equipment");
      if (f.countries.length) bits.push("in " + f.countries.map(name).join(", "));
      else if (f.region) bits.push("in " + f.region);
      if (f.years) bits.push(f.years[0] === f.years[1] ? `signed ${f.years[0]}` : f.years[1] < 2100 ? `signed ${f.years[0]}-${f.years[1]}` : `signed ${f.years[0]} onwards`);
      return bits.join(" ");
    }

    function filtered(f) {
      return C.filter((c) => (!f.items || f.items.includes(c.t)) && (!f.countries.length || f.countries.includes(c.c))
        && (f.countries.length || !f.region || c.r === f.region)
        && (!f.years || (+c.d.slice(0, 4) >= f.years[0] && +c.d.slice(0, 4) <= f.years[1]))
        && (!f.project || c.p === f.project) && (!f.supplier || c.sk === f.supplier) && c.a > 0);
    }

    function contractEvidence(ev, c) {
      return ev.add("C", `Contract ${c.n}: ${c.x} | supplier ${c.s} (${c.sc}) | ${money(c.a)} | signed ${c.d} | ${c.p} ` +
        `${(projects.get(c.p) || {}).name || ""} | ${name(c.c)} | ${c.m}, ${c.v} review | equipment type: ${c.t}`,
      { source: "World Bank Major Contract Awards", project: c.p, contract: c.n, url: (projects.get(c.p) || {}).url });
    }

    function groupBy(rows, key) {
      const g = new Map();
      for (const r of rows) { const k = key(r); if (!g.has(k)) g.set(k, []); g.get(k).push(r); }
      return g;
    }
    const sum = (rows) => rows.reduce((a, r) => a + r.a, 0);
    const median = (xs) => { const s = [...xs].sort((a, b) => a - b); const n = s.length; return n ? (n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2) : 0; };

    // routes: each fills st (statements [section, text, ids]) and out (table / graph)
    const R = {};
    R.rank = (f, ev, st, out, low) => {
      const c = filtered(f);
      if (!c.length) { st.push(["Answer", `Not enough data: no contracts match ${scopeText(f)}.`, []]); return; }
      const top = [...c].sort((a, b) => low ? a.a - b.a : b.a - a.a).slice(0, 5);
      const ids = top.map((x) => contractEvidence(ev, x));
      const r0 = top[0];
      st.push(["Answer", `The ${low ? "lowest" : "highest"}-value contract for ${scopeText(f)} went to ${r0.s} (${r0.sc}): ` +
        `contract ${r0.n}, ${money(r0.a)}, for “${r0.x.slice(0, 120).replace(/[. ]+$/, "")}”.`, [ids[0]]]);
      top.slice(1).forEach((x, i) => st.push(["Next in the ranking", `${x.s}: ${money(x.a)} (contract ${x.n}, ${name(x.c)}).`, [ids[i + 1]]]));
      const did = ev.add("D", `Derived from the contract table: ${c.length} contracts match (${scopeText(f)}); median value ${money(median(c.map((x) => x.a)))}.`, { source: "calculation" });
      st.push(["Context", `${c.length} contracts match this question; the median value is ${money(median(c.map((x) => x.a)))}. ` +
        "Awards are reported per contract, so a low value can be one lot of a larger tender.", [did]]);
      out.table = { cols: ["Contract", "Supplier", "Value", "Signed", "Country", "Equipment type"],
        rows: top.map((x) => [x.n, x.s, money(x.a), x.d, name(x.c), x.t]) };
    };
    R.rank_low = (q, f, ev, st, out) => R.rank(f, ev, st, out, true);
    R.rank_high = (q, f, ev, st, out) => R.rank(f, ev, st, out, false);

    function supplierTable(c) {
      const total = sum(c);
      const g = [...groupBy(c, (x) => x.sk)].map(([k, rows]) => {
        const names = groupBy(rows, (x) => x.s);
        return { key: k, name: [...names].sort((a, b) => b[1].length - a[1].length)[0][0], base: rows[0].sc,
          n: new Set(rows.map((x) => x.n)).size, usd: sum(rows), countries: new Set(rows.map((x) => x.c)).size };
      }).sort((a, b) => b.usd - a.usd);
      return { total, g };
    }

    R.suppliers = (q, f, ev, st, out) => {
      const c = filtered(f);
      if (!c.length) { st.push(["Answer", `Not enough data: no contracts match ${scopeText(f)}.`, []]); return; }
      const { total, g } = supplierTable(c);
      const rows = g.slice(0, 10).map((s) => `${s.name} (${s.base}): ${plural(s.n, "contract")}, ${money(s.usd)}, ${pct(s.usd / total)} of value, ${plural(s.countries, "country", "countries")}`).join("; ");
      const did = ev.add("D", `Derived from the contract table grouped by resolved supplier for ${scopeText(f)}: ${c.length} contracts, ` +
        `${g.length} suppliers, total ${money(total)}. Top suppliers: ${rows}.`, { source: "calculation" });
      st.push(["Answer", `${g.length} suppliers won ${c.length} contracts worth ${money(total)} for ${scopeText(f)}.`, [did]]);
      for (const s of g.slice(0, 5)) {
        st.push(["Top suppliers by value", `${s.name} (${s.base}): ${plural(s.n, "contract")}, ${money(s.usd)} (${pct(s.usd / total)} of value), ${plural(s.countries, "country", "countries")}.`, [did]]);
      }
      const top5 = g.slice(0, 5).reduce((a, s) => a + s.usd, 0) / total;
      const did2 = ev.add("D", `Derived: the top 5 suppliers hold ${pct(top5)} of contract value.`, { source: "calculation" });
      st.push(["Market concentration", `The top 5 suppliers hold ${pct(top5)} of the value, ${top5 > 0.5 ? "a concentrated market" : "a fairly fragmented market"}.`, [did2]]);
      out.table = { cols: ["Supplier", "Based in", "Contracts", "Value", "Share", "Countries"],
        rows: g.slice(0, 15).map((s) => [s.name, s.base, s.n, money(s.usd), pct(s.usd / total), s.countries]) };
    };

    R.aggregate = (q, f, ev, st, out) => {
      const c = filtered(f);
      if (!c.length) { st.push(["Answer", `Not enough data: no contracts match ${scopeText(f)}.`, []]); return; }
      const by = /by country|which countr|per country/i.test(q) ? "country" : /by region|per region/i.test(q) ? "region"
        : /by year|per year|trend|over time/i.test(q) ? "year" : "equipment type";
      const key = { country: (x) => name(x.c), region: (x) => x.r || "Unknown", year: (x) => x.d.slice(0, 4), "equipment type": (x) => x.t }[by];
      let g = [...groupBy(c, key)].map(([k, rows]) => ({ k, n: new Set(rows.map((x) => x.n)).size, usd: sum(rows), sup: new Set(rows.map((x) => x.sk)).size }));
      g.sort(by === "year" ? (a, b) => a.k.localeCompare(b.k) : (a, b) => b.usd - a.usd);
      const total = sum(c);
      const nSup = new Set(c.map((x) => x.sk).filter(Boolean)).size, nProj = new Set(c.map((x) => x.p)).size, nC = new Set(c.map((x) => x.c)).size;
      const did = ev.add("D", `Derived from the contract table for ${scopeText(f)}, grouped by ${by}: total ${c.length} contracts, ` +
        `${money(total)}, ${nSup} suppliers, ${nProj} projects, ${nC} countries. Groups: ` + g.map((x) => `${x.k}: ${x.n} contracts, ${money(x.usd)}`).join("; ") + ".",
      { source: "calculation" });
      st.push(["Answer", `${c.length} contracts worth ${money(total)} match ${scopeText(f)}, awarded to ${nSup} suppliers in ${nProj} projects and ${nC} countries.`, [did]]);
      for (const x of (by === "year" ? g : g.slice(0, 6))) st.push([`By ${by}`, `${x.k}: ${plural(x.n, "contract")}, ${money(x.usd)}.`, [did]]);
      out.table = { cols: [by[0].toUpperCase() + by.slice(1), "Contracts", "Value", "Suppliers"], rows: g.map((x) => [x.k, x.n, money(x.usd), x.sup]) };
      out.chart = g.slice(0, 12).map((x) => [x.k, x.usd]);
    };

    R.timeline = (q, f, ev, st, out) => {
      const types = f.items || D.domainItems;
      let rows = D.benchmarks.filter((b) => b.level === "item_type" && types.includes(b.item_type));
      if (!rows.length) rows = D.benchmarks.filter((b) => b.level === "category");
      rows.sort((a, b) => a.median - b.median);
      let last = null;
      for (const b of rows) {
        const label = b.item_type || b.category;
        const p80 = b.p80 != null ? `, P80 ${Math.round(b.p80)} days (80% of tenders awarded within it)` : "";
        last = ev.add("B", `Kaplan-Meier benchmark for ${label}: median (50%) ${Math.round(b.median)} days from invitation to award${p80}; ` +
          `n=${b.n} tenders, ${b.n_awarded} awarded; median if open tenders are ignored ${Math.round(b.naive)} days.`, { source: "tender benchmarks (procurement notices)" });
        st.push(["Time from invitation to award", `${label}: half of tenders are awarded within ${Math.round(b.median)} days` +
          (b.p80 != null ? `; 80% within ${Math.round(b.p80)} days` : "") + ` (n=${b.n}).`, [last]]);
      }
      if (last) st.push(["How to read this", "These medians count tenders that are still open as at least their current age, so slow tenders are not dropped. Plan milestones on the P80, not the median.", [last]]);
      out.table = { cols: ["Equipment type", "Tenders", "Median days", "P80 days"], rows: rows.map((b) => [b.item_type || b.category, b.n, Math.round(b.median), b.p80 == null ? "-" : Math.round(b.p80)]) };
    };

    function ratingNote(r) {
      const sat = /^(highly satisfactory|satisfactory|moderately satisfactory)$/i;
      if (sat.test(r.resolved) && r.worst && !sat.test(r.worst)) return `this project's supervision ratings fell to ${r.worst} before the final ${r.from} rating of ${r.resolved}, so the lesson reflects a recovery`;
      if (!sat.test(r.resolved)) return `this project's ratings were mixed across sources, and the final ${r.from} rating is ${r.resolved}, so the lesson comes from a project that underperformed`;
      return `sources disagree on this project's rating; the final ${r.from} rating is ${r.resolved}`;
    }

    function bestSentence(text, q) {
      const qt = new Set(tokens(q));
      let best = null, bs = 0;
      for (const s of text.split(/(?<=[.!?])\s+/)) {
        const n = s.split(/\s+/).length;
        if (n < 12 || n > 60) continue;
        const sc = new Set(tokens(s)).size && [...new Set(tokens(s))].filter((w) => qt.has(w)).length;
        if (sc > bs) { best = s; bs = sc; }
      }
      return bs >= 2 ? best : null;
    }

    function searchReports(query, country, k = 5) {
      const domainHint = /\blab|laborator|diagnost|equipment/i.test(query) ? " laboratory diagnostic equipment" : "";
      const queries = [query, query + domainHint + " procurement", query + " lessons implementation"];
      const boost = (rows, i) => (country && rows[i].iso3 === country ? 1.3 : 1);
      const lr = rrf(queries.map((qq) => { const s = lessonIx.scores(qq); s.forEach((v, i) => { s[i] = v * boost(D.lessons, i); }); return topIdx(s, 40); }));
      const pr = rrf(queries.slice(0, 2).map((qq) => { const s = passageIx.scores(qq); s.forEach((v, i) => { s[i] = v * boost(D.passages, i); }); return topIdx(s, 40); }));
      const per = new Map();
      const lessons = [];
      for (const i of lr) { const l = D.lessons[i]; if ((per.get(l.doc) || 0) >= 2) continue; per.set(l.doc, (per.get(l.doc) || 0) + 1); lessons.push(l); if (lessons.length >= k) break; }
      const passages = [];
      const per2 = new Map();
      for (const i of pr) { const p = D.passages[i]; if ((per2.get(p.doc) || 0) >= 2) continue; per2.set(p.doc, (per2.get(p.doc) || 0) + 1); passages.push(p); if (passages.length >= 4) break; }
      return { lessons, passages, hint: domainHint };
    }

    R.lessons = (q, f, ev, st, out) => {
      const country = f.countries.length === 1 ? f.countries[0] : null;
      const { lessons, passages, hint } = searchReports(q, country);
      const used = new Set();
      for (const l of lessons) {
        const lid = ev.add("L", l.detail, { source: `${l.doc_type} ${l.title}`, page: l.page, url: l.url, project: l.project });
        let line = `“${l.quote}” (${l.doc_type}, ${l.project}, p. ${l.page})`;
        const cites = [lid];
        const r = ratings.get(l.project);
        if (r && r.conflict) {
          cites.push(ev.add("R", `Rating evidence for ${l.project}: ${r.trajectory}. Resolved: ${r.resolved} (from ${r.from}).`, { source: "resolved ratings", project: l.project }));
          line += ` - note: ${ratingNote(r)}.`;
        }
        used.add(l.quote.slice(0, 80));
        st.push(["Lessons from evaluations", line, cites]);
      }
      for (const p of passages) {
        const s = bestSentence(p.text, q + hint);
        if (!s || [...used].some((u) => s.includes(u) || u.includes(s.slice(0, 80)))) continue;
        const pid = ev.add("P", p.text, { source: `${p.doc_type} ${p.title}`, page: p.page, url: p.url, project: p.project, section: p.section });
        st.push(["Report passages", `“${s}” (${p.doc_type}, ${p.project}, p. ${p.page})`, [pid]]);
      }
      if (!st.length) st.push(["Answer", "No verified lessons or passages matched this question in the indexed reports.", []]);
    };

    R.project = (q, f, ev, st, out) => {
      const p = projects.get(f.project);
      if (!p) { st.push(["Answer", `Not enough data: project ${f.project} is not in this knowledge base.`, []]); return; }
      const jid = ev.add("J", `Project ${p.id} ${p.name} | ${name(p.iso3)} | ${p.region} | approved ${p.approved} | status ${p.status}` +
        (p.commitment ? ` | commitment ${money(p.commitment)}` : ""), { source: "World Bank Projects API", url: p.url, project: p.id });
      st.push(["Project", p.name ? `${p.id} ${p.name} (${name(p.iso3)}), approved ${p.approved}, status ${p.status}.`
        : `${p.id} (${name(p.iso3)}): the Projects API does not publish this project's name or dates; its contracts and reports are below.`, [jid]]);
      const c = C.filter((x) => x.p === p.id);
      if (c.length) {
        const items = [...groupBy(c, (x) => x.t)].map(([k, rows]) => [k, sum(rows)]).sort((a, b) => b[1] - a[1]);
        const did = ev.add("D", `Derived from the contract table for ${p.id}: ${c.length} lab and diagnostic contracts, ${money(sum(c))}; by equipment type: ` +
          items.map(([k, v]) => `${k}: ${money(v)}`).join("; "), { source: "calculation", project: p.id });
        st.push(["Procurement", `${plural(c.length, "lab and diagnostic contract")} worth ${money(sum(c))}; the largest equipment type is ${items[0][0]} (${money(items[0][1])}).`, [did]]);
        for (const x of [...c].sort((a, b) => b.a - a.a).slice(0, 3)) st.push(["Largest contracts", `${x.s}: ${money(x.a)} for “${x.x.slice(0, 90).replace(/[. ]+$/, "")}”.`, [contractEvidence(ev, x)]]);
      }
      const r = ratings.get(p.id);
      if (r) {
        const rid = ev.add("R", `Rating evidence for ${p.id}: ${r.trajectory}. Resolved: ${r.resolved} (from ${r.from}).`, { source: "resolved ratings", project: p.id });
        st.push(["Outcome", `Development-objective rating: ${r.resolved} (from the ${r.from})` + (r.conflict ? `; supervision ratings disagreed, reaching ${r.worst} during implementation` : "") + ".", [rid]]);
      }
      for (const d of D.docs.filter((x) => x.project === p.id)) {
        st.push(["Documents indexed", `${d.type}: ${d.title.slice(0, 90)} (${d.date}).`, [ev.add("S", `${d.type} ${d.title} ${d.date}`, { source: "Documents & Reports", url: d.url, project: p.id })]]);
      }
      out.graph = graphFor("PROJECT", p.id);
    };

    R.supplier = (q, f, ev, st, out) => {
      const c = C.filter((x) => x.sk === f.supplier);
      const names = [...new Set(c.map((x) => x.s))];
      const total = sum(c);
      const sid = ev.add("V", `Supplier ${names[0]} (${c[0].sc}) | ${names.length} name variants: ${names.join("; ")} | ${c.length} contracts | ${money(total)}`, { source: "resolved suppliers" });
      st.push(["Supplier", `${names[0]} (${c[0].sc}) won ${plural(c.length, "lab and diagnostic contract")} worth ${money(total)}` + (names.length > 1 ? `, recorded under ${names.length} name spellings` : "") + ".", [sid]]);
      const items = [...groupBy(c, (x) => x.t)].map(([k, r]) => [k, sum(r)]).sort((a, b) => b[1] - a[1]);
      const ctry = [...groupBy(c, (x) => x.c)].map(([k, r]) => [k, sum(r)]).sort((a, b) => b[1] - a[1]);
      const nProj = new Set(c.map((x) => x.p)).size;
      const did = ev.add("D", "Derived from the contract table: by equipment type " + items.map(([k, v]) => `${k}: ${money(v)}`).join("; ") +
        ". By country: " + ctry.map(([k, v]) => `${name(k)}: ${money(v)}`).join("; ") + `. Countries: ${ctry.length}. Projects: ${nProj}.`, { source: "calculation" });
      st.push(["What and where", `Mostly ${items[0][0]} (${money(items[0][1])}); active in ${plural(ctry.length, "country", "countries")} and ${plural(nProj, "project")}, largest in ${name(ctry[0][0])} (${money(ctry[0][1])}).`, [did]]);
      for (const x of [...c].sort((a, b) => b.a - a.a).slice(0, 3)) st.push(["Largest contracts", `${money(x.a)} in ${name(x.c)} for “${x.x.slice(0, 90).replace(/[. ]+$/, "")}”.`, [contractEvidence(ev, x)]]);
      out.table = { cols: ["Contract", "Value", "Signed", "Country", "Equipment type"], rows: [...c].sort((a, b) => b.a - a.a).slice(0, 15).map((x) => [x.n, money(x.a), x.d, name(x.c), x.t]) };
      out.graph = graphFor("SUPPLIER", f.supplier);
    };

    R.graph = (q, f, ev, st, out) => {
      if (f.supplier) return R.supplier(q, f, ev, st, out);
      if (f.project) return R.project(q, f, ev, st, out);
      if (f.countries.length === 1) {
        const iso3 = f.countries[0];
        const c = C.filter((x) => x.c === iso3);
        const nProj = new Set(c.map((x) => x.p)).size, nSup = new Set(c.map((x) => x.sk)).size;
        const gid = ev.add("G", `Knowledge-graph neighbourhood of ${name(iso3)}: ${nProj} projects, ${nSup} suppliers, ${c.length} lab and diagnostic contracts.`, { source: "knowledge graph" });
        st.push(["Connections", `${name(iso3)} links to ${plural(nProj, "project")}, which hold ${c.length} lab and diagnostic contracts from ${plural(nSup, "supplier")}.`, [gid]]);
        for (const [sk, rows] of [...groupBy(c, (x) => x.sk)].sort((a, b) => sum(b[1]) - sum(a[1])).slice(0, 3)) {
          const types = [...new Set(rows.map((x) => x.t))].slice(0, 3).join(", ");
          const sid = ev.add("D", `Derived from the contract table: supplier ${rows[0].s} in ${name(iso3)}: ${rows.length} contracts, ${money(sum(rows))}, equipment types ${types}.`, { source: "calculation" });
          st.push(["Main suppliers", `${rows[0].s}: ${plural(rows.length, "contract")}, ${money(sum(rows))} (${types}).`, [sid]]);
        }
        out.graph = graphFor("COUNTRY", iso3);
        return;
      }
      const gid = ev.add("G", "Edge counts in the knowledge graph: " + D.edgeCounts.map(([k, n]) => `${k}: ${n}`).join("; "), { source: "knowledge graph" });
      st.push(["Data model", "The diagram shows how the entities connect. Every link is stored with the method that created it (exact key, fuzzy name, keyword, temporal) and a confidence.", [gid]]);
      for (const [k, n] of D.edgeCounts.slice(0, 6)) st.push(["Largest relationships", `${k}: ${n.toLocaleString("en-US")} links.`, [gid]]);
      out.schema = true;
    };

    // knowledge-graph neighbourhood → nodes in columns by type
    function graphFor(kind, key, max = 8) {
      const nodes = new Map(), edges = [];
      const node = (id, type, label) => { if (!nodes.has(id)) nodes.set(id, { id, type, label }); };
      const projCtx = (pid) => {
        const p = projects.get(pid);
        if (!p) return;
        node("C:" + p.iso3, "COUNTRY", name(p.iso3));
        edges.push([pid, "C:" + p.iso3, "located in"]);
        if (p.region) { node("R:" + p.region, "REGION", p.region); edges.push(["C:" + p.iso3, "R:" + p.region, "in region"]); }
      };
      if (kind === "SUPPLIER") {
        const c = C.filter((x) => x.sk === key);
        node(key, "SUPPLIER", c[0].s);
        for (const [t, rows] of groupBy(c, (x) => x.t)) { node("I:" + t, "EQUIPMENT", t); edges.push([key, "I:" + t, `${rows.length}x, ${money(sum(rows))}`]); }
        for (const [pid, rows] of [...groupBy(c, (x) => x.p)].sort((a, b) => sum(b[1]) - sum(a[1])).slice(0, max)) {
          node(pid, "PROJECT", `${pid} ${((projects.get(pid) || {}).name || "").slice(0, 34)}`);
          edges.push([pid, key, `awarded ${money(sum(rows))}`]);
          projCtx(pid);
        }
      } else if (kind === "PROJECT") {
        const p = projects.get(key);
        node(key, "PROJECT", `${key} ${(p.name || "").slice(0, 34)}`);
        projCtx(key);
        const c = C.filter((x) => x.p === key);
        for (const [sk, rows] of [...groupBy(c, (x) => x.sk)].sort((a, b) => sum(b[1]) - sum(a[1])).slice(0, max)) {
          node(sk, "SUPPLIER", rows[0].s.slice(0, 34));
          edges.push([key, sk, `awarded ${money(sum(rows))}`]);
          for (const t of [...new Set(rows.map((x) => x.t))].slice(0, 2)) { node("I:" + t, "EQUIPMENT", t); edges.push([sk, "I:" + t, "supplied"]); }
        }
        for (const d of D.docs.filter((x) => x.project === key)) { node("D:" + d.id, "REPORT", `${d.type} ${d.date}`); edges.push(["D:" + d.id, key, "describes"]); }
      } else if (kind === "COUNTRY") {
        node(key, "COUNTRY", name(key));
        const c = C.filter((x) => x.c === key);
        for (const [pid, rows] of [...groupBy(c, (x) => x.p)].sort((a, b) => sum(b[1]) - sum(a[1])).slice(0, max)) {
          node(pid, "PROJECT", `${pid} ${((projects.get(pid) || {}).name || "").slice(0, 34)}`);
          edges.push([pid, key, "located in"]);
          for (const [sk, srows] of [...groupBy(rows, (x) => x.sk)].sort((a, b) => sum(b[1]) - sum(a[1])).slice(0, 2)) {
            node(sk, "SUPPLIER", srows[0].s.slice(0, 34));
            edges.push([pid, sk, `awarded ${money(sum(srows))}`]);
          }
        }
        const reg = (D.projects.find((p) => p.iso3 === key) || {}).region;
        if (reg) { node("R:" + reg, "REGION", reg); edges.push([key, "R:" + reg, "in region"]); }
      }
      return { nodes: [...nodes.values()], edges };
    }

    function ask(question) {
      const f = parse(question);
      const route = intent(question, f);
      const ev = new Evidence(), st = [], out = {};
      R[route](question, f, ev, st, out);
      const { kept, log } = verify(st, ev);
      return { question, route, scope: scopeText(f), filters: f, statements: kept, dropped: log, evidence: ev.items, ...out };
    }

    // ── tools for the AI mode: explicit arguments instead of free text, same data and evidence ids ────
    function filtersFromArgs(a) {
      const items = Array.isArray(a.equipment_types) && a.equipment_types.length ? a.equipment_types.map(String).filter((t) => D.itemTypes.some(([n]) => n === t)) : null;
      const countries = (Array.isArray(a.countries) ? a.countries : []).map((x) => {
        const s = String(x).toLowerCase();
        return D.countryName[String(x).toUpperCase()] ? String(x).toUpperCase() : (D.countryAlias.find(([al]) => al === s) || [])[1];
      }).filter(Boolean);
      const region = a.region ? D.regions.find((r) => r.toLowerCase().startsWith(String(a.region).toLowerCase().slice(0, 8))) || null : null;
      const supplier = a.supplier ? (supplierKeys.find(([k]) => (" " + normSupplier(a.supplier) + " ").includes(" " + k + " ") || k.includes(normSupplier(a.supplier))) || [])[1] || "__none__" : null;
      return { countries, region, items: items && items.length ? items : null, domainOnly: true,
        years: a.year_from || a.year_to ? [Number(a.year_from) || 2000, Number(a.year_to) || 2100] : null,
        project: a.project_id ? String(a.project_id).toUpperCase() : null, supplier };
    }
    const asToolResult = (res) => ({
      scope: res.scope,
      findings: res.statements.map((s) => ({ text: s.text, cite: s.cites })),
      evidence: Object.values(res.evidence).map((e) => ({ id: e.id, text: e.text.slice(0, 600), source: e.source, page: e.page })),
    });
    function runTool(route, args, ev) {
      const f = filtersFromArgs(args || {});
      const local = new Evidence(), st = [], out = {};
      const q = String(args.query || args.question || "");
      if (route === "aggregate") R.aggregate(args.by ? `by ${args.by}` : "", f, local, st, out);
      else if (route === "lessons") R.lessons(q, f, local, st, out);
      else R[route](q, f, local, st, out);
      const { kept, log } = verify(st, local);
      // re-number into the conversation-wide registry so ids stay unique across tool calls
      const map = {};
      for (const [id, it] of Object.entries(local.items)) {
        const { id: _old, text, ...meta } = it;
        map[id] = ev.add(id.replace(/\d+$/, ""), text, meta);
      }
      const res = { scope: scopeText(f), statements: kept.map((s) => ({ ...s, text: s.text, cites: s.cites.map((c) => map[c]) })),
        evidence: Object.fromEntries(Object.entries(local.items).map(([id]) => [map[id], ev.items[map[id]]])), table: out.table, graph: out.graph, schema: out.schema, dropped: log };
      return res;
    }

    const FILTER_PROPS = {
      equipment_types: { type: "array", items: { type: "string", enum: D.itemTypes.map(([n]) => n) }, description: "Limit to these equipment types (omit for all lab and diagnostic equipment)." },
      countries: { type: "array", items: { type: "string" }, description: "Country names or ISO-3 codes." },
      region: { type: "string", enum: D.regions },
      year_from: { type: "integer" }, year_to: { type: "integer" },
      supplier: { type: "string", description: "Supplier name." },
      project_id: { type: "string", description: "World Bank project id, e.g. P173836." },
    };
    const toolDefs = [
      ["rank_contracts", "Lowest- or highest-value lab and diagnostic contracts for the filters, with supplier, value, date, country and evidence ids. Use for cheapest / largest / which vendor won questions.",
        { ...FILTER_PROPS, order: { type: "string", enum: ["lowest", "highest"] } }, (a, ev) => runTool(a.order === "lowest" ? "rank_low" : "rank_high", a, ev)],
      ["supplier_market", "Suppliers ranked by contract value for the filters: contracts, value, share, countries, market concentration.", FILTER_PROPS, (a, ev) => runTool("suppliers", a, ev)],
      ["spend_breakdown", "Total contracts and value for the filters, broken down by equipment type, country, region or year.",
        { ...FILTER_PROPS, by: { type: "string", enum: ["equipment type", "country", "region", "year"] } }, (a, ev) => runTool("aggregate", a, ev)],
      ["tender_times", "Censoring-aware days from invitation to award (median and P80) by equipment type.", { equipment_types: FILTER_PROPS.equipment_types }, (a, ev) => runTool("timeline", a, ev)],
      ["search_reports", "Search World Bank project reports (completion reports, IEG reviews, appraisal and supervision reports) for lessons and passages, with verbatim quotes, project and page.",
        { query: { type: "string", description: "What to look for, in plain words." }, countries: FILTER_PROPS.countries }, (a, ev) => runTool("lessons", a, ev)],
      ["project_profile", "One project: country, status, lab and diagnostic contracts, largest suppliers, outcome rating (with disagreements between sources) and indexed reports.",
        { project_id: FILTER_PROPS.project_id }, (a, ev) => runTool("project", a, ev)],
      ["supplier_profile", "One supplier: what it supplied, where, in which projects, largest contracts.", { supplier: FILTER_PROPS.supplier }, (a, ev) => {
        const f = filtersFromArgs(a);
        if (!f.supplier || f.supplier === "__none__") throw new Error("No supplier with that name in the lab and diagnostic contracts.");
        return runTool("supplier", a, ev);
      }],
    ];

    return { ask, parse, intent, verify, unsupportedNumbers, numbersIn, Evidence, toolDefs, runTool, asToolResult, graphFor, money, D };
  }

  root.ProcEngine = { makeEngine, unsupportedNumbers, numbersIn, verify, Evidence, normSupplier, tokens };
})(typeof window !== "undefined" ? window : globalThis);
