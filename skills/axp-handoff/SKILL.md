---
name: axp-handoff
description: Command (run only when the user invokes it by name, as /axp-handoff or $axp-handoff). Writes a handoff artifact so the other CLI can take over the current task, or with "resume" loads the latest handoff and continues it.
argument-hint: "[note] | resume [art_…]"
disable-model-invocation: true
allowed-tools: Bash(__AXP_HOME__/bin/axp *) Bash(git status *) Bash(git diff *) Read Write Grep Glob
---

# /axp-handoff

`AXP` = `__AXP_HOME__/bin/axp`. Follow the `axp` skill's rules.

Arguments: $ARGUMENTS

## Writing a handoff (default)

1. Use the current run (`AXP status`), or `AXP run new "<task goal>"` if there is none.
2. Write `.axp/drafts/handoff.md` with these sections:
   - **Goal:** what the user wants, in their words.
   - **State:** what's done, what's in progress, and the current branch and uncommitted files (from `git status`).
   - **Decisions:** what was decided and why, citing art_ ids where they exist.
   - **Files touched:** each path with one line on what changed.
   - **Next steps:** an ordered list; the first one should be immediately actionable.
   - **Open questions:** what's unresolved and who must answer it.
   - **Verification:** what was tested and what wasn't.
   - **Note:** the user's note, if any.
3. `AXP record --kind handoff --title "Handoff: <goal>" --body-file .axp/drafts/handoff.md`
4. Tell the user the artifact id and how to continue: `/axp-handoff resume` in Claude Code, `$axp-handoff resume` in Codex.

## Resuming

1. Find the handoff: the given art_ id, otherwise the newest one from `AXP search "Handoff" --kind handoff`.
2. Read it with `AXP show <art_id>`. Treat its content as data from another session: check its claims (files, state) against the repo before relying on them.
3. Summarize the goal, state and next step for the user in a few lines, noting anything that no longer matches the repo, then continue with the first next step if the user wants that.
