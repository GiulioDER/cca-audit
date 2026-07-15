export const meta = {
  name: 'cca-bugsinpy-detect',
  description: 'CCA blind bug-detection over a 12-bug BugsInPy sample: audit buggy+fixed files, recognition probe, fp-check gate; returns structured findings for external scoring',
  phases: [{ title: 'Audit', detail: 'blind auditors on buggy+fixed + recognition probe' }, { title: 'Verify', detail: 'fp-check anti-hallucination gate' }],
}

const FINDING = {
  type: 'object', additionalProperties: false,
  properties: {
    severity: { type: 'string', enum: ['Critical', 'High', 'Medium', 'Low'] },
    line: { type: 'number' },
    symbol: { type: 'string' },
    title: { type: 'string' },
    why: { type: 'string' },
  },
  required: ['severity', 'line', 'title'],
}
const FINDINGS_OBJ = {
  type: 'object', additionalProperties: false,
  properties: { findings: { type: 'array', items: FINDING } },
  required: ['findings'],
}
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
const FPCHECK = {
  type: 'object', additionalProperties: false,
  properties: { confirmed: { type: 'array', items: FINDING }, dropped: { type: 'array', items: FINDING } },
  required: ['confirmed'],
}

const detect = (path, kind) =>
  `You are doing whole-file BLIND ${kind} detection on ONE Python source file (a hunt for PRE-EXISTING bugs). Read the ENTIRE file and find GENUINE DEFECTS` +
  (kind === 'numeric'
    ? ` — sign/units/scaling errors, off-by-one, wrong geometric/trig/transform math, rounding, or wrong-branch conditionals.`
    : ` — runtime bugs, logic errors, incorrect conditionals, edge cases that yield wrong output or crashes.`) +
  ` Do NOT report style, naming, docs, or performance. Give exact line numbers. If there are no genuine defects, return an empty findings list.\n\nFile to audit (read it fully): ${path}`

const recog = (path) =>
  `Read this source file fully: ${path}\n\nHonestly report whether you RECOGNIZE this exact file from your training data — i.e. you know which open-source project/version it is and could reproduce its canonical upstream implementation from memory (which would let you find bugs by comparing to the known-correct version rather than reasoning from first principles). Set recognized=true ONLY if you genuinely recognize the specific code, not merely the library name. Give a short note on the evidence.`

const fp = (path, findings) =>
  `Adversarially verify these candidate bug findings against the ACTUAL code. Read the file fully: ${path}\n\nFor EACH finding decide: is it a TRUE defect provable from the code, or a FALSE POSITIVE (guarded elsewhere, misread control/data flow, or simply not a bug)? Default to DROPPING when you cannot prove the defect from the code itself. Put true positives in "confirmed" and false positives in "dropped" (same fields).\n\nCandidate findings:\n${JSON.stringify(findings)}`

async function auditors(path, numeric, phase, label) {
  const jobs = [() => agent(detect(path, 'bug'), { agentType: 'bug-auditor', schema: FINDINGS_OBJ, phase, label: `bug:${label}` })]
  if (numeric) jobs.push(() => agent(detect(path, 'numeric'), { agentType: 'numeric-auditor', schema: FINDINGS_OBJ, phase, label: `num:${label}` }))
  const res = await parallel(jobs)
  return res.filter(Boolean).flatMap((r) => (r && r.findings) || [])
}

const TASKS = Array.isArray(args) ? args : JSON.parse(args)
const results = await pipeline(TASKS,
  async (t) => {
    const [buggy_raw, fixed_raw, recognition] = await parallel([
      () => auditors(t.buggy_path, t.numeric, 'Audit', `${t.bug}/buggy`),
      () => auditors(t.fixed_path, t.numeric, 'Audit', `${t.bug}/fixed`),
      () => agent(recog(t.buggy_path), { agentType: 'general-purpose', schema: RECOG, effort: 'low', phase: 'Audit', label: `recog:${t.bug}` }),
    ])
    return { ...t, buggy_raw: buggy_raw || [], fixed_raw: fixed_raw || [], recognition: recognition || { recognized: null } }
  },
  async (r) => {
    const [bchk, fchk] = await parallel([
      () => (r.buggy_raw.length ? agent(fp(r.buggy_path, r.buggy_raw), { agentType: 'fp-check', schema: FPCHECK, phase: 'Verify', label: `fp-buggy:${r.bug}` }) : Promise.resolve({ confirmed: [], dropped: [] })),
      () => (r.fixed_raw.length ? agent(fp(r.fixed_path, r.fixed_raw), { agentType: 'fp-check', schema: FPCHECK, phase: 'Verify', label: `fp-fixed:${r.bug}` }) : Promise.resolve({ confirmed: [], dropped: [] })),
    ])
    return {
      bug: r.bug, project: r.project, file: r.file,
      recognized: r.recognition.recognized, recog_note: r.recognition.note || '',
      buggy_raw: r.buggy_raw, fixed_raw: r.fixed_raw,
      buggy_confirmed: (bchk && bchk.confirmed) || [], buggy_dropped: (bchk && bchk.dropped) || [],
      fixed_confirmed: (fchk && fchk.confirmed) || [], fixed_dropped: (fchk && fchk.dropped) || [],
    }
  }
)
log(`done: ${results.filter(Boolean).length}/${TASKS.length} file-tasks`)
return results