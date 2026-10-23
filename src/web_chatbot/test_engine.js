// Node check of the browser engine against the Python chatbot's answers.
//   python src/build_web_chatbot.py && python -m procurement_demo.chatbot --eval > py_eval.json (optional)
//   node src/web_chatbot/test_engine.js deliverables/web_chatbot/data.json expected.json
const fs = require("fs");
const path = require("path");
require(path.join(__dirname, "engine.js"));
const D = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const expected = process.argv[3] ? JSON.parse(fs.readFileSync(process.argv[3], "utf8")) : [];
const E = globalThis.ProcEngine.makeEngine(D);

let fails = 0;
for (const x of expected) {
  const t0 = Date.now();
  const a = E.ask(x.question);
  const first = a.statements[0] ? a.statements[0].text : "";
  // same route; for exact routes the first sentence must state the same figures as the Python chatbot
  const sameNums = !x.first || JSON.stringify(E.numbersIn(first)) === JSON.stringify(E.numbersIn(x.first));
  const ok = a.route === x.route && sameNums && a.dropped.length === 0 && a.statements.length > 0;
  if (!ok) fails++;
  console.log(`${ok ? "PASS" : "FAIL"} ${a.route.padEnd(9)} ${String(a.statements.length).padStart(2)} st ${a.dropped.length} dropped ${Date.now() - t0}ms  ${x.question}`);
  if (!ok) {
    console.log("   expected route:", x.route, "\n   py first:", x.first, "\n   js first:", first);
    for (const d of a.dropped) console.log("   dropped:", d.text.slice(0, 120), d.problems);
  }
}
// every Claude tool runs and returns cited, verified findings
const ev = new E.Evidence();
const calls = [
  ["rank_contracts", { equipment_types: ["Molecular diagnostics (PCR, GeneXpert)"], order: "lowest" }],
  ["rank_contracts", { countries: ["Kenya"], order: "highest" }],
  ["supplier_market", { region: "Sub-Saharan Africa", equipment_types: ["Diagnostic imaging"] }],
  ["spend_breakdown", { by: "country" }],
  ["spend_breakdown", { by: "year", equipment_types: ["Rapid tests and test kits"] }],
  ["tender_times", { equipment_types: ["Laboratory analyzers"] }],
  ["search_reports", { query: "laboratory equipment maintenance problems" }],
  ["project_profile", { project_id: D.projects[0].id }],
  ["supplier_profile", { supplier: "UNICEF" }],
];
for (const [name, args] of calls) {
  const def = E.toolDefs.find((t) => t[0] === name);
  try {
    const res = def[3](args, ev);
    const out = E.asToolResult(res);
    const size = JSON.stringify(out).length;
    const ok = out.findings.length > 0 && res.dropped.length === 0 && out.findings.every((f) => f.cite.every((c) => ev.items[c]));
    if (!ok) fails++;
    console.log(`${ok ? "PASS" : "FAIL"} tool ${name.padEnd(16)} ${out.findings.length} findings, ${out.evidence.length} evidence, ${size} bytes  ${out.findings[0] ? out.findings[0].text.slice(0, 90) : ""}`);
  } catch (e) { fails++; console.log(`FAIL tool ${name}: ${e.message}`); }
}
// the answer checker catches an invented number and accepts a supported one
const sup = E.unsupportedNumbers("Contract worth US$987,654,321.77 [C1]", Object.values(ev.items).map((e) => e.text));
const fine = E.unsupportedNumbers("half are awarded within 90 days", ["median (50%) 90 days"]);
console.log(sup.length && !fine.length ? "PASS checker" : "FAIL checker", sup, fine);
if (!(sup.length && !fine.length)) fails++;
console.log(fails ? `${fails} FAILED` : "ALL PASSED");
process.exit(fails ? 1 : 0);
