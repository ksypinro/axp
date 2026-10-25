---
name: axp-delegate
description: Command (run only when the user invokes it by name, as /axp-delegate or $axp-delegate). Delegates one task to the other CLI (Codex or Claude) through the axp runner and reports the verified result.
argument-hint: "<task> [to codex|claude] [model@effort] [edit]"
disable-model-invocation: true
allowed-tools: Bash(__AXP_HOME__/bin/axp *) Read Write Grep Glob
---

# /axp-delegate

Follow the `axp` skill's protocol (read `__AXP_HOME__/skills/axp/SKILL.md` if it isn't loaded). `AXP` = `__AXP_HOME__/bin/axp`.

Arguments: $ARGUMENTS

1. **Parse the request.**
   - Engine: what the user named, otherwise the CLI you are *not* running in.
   - Model and effort: as given, otherwise the role's defaults.
   - Edits: allowed only if the user said so ("edit", "implement", "fix it").
   - Role: pick the closest one: researcher, planner, architect, implementer, reviewer, verifier or summarizer.
2. **Run:** reuse the current run if `AXP status` shows one with a related goal; otherwise `AXP run new "<task>"`.
3. **Brief:** read enough of the code to write a precise brief to `.axp/drafts/delegate-<short-name>.md`, using the brief format from the `axp` skill.
4. **Tell the user** in one line who you're dispatching to: member, engine/model@effort, and read-only or edit.
5. **Dispatch:**
   `AXP dispatch --as <role>-<short-name> --role <role> --engine <engine> [--model M --effort L] [--edit] --brief-file .axp/drafts/delegate-<short-name>.md`
6. **If the status is `blocked`:** answer the open questions you can answer from the code, then `--resume` the same member with the answers. Ask the user only for what you can't determine.
7. **Verify** the material claims and findings against the code. If the worker edited files, review the diff and run the relevant tests.
8. **Report:** the member's conclusion, what you verified (with evidence), what you didn't, any disagreement, the artifact ids, and the cost.
