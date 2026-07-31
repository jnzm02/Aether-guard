export const meta = {
  name: 'feature-team',
  description: 'Run a team of agents to design, adversarially review, and synthesize an implementation plan for a feature in Aether-Guard',
  whenToUse: 'When you want several independent perspectives on HOW to build something, cross-checked, before writing code',
  phases: [
    { title: 'Plan' },
    { title: 'Design' },
    { title: 'Review' },
    { title: 'Synthesize' },
  ],
}

// The task to work on. Pass it as the Workflow `args`, e.g. { task: "add a /health
// endpoint to the listener" }. Kept as a plain string so the workflow is reusable.
const task = (args && args.task) || 'No task provided — pass { task: "..." } as args.'

// ── 1. Plan ───────────────────────────────────────────────────────────────────
// One planner decomposes the task into independent subtasks that can be designed
// in parallel.
phase('Plan')
const PLAN_SCHEMA = {
  type: 'object',
  properties: {
    subtasks: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          id: { type: 'string' },
          title: { type: 'string' },
          detail: { type: 'string' },
        },
        required: ['id', 'title', 'detail'],
      },
    },
  },
  required: ['subtasks'],
}

const plan = await agent(
  `You are the planner for this task in the Aether-Guard repo:\n\n${task}\n\n` +
  'Read CLAUDE.md and the relevant code first, then decompose the task into 2-5 ' +
  'genuinely independent subtasks that could be designed in parallel. Respect the ' +
  "project's safety rules (never weaken DRY_RUN / policy / confidence thresholds).",
  { label: 'plan', phase: 'Plan', schema: PLAN_SCHEMA }
)

const subtasks = (plan && plan.subtasks) || []
if (!subtasks.length) {
  log('Planner produced no subtasks — aborting.')
  return { task, error: 'no subtasks' }
}
log(`Planner produced ${subtasks.length} subtasks`)

// ── 2 + 3. Design → adversarial review (pipelined per subtask) ─────────────────
// Each subtask is designed, then a second agent tries to refute the design. No
// barrier: a subtask can be under review while another is still being designed.
const DESIGN_SCHEMA = {
  type: 'object',
  properties: {
    approach: { type: 'string' },
    files: { type: 'array', items: { type: 'string' } },
    risks: { type: 'string' },
  },
  required: ['approach'],
}
const REVIEW_SCHEMA = {
  type: 'object',
  properties: {
    verdict: { type: 'string', enum: ['solid', 'needs-work'] },
    issues: { type: 'array', items: { type: 'string' } },
    suggestion: { type: 'string' },
  },
  required: ['verdict'],
}

const reviewed = await pipeline(
  subtasks,
  (st) => agent(
    `Design the implementation for subtask "${st.title}" of the task: ${task}\n\n` +
    `Detail: ${st.detail}\n\n` +
    'Propose a concrete approach, the files you would touch, and the risks. Match the ' +
    "existing code style and never weaken a safety layer.",
    { label: `design:${st.id}`, phase: 'Design', schema: DESIGN_SCHEMA }
  ),
  (design, st) => agent(
    `Adversarially review this design for subtask "${st.title}":\n\n` +
    `${JSON.stringify(design)}\n\n` +
    'Try hard to find where it is wrong, unsafe, or over-complex. Return verdict ' +
    '"needs-work" unless it is genuinely solid.',
    { label: `review:${st.id}`, phase: 'Review', schema: REVIEW_SCHEMA }
  ).then((review) => ({ subtask: st, design, review }))
)

const results = reviewed.filter(Boolean)

// ── 4. Synthesize ─────────────────────────────────────────────────────────────
// One agent folds the reviewed designs into a single coherent plan.
phase('Synthesize')
const synthesis = await agent(
  `You are the synthesizer. Task: ${task}\n\n` +
  `Designed and adversarially reviewed subtasks:\n${JSON.stringify(results, null, 2)}\n\n` +
  'Produce a single coherent implementation plan: ordered steps, files to change, and ' +
  "the top risks to watch. Fold in the reviewers' valid concerns and drop any design " +
  'the reviewers judged unsalvageable. Output the plan — do not write code.',
  { label: 'synthesize', phase: 'Synthesize' }
)

return { task, subtasks: results.length, plan: synthesis }

// To make this a *building* team rather than a *planning* team, add
// `isolation: 'worktree'` to the design-stage agent() options and instruct those
// agents to edit files; each runs in its own git worktree so parallel edits don't
// collide, and the synthesizer merges the results.
