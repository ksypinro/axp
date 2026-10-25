---
name: axp-review
description: Command (run only when the user invokes it by name, as /axp-review or $axp-review). Gets a cross-engine review of the current changes, given files, or an AXP artifact from the other CLI via the axp runner, then verifies each finding.
argument-hint: "[diff | <paths> | art_…] [model@effort] [focus]"
disable-model-invocation: true
allowed-tools: Bash(__AXP_HOME__/bin/axp *) Bash(git diff *) Bash(git status *) Read Write Grep Glob
---

# /axp-review

Follow the `axp` skill's protocol (read `__AXP_HOME__/skills/axp/SKILL.md` if it isn't loaded). `AXP` = `__AXP_HOME__/bin/axp`.

Arguments: $ARGUMENTS

1. **Pick the target.**
   - An `art_…` id: review that artifact. Use `--cross art_…`, which picks the engine that didn't write it and passes it as input.
   - Paths: review those files.
   - Nothing, or "diff": review the uncommitted changes (`git diff` plus untracked files from `git status`). If there are none, ask what to review.
2. **Pick the reviewer:** the other CLI's engine (or `--cross` as above), role `reviewer`, and the user's model@effort if given (otherwise the role default).
3. **Write the brief** to `.axp/drafts/review-<short-name>.md`:
   - **Objective:** review the target for correctness, security, data integrity and missing tests (plus any focus the user gave).
   - **Context:** the file paths, and for a diff, the list of changed files. Don't paste large diffs; the reviewer can read files and run `git diff` itself.
   - **Constraints:** read-only. Each finding needs a file:line reference and a concrete failure scenario.
   - **Deliverable:** one `review` artifact plus findings ranked by severity.
4. **Tell the user** in one line who is reviewing (engine/model@effort), then dispatch:
   `AXP dispatch --as reviewer-<short-name> --role reviewer [--engine E | --cross art_…] [--model M --effort L] --brief-file .axp/drafts/review-<short-name>.md`
5. **Verify every finding** by reading the cited code, and run a check where one is cheap. Classify each as confirmed, plausible, or refuted (with the reason), and record it:
   `AXP verify <finding id> … --state verified` for confirmed ones, `--state refuted` for refuted ones, and `--state unverifiable` for plausible ones, each with a `--note` giving the evidence.
6. **Record your verdict:**
   `AXP record --kind review --title "Verified review: <target>" --from <reviewer art id> --body-file .axp/drafts/review-<short-name>-verdict.md`
7. **Report** the confirmed findings first (severity, file:line, the fix), then plausible ones, then the refuted ones in a line each. Name the reviewer's engine and model, and include the artifact ids and cost. Don't fix anything unless the user asks.
