# axp: the AXP/1 runner

`axp` lets [Claude Code](https://claude.com/claude-code) and [Codex](https://github.com/openai/codex) hand work to each other through one protocol. Every task goes out as a brief, comes back as a schema-checked result, and lands as artifacts and findings in a project-local `.axp/` store. `axp` is the only thing that writes to that store. On top of that it runs multi-model panels and pipelines (planners, critic vs. advocate, judge, implementer, verifier) with checkpoints, approval gates, mid-run changes and automatic rules. The design and its history are in [DESIGN.md](DESIGN.md).

**Requirements:** macOS or Linux, Python 3.9+ (standard library only; the tests need 3.11+), and the `codex` and `claude` CLIs on your PATH and logged in. Model names in the defaults (`roles/*.md`) come from the catalogs available when this was written; `axp roles` shows them, and `axp doctor --smoke` checks your setup.

## Install

```bash
git clone https://github.com/ksypinro/axp.git
cd axp
bin/axp install --codex-rules   # skills + agents for both CLIs, and the Codex rule
bin/axp doctor --smoke          # one cheap live call to each engine
```

`install` renders the skills into `~/.claude/skills/` and `~/.codex/skills/` with this checkout's path filled in, generates the role agents into `~/.claude/agents/` and `~/.codex/agents/`, and with `--codex-rules` adds two lines to `~/.codex/rules/default.rules` (backed up first) so Codex can call Claude. It never overwrites files it didn't create. Re-run it after editing a skill or role, or after moving the checkout; `bin/axp install --uninstall` removes everything it added. Optionally put `bin/` on your PATH so `axp` works anywhere.

## Quick start

```bash
axp doctor --smoke                       # CLIs, flags, schema, plus a live call to each engine
axp run new "Plan per-IP rate limiting"  # creates .axp/ at the git root (or cwd) and a current run

# Two planners with the same role, on different engines
echo "Plan rate limiting for src/login.py" | axp dispatch --as planner-a --role planner --engine codex  --model gpt-6-luna --effort high
echo "Plan rate limiting for src/login.py" | axp dispatch --as planner-b --role planner --engine claude --model haiku      --effort high

# Cross-engine critique of planner-a's plan (picks the engine that did not write it)
echo "Critique this plan" | axp dispatch --as critic --role critic --cross art_…

# Continue a member's own session
echo "Answer the critique" | axp dispatch --as planner-a --resume

# The orchestrator's own artifacts go through axp too
axp record --kind analysis --title "Docket" --from art_… --from art_… < docket.md

axp status          # members, tasks, cost, unverified findings
axp show art_…      # an artifact (also t_…, run_…, or a finding id)
axp search lockout  # artifacts and findings across all runs
axp verify t_…#f1 --state verified   # record whether a finding held up (feeds routing calibration)
```

`dispatch` prints a compact JSON summary (status, summary, artifact ids and paths, findings, open questions, session, cost). Exit codes: `0` for a protocol result (done, partial or blocked), `1` when the task failed, `2` when `axp` refused (usage error, guard or validation).

## Commands

| Command | What it does |
|---|---|
| `run new "<goal>" [--spec F] [--parent RUN --from ART…]` | New run, made current. `--spec` stores a panel spec as v1, after checking every member's model@effort against the live catalogs. `--parent` makes a child run and logs `run.child_created` in the parent. |
| `run list` · `run use RUN` | List runs, or switch the current run |
| `dispatch --as M [--role R] [--engine E] [--model X] [--effort L] [--resume] [--cross ART] [--input ART…] [--edit] [--worktree] [--timeout S] [--max-usd N] [--brief T \| --brief-file P]` | Send a brief to member `M` (from the flag, the file, or stdin) |
| `record --kind K --title T [--from ART…] [--body T \| --body-file P]` | Record an orchestrator artifact |
| `panel show` · `panel set F --reason "…"` · `panel diff v1 v2` | Versioned run spec: each `set` writes `panel.v<N>.json` and logs `panel.amended` |
| `status [--json]` · `show ID` · `search TEXT [--kind K] [--run R]` | Inspect the store |
| `doctor [--smoke]` | Environment checks; `--smoke` makes one real call per engine |

## Panels and pipelines (Phase 4)

`axp council` runs a whole multi-model workflow from one spec: stages run in order, members within a stage run in parallel, drafts are anonymized for reviewers, and the run pauses at checkpoints and approval gates.

```bash
axp council run --brief-file brief.md --inline \
  'plan "Per-IP lockout" authors: codex/gpt-6-luna@high, claude/haiku@high critic: codex/gpt-6.1-sol@medium advocate: claude/opus@medium judge: codex/gpt-6-astra@low orchestrator: claude/opus@high rounds: 1'
axp council status
axp council resume --approve          # at a gate
```

**Templates**, chosen by the first word of an inline spec:

| Word | Template | Stages |
|---|---|---|
| `plan` | plan-review | draft → debate (critic + advocate) → rebuttal (only while they disagree, up to `rounds`) → docket → rule (judge) → finalize → **approve** |
| `design` | design-review | the same, with architects as authors |
| `ask` | council | draft (architects answer) → docket → rule → finalize |
| `review` | cross-review | review → finalize |
| `build` | pipeline | plan → plan-review → final-plan → **approve-plan** → implement (`--edit`) → code-review → verify → wrap-up |

Inline keys: `authors:`, `planners:`, `critic:`, `advocate:`, `judge:`, `reviewers:`, `implementers:`, `verifiers:`, `orchestrator:`, `rounds:`, `checkpoints:`, `budget:`. Anything left out falls back to the role defaults. `--spec FILE` takes a JSON spec, either a template or explicit `stages`. Every member's model@effort is checked against the live catalogs before anything runs.

**Who writes the judgment stages.** The docket, tally and finalize stages are dispatched to the `orchestrator:` model (role `moderator`) if the spec names one. Otherwise the run pauses and the host CLI writes them, with `axp council submit <stage> --kind K --title T --body-file F [--more]` and then `axp council resume`.

**Checkpoints.** `checkpoints: on-signal` is the default; the others are `after-each-stage` and `none`.
- **Signals:** `critical_finding`, `low_confidence` (judge's mean confidence below 0.6), `member_failed`, `budget_80` and `disagreement`. Disagreement means the critic's high or critical defects and risks outnumber the advocate's conceded defects. It drives the rebuttal stage and only pauses when no rebuttal follows.
- **Gates** always pause, and need `--approve` or `--reject`.
- **Unattended runs:** `--on-checkpoint continue|stop` handles every other checkpoint automatically.

**Changing a run.** Changes apply from the next stage; finished stages are redone, never edited.

| Command | Effect |
|---|---|
| `axp panel amend --set M=use --add-member M=role:use --remove-member M --add-stage '…' --skip S --checkpoints P --rounds N --reason "…"` | New spec version, with the diff logged. Refuses changes to stages that have started. A removed member that already ran is retired, not deleted. |
| `axp stage redo S [--with M=use] [--revise] [--brief "…"]` | Reruns a finished, failed, cancelled or stale stage: fresh sessions by default, or the same sessions with `--revise`. Old outputs are marked superseded, downstream stages become stale, and later gates reopen. |
| `axp stage cancel S` | Stops a running stage's workers (their whole process group). On a stage that hasn't started, skips it instead. |
| `axp council resume --continue \| --stop \| --approve \| --reject \| --keep-stale \| --extra-round S` | Resolves the checkpoint and continues |
| `axp dispatch --as M --reseat [--engine E --model X --effort L]` | Gives a member a new session, seeded with its earlier artifacts. Logs `member.reseated` and keeps the old session in its history. Panels do this automatically when an amendment changes a member that already ran. |
| `axp run new "<goal>" --parent RUN --from ART --wait` · `axp run close [--result ART]` | Child run. The parent pauses (`waiting-child`) until the child closes, then receives its results as inputs to its next stages. A finished panel closes its own run. |

**`on:` rules (Phase 5).** These are automatic reactions written into a JSON spec, checked at every stage boundary (after each stage, including stages submitted by the host):

```json
"on": [
  {"if": "docket.empty", "then": {"skip": ["rule"]}},
  {"if": "judge.confidence < 0.6",
   "then": {"add_member": {"judge-2": {"role": "judge", "use": "claude/opus@high"}},
            "add_stage": "appeal type=rule after=rule members=judge-2 sees=docket,rule"}},
  {"if": "findings.critical > 0", "then": {"checkpoint": true}, "repeat": true}
]
```

- **Conditions** come from a fixed list; no code runs.
  - `findings.critical` and `findings.high` count the last finished stage's findings at that severity (`high` includes critical); `members.failed` counts its failed members.
  - `judge.confidence` is the mean confidence of the latest ruling's findings.
  - `docket.size` counts the docket's `| D<n> |` rows; `docket.empty` is true when there are none.
  - `budget.used` is a percentage of `budget.max_usd`.
  - `stage.<name>.status` is any stage's status.
  - A variable that isn't defined yet (say, no docket so far) never fires.
- **Actions:** `checkpoint`, `skip`, `set`, `add_member`, `add_stage`, `redo` and `stop`.
- **Each firing** logs `rule.fired`. Spec changes are ordinary amendments by `rule[i]`, with the diff logged.
- **Limits:**
  - A rule fires once per run unless it sets `"repeat": true` (then at most once per boundary).
  - A rule can't add a stage that edits files.
  - A failing action pauses the run with a `rule-error` checkpoint.
  - Rule-added members count toward the budget.

Panel state lives in `runs/<run>/council.json`. `axp status` shows the stages and the current checkpoint.

## Verification and routing calibration (Phase 5)

```bash
axp verify t_…#f1 t_…#f2 --state verified --note "confirmed in src/login.py:7"   # or refuted / unverifiable
axp routing report [--role R] [--store PATH …] [--json]
axp routing suggest [--min-tasks 3] [--min-judged 5] [--store PATH …] [--apply]
```

- **`verify`** records whether a finding (or artifact) held up. It's logged as `finding.verified`, `finding.refuted` and so on, and the index keeps the latest verdict. That's what gives routing a quality signal, so verify the findings you checked as you go.
- **`routing report`** reads every ledger in the given projects (default: the current one). For each role and engine/model@effort it shows:
  - tasks, how many finished `done`, and how many needed a schema repair;
  - average time and cost (Claude in USD, Codex in tokens);
  - how many findings were judged, and the precision: verified / (verified + refuted).
- **`routing suggest`** ranks candidates per role and engine:
  - by findings precision, once at least `--min-judged` findings have been judged;
  - otherwise by done rate, then fewer repairs, then lower cost, then speed.

  It only recommends a change after comparing against data for the current default. An untested default is shown as `untested`, never replaced just because something cheaper happened to run.
- **`--apply`** rewrites only the changed engine's `model` and `effort` lines in `roles/*.md`; run `axp sync-roles` afterwards.

## Members and sessions

- A **member** is a named participant (`--as planner-a`). Sessions are keyed by member, so two members can share a role.
- A new member needs `--role`. Engine, model and effort come from your flags, otherwise from the role defaults below.
- Afterwards, `--as M --resume` continues the same session. Changing a member's engine, model or effort on resume is refused; use `--reseat` for that, or a new member name for a different role.
- Dispatching an existing member without `--resume` is refused, so a session can't be silently replaced.
- A member can only run one task at a time.

## Roles (Phase 3)

Roles are defined once in [roles/](roles/)`<name>.md`: frontmatter with the default engine and per-engine settings, then the role's instructions. That one file drives three things:

1. **`axp dispatch --role <name>`** takes the engine, model and effort defaults from it, and adds the role's instructions to the worker's brief.
2. **`axp sync-roles`** generates a native Claude subagent `~/.claude/agents/axp-<name>.md` and a native Codex agent role `~/.codex/agents/axp-<name>.toml`. Use them through Claude's Agent tool (`subagent_type: "axp-critic"`) or Codex's `spawn_agent` (`agent_type: "axp-critic"`).
3. **Two bridges** are generated as well: `axp-codex-bridge` (a Claude subagent) and `axp-claude-bridge` (a Codex role). Each forwards one task to the other engine through `axp dispatch` and returns the runner's JSON unchanged. That lets a host fan out to the other engine through its own agent tool.

```bash
axp roles                 # list roles and their defaults
axp sync-roles --check    # show what would change; exit 1 if anything would
axp sync-roles            # write; re-run after editing any roles/*.md
```

Rules for `sync-roles`:
- It only touches `axp-*` files that carry its "generated by axp sync-roles" marker.
- It refuses to overwrite a file it didn't generate, and deletes generated files whose role no longer exists.
- Edit the role file, never the generated copies.

Role frontmatter is a restricted YAML subset: `key: value`, inline lists `[a, b]`, and one nested level (`claude:` / `codex:` with two-space-indented keys):

```markdown
---
name: critic
description: Attacks plans and designs …
produces: [review]
default_engine: codex          # codex | claude | cross (the engine that isn't the host)
claude:
  model: opus
  effort: medium
  tools: [Read, Grep, Glob]    # native Claude subagent only
codex:
  model: gpt-6.1-sol
  effort: medium
  sandbox: read-only           # native Codex role only: read-only | workspace-write
---
Role instructions …
```

**Permissions come from the caller, not the role.** `tools` and `sandbox` apply only to the generated native agents. Through `axp dispatch`, a worker is read-only unless the call passes `--edit`.

**The Claude bridge needs permission.** Its Bash call to `axp` asks once in Claude Code (subagents don't inherit a skill's `allowed-tools`). To skip the prompt, allow `Bash(/path/to/axp/bin/axp *)` in your settings. The Codex bridge is covered by the Codex rule.

### Shipped roles

| Role | Default engine | Codex | Claude |
|---|---|---|---|
| implementer | **codex** | gpt-6-sol@medium | sonnet@medium |
| planner, architect | claude | gpt-6.1-sol@high | fable@high |
| researcher | claude | gpt-6-luna@medium | sonnet@medium |
| reviewer, verifier | the engine that isn't the host | gpt-6.1-sol@high | opus@high |
| critic | codex | gpt-6.1-sol@medium | opus@medium |
| advocate | claude | gpt-6.1-sol@medium | opus@medium |
| judge | codex | gpt-6-astra@low | sonnet@medium |
| summarizer | claude | gpt-5.6-luna@low | haiku@low |

How engine, model and effort are chosen:

- **Engine:** `--engine` wins, then `--cross` (the engine that did not write that artifact), then the role default.
- **Explicit values are validated.** A model or effort that doesn't exist (checked against `codex debug models`, cached for a day, or the Claude aliases) is rejected.
- **Role defaults are clamped** to the nearest supported effort.

## Guards

- **Read-only by default.** Codex runs with `--sandbox read-only`; Claude gets only `Read,Grep,Glob`.
- **Edits need `--edit`**, which gives Codex `workspace-write` and Claude `acceptEdits`. `--worktree` adds a git worktree. An implementer without `--edit` is refused.
- **Depth:** workers get `AXP_DEPTH=1`, so a worker calling `axp` is refused (`AXP_MAX_DEPTH` raises the limit).
- **No ping-pong:** `AXP_CHAIN` refuses Claude→Codex→Claude unless `AXP_ALLOW_PINGPONG` is set.
- **Secrets:** briefs and records that look like they contain secrets are refused.
- **Clean environment:** workers don't inherit the host's Claude Code session variables (messaging socket and token, session ids).
- **Schema repair:** a result that doesn't match the schema gets one repair attempt in the same session; a second failure is logged as `task.failed`.

## Skills (Phase 2)

The skills live in [skills/](skills/). `axp install` renders each one into `~/.claude/skills/` and `~/.codex/skills/`, so a single source serves both CLIs.

| Skill | Claude Code | Codex | What it does |
|---|---|---|---|
| `axp` | used automatically when relevant | used automatically when relevant | The protocol: the brief → dispatch → verify → record loop, host differences, model choice, rules |
| `axp-delegate` | `/axp-delegate …` | `$axp-delegate …` | Hand one task to the other CLI, then verify and report |
| `axp-review` | `/axp-review [diff\|paths\|art_…] [model@effort]` | `$axp-review …` | Cross-engine review, then verify each finding and record a verdict |
| `axp-council` | `/axp-council plan "<goal>" authors: … critic: … advocate: … judge: …` | `$axp-council …` | Panel per DESIGN.md Appendix A, orchestrated by the host, ending at your approval |
| `axp-handoff` | `/axp-handoff [note]` · `/axp-handoff resume` | `$axp-handoff …` | Pass the current task to the other CLI and pick it up there |
| `axp-status` | `/axp-status [search …]` | `$axp-status …` | Current run, members, cost, unverified findings |

- **Permissions in Claude Code:** each skill's `allowed-tools` grants `Bash(…/axp/bin/axp *)` while that skill is in use, so nothing was added to your global settings.
- **Model invocation:** in Claude Code the command skills are `disable-model-invocation: true`. Codex has no such switch, so their descriptions tell the model to run them only when called by name.
- **Absolute path:** the sources say `__AXP_HOME__`, and `install` fills in your checkout's path, so the skills work without `axp` on your PATH. If you move the checkout, re-run `install` (and update the Codex rule).

## Calling Claude from Codex

Codex's default sandbox blocks Claude (it reports "Not logged in"). This rule in `~/.codex/rules/default.rules` pre-approves the runner, so it runs outside the sandbox while everything else stays sandboxed:

```
prefix_rule(pattern=["/path/to/axp/bin/axp", "dispatch"], decision="allow")
prefix_rule(pattern=["/path/to/axp/bin/axp", "council"], decision="allow")
```

The `council` rule (Phase 4) covers `council run` and `council resume`, which launch workers. The other commands only read and write the store, which the sandbox allows anyway.

Verified 2026-10-25: a Codex session with the default `workspace-write` sandbox and no network override dispatched to Claude successfully. The rule only matches a single plain command, which is why `dispatch` accepts `--brief "…"` and `--brief-file PATH` (and `record` accepts `--body`/`--body-file`): no pipes needed. Hosts write drafts to `.axp/drafts/`, which is gitignored. `axp install --codex-rules` adds these lines for you (with your path), backing the file up to `default.rules.bak-axp` first.

## Store layout

```
.axp/
  store.json  index.jsonl  current  .gitignore  cache/  knowledge/
  runs/<run>/run.json  panel.json  panel.v<N>.json  sessions.json  ledger.jsonl
             briefs/<task>.md  artifacts/<art>.md  raw/<task>.{stdout,stderr,result.json}
```

`raw/` is gitignored. Artifacts are Markdown with frontmatter carrying provenance stamped by the runner (engine, model, resolved model id, effort, member, session, cost).

## Tests

```bash
python3 -m unittest discover -s axp/tests    # offline: engines are faked
axp doctor --smoke                           # live: one cheap call per engine
```
