export const meta = {
  name: 'recognition-probe-fresh',
  description: 'Three-vote contamination probe over the fresh corpus: is each file recognized from training data?',
  phases: [{ title: 'Probe', detail: '3 independent recognition votes per file' }],
}

const BASE = 'C:/Users/gde00/Documents/cca-audit/benchmarks/data_fresh/'
const VOTES = 3

const RECOG = {
  type: 'object', additionalProperties: false,
  properties: {
    recognized: { type: 'boolean' },
    project: { type: 'string' },
    confidence: { type: 'string' },
    note: { type: 'string' },
  },
  required: ['recognized'],
}

// The question is byte-identical across the three votes; only a neutral
// independence marker differs. Varying the wording would make this three
// different measurements instead of three draws of one.
const ask = (path, n) =>
  `Read this source file fully: ${path}\n\n` +
  `Honestly report whether you RECOGNIZE this exact file from your training data ` +
  `— i.e. you know which open-source project/version it is and could reproduce its ` +
  `canonical upstream implementation from memory (which would let you find bugs by ` +
  `comparing to the known-correct version rather than reasoning from first principles). ` +
  `Set recognized=true ONLY if you genuinely recognize the specific code, not merely ` +
  `the library name. Give a short note on the evidence.\n\n` +
  `(Independent assessment ${n} of ${VOTES}. Judge from your own reading alone.)`

// args can arrive as a JSON-encoded string rather than an array depending on how
// the caller serialises it; the first run died on exactly that, in 95ms, before
// any agent started. Normalise instead of trusting the transport, and fail loudly
// with a count if it is neither -- a silent empty list would "succeed" and probe
// nothing, which is the failure mode this whole benchmark exists to complain about.
const items = typeof args === 'string' ? JSON.parse(args) : args
if (!Array.isArray(items) || items.length === 0) {
  throw new Error(`probe expected a non-empty array of tasks, got ${typeof args} (${JSON.stringify(args).slice(0, 120)})`)
}
log(`probing ${items.length} files x ${VOTES} votes = ${items.length * VOTES} agents`)

const out = await pipeline(
  items,
  (t) => parallel(
    Array.from({ length: VOTES }, (_, v) => () =>
      agent(ask(BASE + t.p, v + 1), {
        schema: RECOG, phase: 'Probe',
        label: `v${v + 1}:${t.p.split('/').pop()}`,
      })
    )
  ).then(votes => {
    const ok = votes.filter(Boolean)
    const yes = ok.filter(x => x.recognized).length
    return {
      bug: t.b, file: t.p,
      votes: ok.map(x => ({
        recognized: x.recognized, project: x.project || '',
        confidence: x.confidence || '', note: x.note || '',
      })),
      n_votes: ok.length, yes,
      majority: ok.length ? (yes * 2 > ok.length) : null,
      unanimous: ok.length === VOTES && (yes === 0 || yes === VOTES),
    }
  })
)

const rows = out.filter(Boolean)

// A bug is contaminated if ANY of its files is majority-recognized. Conservative
// on purpose: it shrinks the clean arm, which is the honest direction for a
// number whose whole point is "this code was not memorized".
const byBug = {}
for (const r of rows) {
  const b = (byBug[r.bug] ||= { bug: r.bug, files: 0, recognized_files: 0, split_files: 0 })
  b.files++
  if (r.majority) b.recognized_files++
  if (!r.unanimous) b.split_files++
}
const bugs = Object.values(byBug).map(b => ({ ...b, recognized: b.recognized_files > 0 }))
const clean = bugs.filter(b => !b.recognized)
const split = rows.filter(r => !r.unanimous)

log(`files=${rows.length} bugs=${bugs.length} recognized=${bugs.length - clean.length} CLEAN=${clean.length} split_votes=${split.length}`)

return {
  summary: {
    files_probed: rows.length,
    bugs: bugs.length,
    clean_bugs: clean.length,
    recognized_bugs: bugs.length - clean.length,
    files_with_split_votes: split.length,
  },
  clean_bugs: clean.map(b => b.bug).sort(),
  recognized_bugs: bugs.filter(b => b.recognized).map(b => b.bug).sort(),
  per_bug: bugs,
  per_file: rows,
}
