---
name: axp-status
description: Command (run only when the user invokes it by name, as /axp-status or $axp-status). Shows the current AXP run (members, tasks, cost, unverified findings), or searches past artifacts and findings.
argument-hint: "[run_… | search <text>]"
disable-model-invocation: true
allowed-tools: Bash(__AXP_HOME__/bin/axp *)
---

# /axp-status

`AXP` = `__AXP_HOME__/bin/axp`.

Arguments: $ARGUMENTS

- **No arguments:** run `AXP status`. If there's no store or run, say so and mention `/axp-delegate` and `/axp-council` (`$axp-…` in Codex).
- **A `run_…` id:** run `AXP status --run <id>`.
- **`search <text>`:** run `AXP search "<text>"`.
- **`runs`:** run `AXP run list`.

Show the output in a compact form: the run goal, each member as `engine/model@effort`, the tasks and their status, the total cost, and the number of unverified findings. Don't dispatch anything.
