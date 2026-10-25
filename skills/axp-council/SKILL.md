---
name: axp-council
description: Command (run only when the user invokes it by name, as /axp-council or $axp-council). Runs a multi-model panel or pipeline end to end with `axp council` (plan review, design review, open council, cross-review, or a build pipeline), pausing at checkpoints and approval gates for the user.
argument-hint: "plan|design|ask|review|build \"<goal>\"  authors: e/m@x, …  critic: e/m@x  advocate: e/m@x  judge: e/m@x  orchestrator: e/m@x  rounds: N"
disable-model-invocation: true
allowed-tools: Bash(__AXP_HOME__/bin/axp *) Read Write Grep Glob
---

# /axp-council

Follow the `axp` skill's protocol (read `__AXP_HOME__/skills/axp/SKILL.md` if it isn't loaded). `AXP` = `__AXP_HOME__/bin/axp`. Details are in DESIGN.md §8 and Appendix A.

Arguments: $ARGUMENTS

## 1. Start the panel

1. **Brief.** Read the relevant code, then write the task brief to `.axp/drafts/council-brief.md`: objective, the files involved, constraints, and acceptance criteria.
2. **Run.** Pass the user's inline spec through unchanged; the runner checks every model and effort before anything runs:
   `AXP council run --inline '<the user's arguments>' --brief-file .axp/drafts/council-brief.md`
   - **Templates:** `plan` (plan review), `design`, `ask` (open council), `review` (cross-review), `build` (pipeline: plan → review → final plan → **approval** → implement → code review → verify → wrap-up).
   - **Defaults** for anything left out come from the role files.
   - **Who orchestrates.** If the user named an `orchestrator:` (e.g. `claude/opus@high`), the docket and final plan are written by that model. Otherwise *you* write them when the panel asks (see step 3).
   - **Unattended runs:** add `--on-checkpoint continue` only if the user asked. Approval gates always stop.
3. **Show the roster** from the output's stages (each member as engine/model@effort) before going further.

## 2. Read every result the same way

`axp council run`, `resume` and `status` all print the panel state: `status`, each stage's status, members, outputs and signals, and the `checkpoint` (its kind and the commands that resolve it). Act on the checkpoint's kind:

| Kind | What it means | What you do |
|---|---|---|
| `gate` | approval needed | Present the final plan (read its artifact; reveal the Plan A/B mapping from `labels`) with the decision record and the cost. **Only the user approves**: then run `AXP council resume --approve`, or `--reject`. |
| `signal` / `stage-end` | something worth a look: critical finding, low judge confidence, a failed member, budget at 80%, or the stage just ended | Summarize the stage for the user and offer the choices: continue, amend, extra round, redo, branch, stop. Carry out the one they pick. |
| `orchestrator` | a docket, tally or final stage is waiting for you | Read the stage inputs (`AXP council status`, then `AXP show <id>`), write the artifact as the stage instructions say, and submit it: `AXP council submit <stage> --kind K --title T --body-file .axp/drafts/<stage>.md` (add `--more` on all but the last). Then `AXP council resume`. |
| `stale` | an earlier redo invalidated this stage | Redo it (`AXP stage redo <stage>`), or `--keep-stale` if the user says the old result still holds |
| `failed` / `cancelled` | a stage produced nothing | `AXP stage redo <stage>`, `AXP panel amend --skip <stage> --reason …`, or `--stop` |
| `waiting-child` | a child run must finish first | Finish the child run; `AXP run close --run <child>` hands its results back |

Verify material claims in the final plan against the code before presenting it, and say what you checked.

## 3. Mid-run changes (when the user asks)

- **Change members or stages** that haven't run: `AXP panel amend --set judge=claude/opus@high --add-member security=reviewer:codex/gpt-6.1-sol@high --add-stage "security-review type=review after=debate members=security sees=draft,debate" --reason "<why>"`, then `AXP council resume --continue`. A member that already ran is re-seated automatically: it gets a new session seeded with its earlier work.
- **Redo a finished stage:** `AXP stage redo <stage> [--with member=engine/model@effort] [--revise] [--brief "<extra instructions>"]`. Downstream stages become stale.
- **Stop a running stage:** `AXP stage cancel <stage>` (on a stage that hasn't started, this skips it).
- **An extra rebuttal round:** `AXP council resume --extra-round rebuttal`.
- **Branch:** `AXP run new "<sub-goal>" --parent <run> --from art_… --wait`, run the child, and its results flow into the parent's next stages when it closes.

Always tell the user what changed (the spec version diff that `panel amend` prints) and why.

## Automatic rules (when the user wants them)

If the user asks for automatic reactions ("add an appeal judge if confidence is low", "skip judging when nothing is disputed"), write a JSON spec with an `on:` list and run it with `AXP council run --spec .axp/drafts/panel.json`. Variables, actions and limits are in `__AXP_HOME__/README.md`. Rule firings show up as `rule` checkpoints, or as amendments by `rule[i]`; report them like any other change.

After the panel, verify the findings you relied on with `AXP verify` (see the `axp` skill). Over time, `AXP routing report` and `AXP routing suggest` use those verdicts to recommend which models to use for each role.
