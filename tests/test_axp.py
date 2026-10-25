"""Offline tests for the axp runner. Engines are faked; no model is called.

Run: python3 -m unittest discover -s axp/tests   (from the DevAII folder)
"""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

AXP_PATH = Path(__file__).resolve().parents[1] / "bin" / "axp"
loader = importlib.machinery.SourceFileLoader("axp", str(AXP_PATH))
spec = importlib.util.spec_from_loader("axp", loader)
axp = importlib.util.module_from_spec(spec)
loader.exec_module(axp)

CATALOG = {"gpt-6-luna": ["low", "medium", "high", "xhigh", "max"],
           "gpt-6.1-sol": ["low", "medium", "high", "xhigh", "max", "ultra"],
           "gpt-6-sol": ["low", "medium", "high", "xhigh", "max", "ultra"],
           "gpt-6-astra": ["low", "medium", "high", "xhigh", "max", "ultra"]}


def result(task_id="t_x", status="done", artifacts=None, findings=None):
    return {"protocol": "axp/1", "task_id": task_id, "status": status, "summary": "ok",
            "artifacts": artifacts if artifacts is not None else
            [{"kind": "plan", "title": "Plan", "content": "# Plan\nstep 1", "derived_from": []}],
            "findings": findings if findings is not None else
            [{"id": "f1", "kind": "risk", "severity": "high", "claim": "NAT users share an IP",
              "evidence": [{"type": "file", "ref": "src/a.py:3", "detail": None}], "confidence": 0.8}],
            "open_questions": [], "next_actions": []}


class FakeEngines:
    """Stands in for run_engine; records calls and returns queued outputs."""

    def __init__(self):
        self.calls = []
        self.queue = []

    def __call__(self, engine, **kw):
        self.calls.append({"engine": engine, **kw})
        if self.queue:
            out = self.queue.pop(0)
        else:
            out = {"result": result(), "handle": kw["handle"] or f"{engine}-session-{len(self.calls)}"}
        return {"cost": {"usd": 0.01, "output_tokens": 10}, "seconds": 1.0, "error": None,
                "model_id": None, "handle": kw["handle"], **out}


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.cwd = os.getcwd()
        os.chdir(self.root)
        self.env = mock.patch.dict(os.environ, {"AXP_HOST": "claude"}, clear=False)
        self.env.start()
        for k in ("AXP_STORE", "AXP_RUN", "AXP_DEPTH", "AXP_CHAIN", "AXP_MAX_DEPTH"):
            os.environ.pop(k, None)
        self.fake = FakeEngines()
        self.patch = mock.patch.object(axp, "run_engine", self.fake)
        self.patch.start()
        self.catalog = mock.patch.object(axp, "codex_catalog", lambda store, refresh=False: CATALOG)
        self.catalog.start()

    def tearDown(self):
        self.catalog.stop()
        self.patch.stop()
        self.env.stop()
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def axp(self, *argv, stdin=""):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(stdin)), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = axp.main(list(argv))
        text = out.getvalue()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = text
        return code, data, err.getvalue()

    def new_run(self, *extra):
        code, data, err = self.axp("run", "new", "Plan rate limiting", *extra)
        self.assertEqual(code, 0, err)
        axp.write_json(self.root / ".axp" / "cache" / "codex_models.json",
                       {"fetched": time.time(), "models": CATALOG})
        return data["run"]

    def run_dir(self, run_id):
        return self.root / ".axp" / "runs" / run_id

    def ledger_events(self, run_id):
        return [json.loads(l)["event"] for l in (self.run_dir(run_id) / "ledger.jsonl").read_text().splitlines()]


class TestPureFunctions(unittest.TestCase):
    def test_ids_are_prefixed_and_sortable(self):
        a = axp.new_id("art")
        time.sleep(0.002)
        b = axp.new_id("art")
        self.assertRegex(a, r"^art_[0-9A-HJKMNP-TV-Z]{26}$")
        self.assertLess(a, b)

    def test_validate_accepts_a_good_result(self):
        self.assertEqual(axp.validate(result(), axp.load_schema()), [])

    def test_validate_reports_missing_extra_and_enum_errors(self):
        bad = result()
        del bad["summary"]
        bad["extra"] = 1
        bad["status"] = "finished"
        errors = axp.validate(bad, axp.load_schema())
        self.assertTrue(any("missing summary" in e for e in errors))
        self.assertTrue(any("unexpected extra" in e for e in errors))
        self.assertTrue(any("finished" in e for e in errors))

    def test_validate_nested_nullable(self):
        r = result()
        r["findings"][0]["evidence"][0]["detail"] = 5
        self.assertTrue(axp.validate(r, axp.load_schema()))

    def test_parse_use(self):
        self.assertEqual(axp.parse_use("codex/gpt-6-luna@high"), ("codex", "gpt-6-luna", "high"))
        for bad in ("codex/gpt-6-luna", "gemini/x@low", "claude/opus@extreme"):
            with self.assertRaises(axp.AxpError):
                axp.parse_use(bad)

    def test_secret_guard(self):
        with self.assertRaises(axp.AxpError):
            axp.guard_secrets("key: sk-ant-" + "a" * 30)
        with self.assertRaises(axp.AxpError):
            axp.guard_secrets("-----BEGIN RSA PRIVATE KEY-----")
        axp.guard_secrets("review src/auth/login.ts for token handling")

    def test_depth_and_chain_guards(self):
        with mock.patch.dict(os.environ, {"AXP_DEPTH": "1"}):
            with self.assertRaises(axp.AxpError):
                axp.guard_depth()
        with mock.patch.dict(os.environ, {"AXP_CHAIN": "claude>codex"}):
            with self.assertRaises(axp.AxpError):
                axp.guard_chain("claude")
        with mock.patch.dict(os.environ, {"AXP_CHAIN": "claude"}):
            self.assertEqual(axp.guard_chain("codex"), "claude>codex")

    def test_child_env_strips_host_session_variables(self):
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_MESSAGING_TOKEN": "x", "CLAUDE_CODE_SESSION_ID": "y"}):
            env = axp.child_env(0, "codex", "run_1", "t_1")
        self.assertNotIn("CLAUDE_CODE_MESSAGING_TOKEN", env)
        self.assertNotIn("CLAUDE_CODE_SESSION_ID", env)
        self.assertEqual(env["AXP_DEPTH"], "1")
        self.assertEqual(env["AXP_CHAIN"], "codex")

    def test_codex_command_puts_flags_before_resume_and_reads_stdin(self):
        with tempfile.TemporaryDirectory() as d:
            cmd = axp.codex_command("gpt-6-luna", "high", False, False, Path(d) / "o.json", "thread-1", d)
        self.assertEqual(cmd[-3:], ["resume", "thread-1", "-"])
        self.assertLess(cmd.index("--output-schema"), cmd.index("resume"))
        self.assertIn("--skip-git-repo-check", cmd)  # temp dir is not a git repo
        self.assertEqual(cmd[cmd.index("--sandbox") + 1], "read-only")
        self.assertIn('model_reasoning_effort="high"', cmd)

    def test_codex_command_edit_mode(self):
        with tempfile.TemporaryDirectory() as d:
            cmd = axp.codex_command("gpt-6-sol", "medium", True, True, Path(d) / "o.json", None, d)
        self.assertEqual(cmd[cmd.index("--sandbox") + 1], "workspace-write")
        self.assertIn("--worktree", cmd)
        self.assertNotIn("resume", cmd)

    def test_claude_command_has_no_positional_prompt(self):
        new = axp.claude_command("haiku", "high", False, False, None, None, "t_1", "{}")
        self.assertIn("--session-id", new)
        self.assertEqual(new[new.index("--tools") + 1], "Read,Grep,Glob")
        self.assertTrue(new[-1].count("-") == 4)  # ends with the session uuid, not a prompt
        resumed = axp.claude_command("haiku", "high", False, False, "abc", 2.0, "t_1", "{}")
        self.assertEqual(resumed[-2:], ["-r", "abc"])
        self.assertIn("--max-budget-usd", resumed)


class TestModelResolution(StoreTest):
    def setUp(self):
        super().setUp()
        self.new_run()
        self.store = axp.Store.find()

    def test_claude_ultra_rejected_when_explicit_and_clamped_when_default(self):
        with self.assertRaises(axp.AxpError):
            axp.resolve_model(self.store, "claude", "opus", "ultra", explicit=True)
        self.assertEqual(axp.resolve_model(self.store, "claude", "opus", "ultra", explicit=False), ("opus", "max"))

    def test_codex_unknown_model_rejected(self):
        with self.assertRaises(axp.AxpError):
            axp.resolve_model(self.store, "codex", "gpt-9", "low", explicit=True)

    def test_codex_effort_rejected_when_explicit_and_clamped_when_default(self):
        with self.assertRaises(axp.AxpError):
            axp.resolve_model(self.store, "codex", "gpt-6-luna", "ultra", explicit=True)
        self.assertEqual(axp.resolve_model(self.store, "codex", "gpt-6-luna", "ultra", explicit=False),
                         ("gpt-6-luna", "max"))


class TestRuns(StoreTest):
    def test_run_new_creates_marked_store_and_current(self):
        run_id = self.new_run()
        self.assertTrue((self.root / ".axp" / "store.json").exists())
        self.assertEqual((self.root / ".axp" / "current").read_text().strip(), run_id)
        self.assertIn("runs/*/raw/", (self.root / ".axp" / ".gitignore").read_text())
        self.assertEqual(self.ledger_events(run_id), ["run.created"])

    def test_spec_versions_and_diff(self):
        spec = {"members": {"critic": {"role": "critic", "use": "codex/gpt-6.1-sol@medium"}}}
        Path("v1.json").write_text(json.dumps(spec))
        run_id = self.new_run("--spec", "v1.json")
        spec["members"]["judge"] = {"role": "judge", "use": "claude/opus@high"}
        Path("v2.json").write_text(json.dumps(spec))
        code, data, err = self.axp("panel", "set", "v2.json", "--reason", "upgrade judge")
        self.assertEqual((code, data["spec_version"]), (0, 2), err)
        d = self.run_dir(run_id)
        self.assertTrue((d / "panel.v1.json").exists() and (d / "panel.v2.json").exists())
        self.assertIn("panel.amended", self.ledger_events(run_id))
        code, text, _ = self.axp("panel", "diff", "v1", "v2")
        self.assertIn('+    "judge"', text)

    def test_bad_spec_rejected(self):
        Path("bad.json").write_text(json.dumps({"members": {"x": {"role": "critic", "use": "codex/gpt@nope"}}}))
        code, _, err = self.axp("run", "new", "g", "--spec", "bad.json")
        self.assertEqual(code, 2)
        self.assertIn("shorthand", err)

    def test_spec_with_unknown_model_or_effort_rejected_before_running(self):
        for use in ("codex/gpt-9@low", "codex/gpt-6-luna@ultra", "claude/opus@ultra"):
            Path("s.json").write_text(json.dumps({"members": {"judge": {"role": "judge", "use": use}}}))
            code, _, err = self.axp("run", "new", "g", "--spec", "s.json")
            self.assertEqual(code, 2, use)
            self.assertIn("member judge", err)
        self.assertFalse((self.root / ".axp" / "runs").exists() and any((self.root / ".axp" / "runs").iterdir()))

    def test_child_run_logged_in_parent(self):
        parent = self.new_run()
        self.axp("dispatch", "--as", "planner-a", "--role", "planner", "--engine", "claude", stdin="plan")
        art = json.loads((self.root / ".axp" / "index.jsonl").read_text().splitlines()[0])["id"]
        code, data, err = self.axp("run", "new", "Council on D2", "--parent", parent, "--from", art)
        self.assertEqual(code, 0, err)
        self.assertIn("run.child_created", self.ledger_events(parent))


class TestDispatch(StoreTest):
    def test_two_members_same_role_get_separate_sessions(self):
        run_id = self.new_run()
        c1, r1, e1 = self.axp("dispatch", "--as", "planner-a", "--role", "planner", "--engine", "codex",
                              "--model", "gpt-6-luna", "--effort", "high", stdin="Plan it")
        c2, r2, e2 = self.axp("dispatch", "--as", "planner-b", "--role", "planner", "--engine", "claude",
                              "--model", "haiku", "--effort", "high", stdin="Plan it")
        self.assertEqual((c1, c2), (0, 0), e1 + e2)
        sessions = json.loads((self.run_dir(run_id) / "sessions.json").read_text())
        self.assertEqual(set(sessions), {"planner-a", "planner-b"})
        self.assertNotEqual(sessions["planner-a"]["handle"], sessions["planner-b"]["handle"])
        self.assertEqual(sessions["planner-a"]["engine"], "codex")
        self.assertNotIn("busy", sessions["planner-a"])

    def test_artifacts_findings_and_provenance(self):
        run_id = self.new_run()
        code, r, err = self.axp("dispatch", "--as", "planner-a", "--role", "planner", "--engine", "codex",
                                "--model", "gpt-6-luna", "--effort", "high", stdin="Plan it")
        self.assertEqual(code, 0, err)
        art = r["artifacts"][0]
        text = (self.root / art["path"]).read_text()
        self.assertIn("kind: plan", text)
        self.assertIn('"engine": "codex"', text)
        self.assertIn('"member": "planner-a"', text)
        self.assertEqual(r["findings"][0]["id"], f"{r['task']}#f1")
        brief = (self.run_dir(run_id) / "briefs" / f"{r['task']}.md").read_text()
        self.assertIn(f'"task_id": "{r["task"]}"', brief)
        self.assertTrue(brief.rstrip().endswith("Plan it"))
        events = self.ledger_events(run_id)
        for ev in ("task.dispatched", "session.created", "artifact.registered", "task.completed"):
            self.assertIn(ev, events)

    def test_resume_uses_recorded_session_and_rejects_changes(self):
        self.new_run()
        self.axp("dispatch", "--as", "critic", "--role", "critic", stdin="Review")
        handle = self.fake.calls[-1]
        code, r, err = self.axp("dispatch", "--as", "critic", "--resume", stdin="Rebut")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.fake.calls[-1]["handle"], r["session"])
        self.assertEqual(self.fake.calls[-1]["engine"], handle["engine"])
        code, _, err = self.axp("dispatch", "--as", "critic", "--resume", "--engine", "claude", stdin="x")
        self.assertEqual(code, 2)
        self.assertIn("--reseat", err)

    def test_existing_member_needs_resume(self):
        self.new_run()
        self.axp("dispatch", "--as", "critic", "--role", "critic", stdin="Review")
        code, _, err = self.axp("dispatch", "--as", "critic", "--role", "critic", stdin="Again")
        self.assertEqual(code, 2)
        self.assertIn("--resume", err)

    def test_role_defaults_codex_implementer_and_edit_required(self):
        self.new_run()
        code, _, err = self.axp("dispatch", "--as", "impl", "--role", "implementer", stdin="Do it")
        self.assertEqual(code, 2)
        self.assertIn("--edit", err)
        code, r, err = self.axp("dispatch", "--as", "impl", "--role", "implementer", "--edit", stdin="Do it")
        self.assertEqual(code, 0, err)
        self.assertEqual((r["engine"], r["model"]), ("codex", "gpt-6-sol"))
        self.assertTrue(self.fake.calls[-1]["edit"])

    def test_cross_picks_the_other_engine_and_adds_input(self):
        self.new_run()
        _, r, _ = self.axp("dispatch", "--as", "planner-a", "--role", "planner", "--engine", "codex",
                           "--model", "gpt-6-luna", "--effort", "high", stdin="Plan")
        art = r["artifacts"][0]["id"]
        code, r2, err = self.axp("dispatch", "--as", "reviewer", "--role", "reviewer", "--cross", art, stdin="Review")
        self.assertEqual(code, 0, err)
        self.assertEqual(r2["engine"], "claude")
        self.assertIn(art, self.fake.calls[-1]["brief"])

    def test_repair_then_success(self):
        run_id = self.new_run()
        bad = result()
        del bad["summary"]
        self.fake.queue = [{"result": bad, "handle": "s1"}, {"result": result(), "handle": "s1"}]
        code, r, err = self.axp("dispatch", "--as", "judge", "--role", "judge", stdin="Rule")
        self.assertEqual(code, 0, err)
        self.assertIn("did not match", self.fake.calls[-1]["brief"])
        self.assertEqual(self.fake.calls[-1]["handle"], "s1")
        self.assertIn("task.repaired", self.ledger_events(run_id))

    def test_failure_is_recorded_and_frees_the_member(self):
        run_id = self.new_run()
        self.fake.queue = [{"result": None, "handle": None, "error": "codex exited 1: auth"}]
        code, r, _ = self.axp("dispatch", "--as", "critic", "--role", "critic", stdin="Review")
        self.assertEqual((code, r["status"]), (1, "failed"))
        self.assertIn("task.failed", self.ledger_events(run_id))
        sessions = json.loads((self.run_dir(run_id) / "sessions.json").read_text())
        self.assertNotIn("critic", sessions)  # no session was created, so the name is free again

    def test_explicit_unsupported_effort_rejected_before_dispatch(self):
        self.new_run()
        code, _, err = self.axp("dispatch", "--as", "p", "--role", "planner", "--engine", "codex",
                                "--model", "gpt-6-luna", "--effort", "ultra", stdin="Plan")
        self.assertEqual(code, 2)
        self.assertIn("does not support", err)
        self.assertEqual(self.fake.calls, [])

    def test_brief_from_flag_and_file(self):
        run_id = self.new_run()
        code, r, err = self.axp("dispatch", "--as", "a", "--role", "planner", "--brief", "Plan from flag")
        self.assertEqual(code, 0, err)
        Path("brief.md").write_text("Plan from file")
        code, r2, err = self.axp("dispatch", "--as", "b", "--role", "planner", "--brief-file", "brief.md")
        self.assertEqual(code, 0, err)
        briefs = self.run_dir(run_id) / "briefs"
        self.assertTrue((briefs / f"{r['task']}.md").read_text().rstrip().endswith("Plan from flag"))
        self.assertTrue((briefs / f"{r2['task']}.md").read_text().rstrip().endswith("Plan from file"))
        code, rec, err = self.axp("record", "--kind", "analysis", "--title", "t", "--body", "body text")
        self.assertEqual(code, 0, err)

    def test_nested_dispatch_refused(self):
        self.new_run()
        with mock.patch.dict(os.environ, {"AXP_DEPTH": "1"}):
            code, _, err = self.axp("dispatch", "--as", "p", "--role", "planner", stdin="Plan")
        self.assertEqual(code, 2)
        self.assertIn("nested", err)


class TestRecordStatusSearch(StoreTest):
    def test_record_status_show_search(self):
        run_id = self.new_run()
        self.axp("dispatch", "--as", "critic", "--role", "critic", stdin="Review")
        arts = [json.loads(l) for l in (self.root / ".axp" / "index.jsonl").read_text().splitlines()]
        first = next(a for a in arts if a["type"] == "artifact")["id"]
        code, rec, err = self.axp("record", "--kind", "analysis", "--title", "Docket", "--from", first,
                                  stdin="| D1 | NAT |")
        self.assertEqual(code, 0, err)
        code, text, _ = self.axp("show", rec["id"])
        self.assertIn('"role": "orchestrator"', text)
        self.assertIn("| D1 | NAT |", text)
        code, s, _ = self.axp("status", "--json")
        self.assertEqual((s["artifacts"], s["unverified_findings"], len(s["tasks"])), (2, 1, 1))
        code, text, _ = self.axp("search", "nat users")
        self.assertIn("finding/risk high", text)
        code, _, err = self.axp("record", "--kind", "essay", "--title", "x", stdin="y")
        self.assertEqual(code, 2)


class TestRoles(unittest.TestCase):
    def test_frontmatter_subset(self):
        meta, body = axp.parse_frontmatter(
            "---\nname: x\n# comment\nlist: [a, \"b c\"]\nclaude:\n  model: opus\n  tools: [Read, Grep]\n---\nBody\n")
        self.assertEqual(meta, {"name": "x", "list": ["a", "b c"], "claude": {"model": "opus", "tools": ["Read", "Grep"]}})
        self.assertEqual(body, "Body\n")
        for bad in ("name: x\n", "---\nname: x\n", "---\n    deep: x\n---\n", "---\nnot a pair\n---\n"):
            with self.assertRaises(axp.AxpError):
                axp.parse_frontmatter(bad)

    def test_shipped_roles_are_valid(self):
        roles = axp.load_roles()
        self.assertTrue({"critic", "advocate", "judge", "implementer", "reviewer"} <= set(roles))
        self.assertEqual(roles["implementer"]["default_engine"], "codex")
        self.assertEqual(roles["implementer"]["codex"]["sandbox"], "workspace-write")
        self.assertEqual(roles["reviewer"]["default_engine"], "cross")
        self.assertIn("concede", roles["advocate"]["body"])

    def test_role_validation_errors(self):
        good = ("---\nname: r\ndescription: d\nproduces: [review]\ndefault_engine: codex\n"
                "claude:\n  model: opus\n  effort: high\ncodex:\n  model: gpt-6-sol\n  effort: high\n---\nbody\n")
        with tempfile.TemporaryDirectory() as d:
            Path(d, "r.md").write_text(good)
            self.assertIn("r", axp.load_roles(d))
            for old, new in (("name: r", "name: other"), ("default_engine: codex", "default_engine: both"),
                             ("produces: [review]", "produces: [essay]"), ("  effort: high\ncodex", "  effort: huge\ncodex")):
                Path(d, "r.md").write_text(good.replace(old, new, 1))
                with self.assertRaises(axp.AxpError, msg=new):
                    axp.load_roles(d)


class TestSyncRoles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.claude = Path(self.tmp.name) / "claude"
        self.codex = Path(self.tmp.name) / "codex"

    def tearDown(self):
        self.tmp.cleanup()

    def sync(self, *extra):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = axp.main(["sync-roles", "--claude-dir", str(self.claude), "--codex-dir", str(self.codex), *extra])
        return code, out.getvalue()

    def test_generates_valid_files_for_both_clis(self):
        import tomllib
        code, _ = self.sync()
        self.assertEqual(code, 0)
        n = len(axp.load_roles())
        self.assertEqual(len(list(self.claude.glob("axp-*.md"))), n + 1)
        self.assertEqual(len(list(self.codex.glob("axp-*.toml"))), n + 1)
        toml = tomllib.loads((self.codex / "axp-judge.toml").read_text())
        self.assertEqual((toml["name"], toml["model"], toml["model_reasoning_effort"], toml["sandbox_mode"]),
                         ("axp-judge", "gpt-6-astra", "low", "read-only"))
        self.assertIn("Rule only on the docket", toml["developer_instructions"])
        self.assertEqual(tomllib.loads((self.codex / "axp-implementer.toml").read_text())["sandbox_mode"],
                         "workspace-write")
        bridge = tomllib.loads((self.codex / "axp-claude-bridge.toml").read_text())
        self.assertIn("--engine claude", bridge["developer_instructions"])
        meta, body = axp.parse_frontmatter((self.claude / "axp-advocate.md").read_text())
        self.assertEqual((meta["name"], meta["model"], meta["effort"]), ("axp-advocate", "opus", "medium"))
        self.assertEqual(meta["tools"], "Read, Grep, Glob")
        self.assertIn("concede", body)

    def test_idempotent_check_update_and_stale_delete(self):
        self.sync()
        code, out = self.sync("--check")
        self.assertEqual((code, out.strip()), (0, "in sync"))
        (self.codex / "axp-judge.toml").write_text(f"# {axp.GENERATED_MARK}\nstale = true\n")
        (self.claude / "axp-retired.md").write_text(f"---\n# {axp.GENERATED_MARK}\n---\n")
        code, out = self.sync("--check")
        self.assertEqual(code, 1)
        self.assertIn("update", out)
        self.assertIn("delete", out)
        self.assertIn("stale = true", (self.codex / "axp-judge.toml").read_text())  # --check wrote nothing
        self.sync()
        self.assertFalse((self.claude / "axp-retired.md").exists())
        self.assertNotIn("stale", (self.codex / "axp-judge.toml").read_text())

    def test_never_overwrites_or_deletes_hand_written_files(self):
        self.claude.mkdir(parents=True)
        (self.claude / "axp-critic.md").write_text("my own agent\n")
        (self.claude / "axp-mine.md").write_text("another of mine\n")
        code, _ = self.sync()
        self.assertEqual(code, 2)
        self.assertEqual((self.claude / "axp-critic.md").read_text(), "my own agent\n")
        (self.claude / "axp-critic.md").unlink()
        self.sync()
        self.assertTrue((self.claude / "axp-mine.md").exists())


class TestRoleDispatch(StoreTest):
    def test_role_instructions_reach_the_worker(self):
        self.new_run()
        code, r, err = self.axp("dispatch", "--as", "judge", "--role", "judge", "--brief", "Rule")
        self.assertEqual(code, 0, err)
        self.assertEqual((r["engine"], r["model"], r["effort"]), ("codex", "gpt-6-astra", "low"))
        self.assertIn("Role instructions (judge):", self.fake.calls[-1]["brief"])
        self.assertIn("Rule only on the docket", self.fake.calls[-1]["brief"])

    def test_unknown_role_refused(self):
        self.new_run()
        code, _, err = self.axp("dispatch", "--as", "x", "--role", "poet", "--brief", "write")
        self.assertEqual(code, 2)
        self.assertIn("unknown role", err)

    def test_cross_default_picks_engine_other_than_host(self):
        self.new_run()
        _, r, _ = self.axp("dispatch", "--as", "rev", "--role", "reviewer", "--brief", "Review")
        self.assertEqual(r["engine"], "codex")  # AXP_HOST=claude in these tests


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------- Phase 4: panels

class FakePanel(FakeEngines):
    """Answers like a panel: plans for authors, a high-severity defect from the critic,
    no concessions from the advocate (so they disagree), rulings from the judge."""

    def __init__(self, critical=False, judge_confidence=0.9):
        super().__init__()
        self.critical = critical
        self.judge_confidence = judge_confidence
        self.docket_rows = 1

    def __call__(self, engine, **kw):
        self.calls.append({"engine": engine, **kw})
        role = re.search(r"Role: ([a-z]+)\.", kw["brief"]).group(1) if "Role: " in kw["brief"] else "?"
        stage = (re.search(r"Panel stage: ([a-z-]+)", kw["brief"]) or [None, "?"])[1]
        art = lambda kind, title: {"kind": kind, "title": title, "content": f"# {title}\n", "derived_from": []}
        finding = lambda kind, sev, conf=0.8: {"id": f"f{len(self.calls)}", "kind": kind, "severity": sev,
                                               "claim": f"{role} {kind}", "evidence": [], "confidence": conf}
        arts, finds = [art("review", f"{role} review")], []
        if role in ("planner", "architect"):
            arts = [art("plan", f"{role} plan")]
        elif role == "critic":
            finds = [finding("defect", "critical" if self.critical else "high")]
        elif role == "judge":
            arts = [art("decision", "Rulings")]
            finds = [finding("recommendation", "medium", self.judge_confidence)]
        elif role == "moderator":
            docket = art("analysis", "Docket")
            docket["content"] += "".join(f"| D{n} | issue |\n" for n in range(1, self.docket_rows + 1))
            arts = ([docket] if stage == "docket" else
                    [art("plan", "Final plan"), art("decision", "Decision record")])
        elif role == "implementer":
            arts = [art("output", "Changes")]
        elif role == "reviewer":
            finds = [finding("defect", "high")]
        r = result(artifacts=arts, findings=finds)
        r["task_id"] = "x"
        out = {"result": r, "handle": kw["handle"] or f"{engine}-s{len(self.calls)}"}
        return {"cost": {"usd": 0.01, "output_tokens": 10}, "seconds": 0.1, "error": None, "model_id": None, **out}


import re  # noqa: E402  (used by FakePanel)

INLINE = ('plan "Add rate limiting" authors: codex/gpt-6-luna@high, claude/haiku@high '
          'critic: codex/gpt-6.1-sol@medium advocate: claude/opus@medium judge: codex/gpt-6-astra@low rounds: 1')


class PanelTest(StoreTest):
    def setUp(self):
        super().setUp()
        self.patch.stop()
        self.fake = FakePanel()
        self.patch = mock.patch.object(axp, "run_engine", self.fake)
        self.patch.start()
        self.new_run()  # creates the store and caches the catalog

    def council(self, *argv):
        code, data, err = self.axp("council", *argv)
        self.assertEqual(code, 0, err)
        return data

    def stage_status(self, data):
        return {s["stage"]: s["status"] for s in data["stages"]}

    def events(self, data):
        return self.ledger_events(data["run"])


class TestTemplates(unittest.TestCase):
    def test_inline_plan_review(self):
        with mock.patch.dict(os.environ, {"AXP_HOST": "claude"}):
            spec = axp.expand_template(axp.parse_inline(INLINE))
        self.assertEqual(spec["members"]["planner-a"], {"role": "planner", "use": "codex/gpt-6-luna@high"})
        self.assertEqual(spec["members"]["judge"]["use"], "codex/gpt-6-astra@low")
        self.assertEqual([s["name"] for s in spec["stages"]],
                         ["draft", "debate", "rebuttal", "docket", "rule", "finalize", "approve"])
        self.assertTrue(spec["anonymize"])
        axp.validate_spec(spec)

    def test_pipeline_template(self):
        with mock.patch.dict(os.environ, {"AXP_HOST": "claude"}):
            spec = axp.expand_template(axp.parse_inline('build "Ship it"'))
        self.assertEqual([s["name"] for s in spec["stages"]],
                         ["plan", "plan-review", "final-plan", "approve-plan", "implement", "code-review",
                          "verify", "wrap-up"])
        self.assertTrue(next(s for s in spec["stages"] if s["name"] == "implement")["edit"])
        self.assertEqual(spec["members"]["implementer"]["use"].split("/")[0], "codex")

    def test_bad_inline_and_stage_refs(self):
        with self.assertRaises(axp.AxpError):
            axp.parse_inline('poem "x"')
        bad = {"members": {"a": {"role": "critic", "use": "codex/gpt-6.1-sol@low"}},
               "stages": [{"name": "s", "type": "review", "members": ["a"], "sees": ["later"]}]}
        with self.assertRaises(axp.AxpError):
            axp.validate_spec(bad)


class TestCouncilRun(PanelTest):
    def test_end_to_end_with_orchestrator_member(self):
        data = self.council("run", "--inline", INLINE + " orchestrator: claude/opus@high", "--on-checkpoint", "continue")
        self.assertEqual(self.stage_status(data), {"draft": "done", "debate": "done", "rebuttal": "done",
                                                   "docket": "done", "rule": "done", "finalize": "done",
                                                   "approve": "waiting"})
        self.assertEqual(data["checkpoint"]["kind"], "gate")
        debate_briefs = [c["brief"] for c in self.fake.calls if "Panel stage: debate" in c["brief"]]
        self.assertTrue(all('"Plan A"' in b and '"Plan B"' in b for b in debate_briefs))
        self.assertFalse(any("planner plan" in b for b in debate_briefs))  # anonymized copies only
        final = next(c["brief"] for c in self.fake.calls if "Panel stage: finalize" in c["brief"])
        self.assertIn("Plan A = planner-a (codex/gpt-6-luna@high)", final)
        rebut = [c for c in self.fake.calls if "Panel stage: rebuttal" in c["brief"]]
        self.assertEqual(len(rebut), 2)
        self.assertTrue(all(c["handle"] for c in rebut))  # resumed sessions
        data = self.council("resume", "--approve")
        self.assertEqual(data["status"], "done")
        self.assertEqual(data["run_status"], "closed")

    def test_host_orchestrated_submit_flow(self):
        data = self.council("run", "--inline", INLINE, "--on-checkpoint", "continue")
        self.assertEqual((data["checkpoint"]["stage"], data["checkpoint"]["kind"]), ("docket", "orchestrator"))
        code, _, err = self.axp("council", "resume", "--continue")
        self.assertEqual(code, 2)
        self.assertIn("submit", err)
        self.axp("council", "submit", "docket", "--kind", "analysis", "--title", "Docket", "--body", "| D1 |")
        data = self.council("resume", "--on-checkpoint", "continue")
        self.assertEqual(data["checkpoint"]["stage"], "finalize")
        self.axp("council", "submit", "finalize", "--kind", "plan", "--title", "Final", "--body", "plan", "--more")
        self.axp("council", "submit", "finalize", "--kind", "decision", "--title", "Decision", "--body", "dec")
        data = self.council("resume")
        self.assertEqual(data["checkpoint"]["kind"], "gate")
        self.assertEqual(len(next(s for s in data["stages"] if s["stage"] == "finalize")["outputs"]), 2)

    def test_signal_checkpoint_then_continue(self):
        self.fake.critical = True
        data = self.council("run", "--inline", INLINE + " orchestrator: claude/opus@high")
        cp = data["checkpoint"]
        self.assertEqual((cp["stage"], cp["kind"]), ("debate", "signal"))
        self.assertIn("critical_finding", cp["signals"])
        self.assertNotIn("disagreement", cp["signals"])  # the rebuttal stage handles that one
        data = self.council("resume", "--continue")
        self.assertEqual(data["checkpoint"]["stage"], "rebuttal")  # critical again in rebuttal

    def test_stop_and_after_each_stage(self):
        data = self.council("run", "--inline", INLINE + " checkpoints: after-each-stage orchestrator: claude/opus@high")
        self.assertEqual((data["checkpoint"]["stage"], data["checkpoint"]["kind"]), ("draft", "stage-end"))
        data = self.council("resume", "--stop")
        self.assertEqual(data["status"], "stopped")

    def test_low_confidence_judge(self):
        self.fake.judge_confidence = 0.3
        data = self.council("run", "--inline", INLINE + " orchestrator: claude/opus@high checkpoints: on-signal",
                            "--on-checkpoint", "pause")
        while data["checkpoint"]["stage"] != "rule":
            data = self.council("resume", "--continue")
        self.assertIn("low_confidence", data["checkpoint"]["signals"])

    def test_extra_round(self):
        data = self.council("run", "--inline", INLINE + " checkpoints: after-each-stage orchestrator: claude/opus@high")
        while data["checkpoint"]["stage"] != "rebuttal":
            data = self.council("resume", "--continue")
        before = len([c for c in self.fake.calls if "Panel stage: rebuttal" in c["brief"]])
        data = self.council("resume", "--extra-round", "rebuttal")
        after = [c for c in self.fake.calls if "Panel stage: rebuttal" in c["brief"]]
        self.assertEqual(len(after) - before, 2)
        self.assertIn("Round 2", after[-1]["brief"])


class TestAmendRedoCancel(PanelTest):
    def run_to(self, stage, extra=""):
        data = self.council("run", "--inline", INLINE + " checkpoints: after-each-stage orchestrator: claude/opus@high" + extra)
        while data["checkpoint"]["stage"] != stage:
            data = self.council("resume", "--continue")
        return data

    def test_amend_swaps_judge_before_it_runs_and_refuses_started_stages(self):
        data = self.run_to("debate")
        code, out, err = self.axp("panel", "amend", "--set", "judge=claude/opus@high",
                                  "--add-member", "security=reviewer:codex/gpt-6.1-sol@high",
                                  "--add-stage", "security-review type=review after=debate members=security sees=draft,debate",
                                  "--reason", "critical finding")
        self.assertEqual(code, 0, err)
        self.assertIn("panel.amended", self.events(data))
        code, _, err = self.axp("panel", "amend", "--skip", "debate", "--reason", "x")
        self.assertEqual(code, 2)
        self.assertIn("already run", err)
        code, _, err = self.axp("panel", "amend", "--add-stage", "early type=review after=draft members=critic", "--reason", "x")
        self.assertEqual(code, 2)
        data = self.council("resume", "--continue", "--on-checkpoint", "continue")
        judge_call = next(c for c in self.fake.calls if "Panel stage: rule" in c["brief"])
        self.assertEqual(judge_call["engine"], "claude")
        self.assertIn("security-review", self.stage_status(data))
        self.assertEqual(self.stage_status(data)["security-review"], "done")

    def test_redo_supersedes_marks_stale_and_reseats(self):
        data = self.run_to("rule")
        old = next(s for s in data["stages"] if s["stage"] == "debate")["outputs"]
        code, r, err = self.axp("stage", "redo", "debate", "--with", "critic=claude/opus@high")
        self.assertEqual(code, 0, err)
        self.assertEqual(r["stale"], ["rebuttal", "docket", "rule", "finalize"])
        index = axp.Store.find().read_index()
        self.assertTrue(all(index[o].get("status") == "superseded" for o in old))
        data = self.council("status")
        self.assertEqual(self.stage_status(data)["debate"], "pending")
        self.assertEqual(self.stage_status(data)["docket"], "stale")
        data = self.council("resume", "--on-checkpoint", "continue")
        self.assertEqual(data["checkpoint"]["kind"], "stale")
        self.assertIn("member.reseated", self.events(data))
        critic = json.loads((self.run_dir(data["run"]) / "sessions.json").read_text())["critic"]
        self.assertEqual((critic["engine"], critic["model"]), ("claude", "opus"))
        self.assertEqual(critic["history"][0]["engine"], "codex")
        data = self.council("resume", "--keep-stale")
        self.assertNotEqual(data["checkpoint"], None)

    def test_cancel_pending_stage_skips_it(self):
        data = self.run_to("debate")
        code, r, err = self.axp("stage", "cancel", "rebuttal")
        self.assertEqual((code, r["now"]), (0, "skipped"), err)
        data = self.council("resume", "--continue")
        self.assertEqual(self.stage_status(data)["rebuttal"], "skipped")

    def test_cancel_running_stage_kills_workers(self):
        import subprocess as sp
        data = self.run_to("draft")
        run_id = data["run"]
        proc = sp.Popen(["sleep", "30"], start_new_session=True)
        raw = self.run_dir(run_id) / "raw"
        (raw / "t_fake.pid").write_text(str(proc.pid))
        store = axp.Store.find()
        with store.lock():
            store.ledger(run_id, "task.dispatched", "test", "t_fake", stage="debate", member="critic")
        axp.mutate_state(store, run_id, lambda s: s["stages"].__setitem__("debate", {"status": "running"}))
        code, r, err = self.axp("stage", "cancel", "debate")
        self.assertEqual(code, 0, err)
        self.assertEqual(r["stopped_tasks"], ["t_fake"])
        self.assertEqual(proc.wait(timeout=5), -15)
        self.assertTrue((raw / "t_fake.cancelled").exists())
        axp.mutate_state(store, run_id, lambda s: s.update(checkpoint=None, status="running"))
        data = self.council("resume")
        self.assertEqual(data["checkpoint"]["kind"], "cancelled")


class TestReseatAndChildren(PanelTest):
    def test_dispatch_reseat_seeds_prior_work(self):
        self.axp("dispatch", "--as", "critic", "--role", "critic", "--brief", "Review")
        code, r, err = self.axp("dispatch", "--as", "critic", "--reseat", "--engine", "claude", "--model", "opus",
                                "--effort", "high", "--brief", "Continue")
        self.assertEqual(code, 0, err)
        call = self.fake.calls[-1]
        self.assertIsNone(call["handle"])
        self.assertIn("taking over the seat of member critic", call["brief"])
        prior = axp.member_artifacts(axp.Store.find(), self.fake.calls[0]["env"]["AXP_RUN"], "critic")[0]
        self.assertIn(prior, call["brief"])
        sessions = json.loads((self.root / ".axp" / "runs" / call["env"]["AXP_RUN"] / "sessions.json").read_text())
        self.assertEqual(sessions["critic"]["history"][0]["engine"], "codex")
        code, _, err = self.axp("dispatch", "--as", "critic", "--reseat", "--role", "judge", "--brief", "x")
        self.assertEqual(code, 2)

    def test_parent_waits_for_child_results(self):
        parent = self.council("run", "--inline", INLINE + " checkpoints: after-each-stage orchestrator: claude/opus@high")
        pid = parent["run"]
        code, child, err = self.axp("run", "new", "Council on D2", "--parent", pid, "--wait", "--no-switch")
        self.assertEqual(code, 0, err)
        data = self.council("resume", "--continue", "--run", pid)
        self.assertEqual(data["checkpoint"]["kind"], "waiting-child")
        store = axp.Store.find()
        with store.lock():
            art, rec = axp.write_artifact(store, child["run"], kind="decision", title="D2 decision", content="x",
                                          derived_from=[], author={"engine": "claude", "member": "orchestrator"})
            store.index(rec)
        code, closed, err = self.axp("run", "close", "--run", child["run"], "--result", art)
        self.assertEqual(code, 0, err)
        data = self.council("status", "--run", pid)
        self.assertIsNone(data["checkpoint"])
        self.assertEqual(data["child_results"], [art])
        data = self.council("resume", "--run", pid)
        nxt = self.fake.calls[-1]["brief"]
        self.assertIn(art, nxt)


# ---------------------------------------------------------------- Phase 5: on: rules, verify, routing

class TestRuleParsing(unittest.TestCase):
    def test_conditions(self):
        self.assertEqual(axp.parse_condition("docket.empty"), ("docket.empty", "==", True))
        self.assertEqual(axp.parse_condition("judge.confidence < 0.6"), ("judge.confidence", "<", 0.6))
        self.assertEqual(axp.parse_condition("budget.used >= 80%"), ("budget.used", ">=", 80.0))
        self.assertEqual(axp.parse_condition("stage.debate.status == done"), ("stage.debate.status", "==", "done"))
        for bad in ("os.system > 1", "findings.critical", "findings.high > lots", "stage.x.status > done",
                    "__import__('os')"):
            with self.assertRaises(axp.AxpError, msg=bad):
                axp.parse_condition(bad)

    def test_rule_validation(self):
        base = {"members": {"c": {"role": "critic", "use": "codex/gpt-6.1-sol@medium"}},
                "stages": [{"name": "s", "type": "review", "members": ["c"], "sees": ["brief"]}]}
        for rules in ([{"if": "docket.empty", "then": {"explode": True}}],
                      [{"if": "docket.empty"}],
                      [{"if": "findings.high > 0", "then": {"add_stage": "fix type=implement members=c"}}],
                      [{"if": "findings.high > 0", "then": {"add_stage": "x type=review members=c edit"}}],
                      [{"if": "findings.high > 0", "then": {"set": {"c": "codex/gpt-6.1-sol"}}}]):
            with self.assertRaises(axp.AxpError, msg=str(rules)):
                axp.validate_spec({**base, "on": rules})
        axp.validate_spec({**base, "on": [{"if": "findings.high > 0", "then": {"checkpoint": True}, "repeat": True}]})


class TestRulesInPanels(PanelTest):
    def spec_file(self, rules, extra=""):
        with mock.patch.dict(os.environ, {"AXP_HOST": "claude"}):
            spec = axp.expand_template(axp.parse_inline(INLINE + " orchestrator: claude/opus@high" + extra))
        spec["on"] = rules
        Path("spec.json").write_text(json.dumps(spec))
        return "spec.json"

    def test_low_confidence_adds_an_appeal(self):
        self.fake.judge_confidence = 0.3
        f = self.spec_file([{"if": "judge.confidence < 0.6", "then": {
            "add_member": {"judge-2": {"role": "judge", "use": "claude/opus@high"}},
            "add_stage": "appeal type=rule after=rule members=judge-2 sees=docket,rule"}}])
        data = self.council("run", "--spec", f, "--on-checkpoint", "continue")
        status = self.stage_status(data)
        self.assertEqual(status["appeal"], "done")
        self.assertEqual([s["stage"] for s in data["stages"]][4:6], ["rule", "appeal"])
        appeal_call = next(c for c in self.fake.calls if "Panel stage: appeal" in c["brief"])
        self.assertEqual(appeal_call["engine"], "claude")
        events = self.events(data)
        self.assertEqual(events.count("rule.fired"), 1)
        ledger = [json.loads(l) for l in (self.run_dir(data["run"]) / "ledger.jsonl").read_text().splitlines()]
        amended = [e for e in ledger if e["event"] == "panel.amended"]
        self.assertEqual(amended[0]["actor"], "rule[0]")

    def test_checkpoint_rule_and_fire_once(self):
        f = self.spec_file([{"if": "findings.high > 0", "then": {"checkpoint": True}}])
        data = self.council("run", "--spec", f)
        self.assertEqual(data["checkpoint"]["kind"], "rule")
        self.assertIn("rule[0]: findings.high > 0", data["checkpoint"]["signals"])
        data = self.council("resume", "--continue", "--on-checkpoint", "continue")
        self.assertEqual(self.events(data).count("rule.fired"), 1)  # not again on later high findings
        self.assertEqual(data["checkpoint"]["kind"], "gate")

    def test_repeat_rule_fires_at_each_boundary(self):
        f = self.spec_file([{"if": "findings.high > 0", "then": {"checkpoint": True}, "repeat": True}])
        data = self.council("run", "--spec", f, "--on-checkpoint", "continue")
        self.assertGreaterEqual(self.events(data).count("rule.fired"), 2)

    def test_docket_empty_skips_judging(self):
        self.fake.docket_rows = 0
        f = self.spec_file([{"if": "docket.empty", "then": {"skip": ["rule"]}},
                            {"if": "stage.finalize.status == done", "then": {"stop": True}}])
        data = self.council("run", "--spec", f, "--on-checkpoint", "continue")
        status = self.stage_status(data)
        self.assertEqual(status["rule"], "skipped")
        self.assertEqual(data["status"], "stopped")
        self.assertFalse(any("Panel stage: rule" in c["brief"] for c in self.fake.calls))

    def test_docket_with_rows_is_not_empty(self):
        self.fake.docket_rows = 2
        f = self.spec_file([{"if": "docket.size >= 2", "then": {"checkpoint": True}}])
        data = self.council("run", "--spec", f, "--on-checkpoint", "pause")
        while data["checkpoint"]["kind"] != "rule":
            data = self.council("resume", "--continue")
        self.assertEqual(data["checkpoint"]["stage"], "docket")

    def test_rule_error_pauses(self):
        f = self.spec_file([{"if": "stage.draft.status == done", "then": {"redo": "nonexistent"}}])
        data = self.council("run", "--spec", f, "--on-checkpoint", "continue")
        self.assertEqual(data["checkpoint"]["kind"], "rule-error")
        self.assertIn("rule.failed", self.events(data))


class TestVerifyAndRouting(PanelTest):
    def test_verify_updates_index_and_ledger(self):
        _, r, _ = self.axp("dispatch", "--as", "critic", "--role", "critic", "--brief", "Review")
        fid = r["findings"][0]["id"]
        code, out, err = self.axp("verify", fid, "--state", "refuted", "--note", "the code handles it")
        self.assertEqual(code, 0, err)
        rec = axp.Store.find().read_index()[fid]
        self.assertEqual((rec["verification"], rec["verify_note"]), ("refuted", "the code handles it"))
        self.assertIn("finding.refuted", self.ledger_events(r["run"]))
        code, s, _ = self.axp("status", "--json")
        self.assertEqual(s["unverified_findings"], 0)

    def seed(self, member, model, effort, n, verdicts=()):
        for i in range(n):
            _, r, _ = self.axp("dispatch", "--as", f"{member}-{i}", "--role", "reviewer", "--engine", "codex",
                               "--model", model, "--effort", effort, "--brief", "Review")
            for f, v in zip(r["findings"], verdicts[i:i + 1]):
                self.axp("verify", f["id"], "--state", v)

    def test_report_and_suggest_apply(self):
        with tempfile.TemporaryDirectory() as roles_dir:
            for f in axp.ROLES_DIR.glob("*.md"):
                Path(roles_dir, f.name).write_text(f.read_text())
            with mock.patch.object(axp, "ROLES_DIR", Path(roles_dir)):
                self.seed("cheap", "gpt-6-luna", "low", 3)
                self.seed("strong", "gpt-6.1-sol", "high", 3)
                rows = axp.routing_rows([axp.Store.find()], "reviewer")
                self.assertEqual({(r["model"], r["tasks"]) for r in rows}, {("gpt-6-luna", 3), ("gpt-6.1-sol", 3)})
                code, text, _ = self.axp("routing", "report", "--role", "reviewer")
                self.assertIn("codex/gpt-6-luna@low", text)
                # Without judged findings, both candidates tie on success and cost.
                sugg = axp.routing_suggestions(rows, axp.load_roles(), 3, 5)
                rev = next(s for s in sugg if s["role"] == "reviewer" and s["engine"] == "codex")
                self.assertEqual(rev["current"], "gpt-6.1-sol@high")
                self.assertEqual(rev["candidates"], 2)
                # With judged findings, precision outranks everything.
                for r in rows:
                    if r["model"] == "gpt-6-luna":
                        r.update(verified=6, refuted=0, precision=1.0)
                    else:
                        r.update(verified=2, refuted=4, precision=0.333)
                rev = next(s for s in axp.routing_suggestions(rows, axp.load_roles(), 3, 5)
                           if s["role"] == "reviewer" and s["engine"] == "codex")
                self.assertEqual((rev["suggested"], rev["change"], rev["basis"]), ("gpt-6-luna@low", True, "precision"))
                # An untested default is never replaced just because something else ran.
                only_cheap = [r for r in rows if r["model"] == "gpt-6-luna"]
                rev = next(s for s in axp.routing_suggestions(only_cheap, axp.load_roles(), 3, 5)
                           if s["role"] == "reviewer" and s["engine"] == "codex")
                self.assertEqual((rev["change"], rev["basis"]), (False, "no data for the current default"))
                axp.set_role_engine(Path(roles_dir, "reviewer.md"), "codex", "gpt-6-luna", "low")
                role = axp.load_roles(roles_dir)["reviewer"]
                self.assertEqual((role["codex"]["model"], role["codex"]["effort"]), ("gpt-6-luna", "low"))
                self.assertEqual(role["claude"]["model"], "opus")  # the other engine is untouched


class TestInstall(unittest.TestCase):
    def test_install_renders_paths_and_uninstalls_only_its_own_files(self):
        with tempfile.TemporaryDirectory() as d:
            claude, codex = Path(d, "claude"), Path(d, "codex")
            (codex / "skills" / "mine").mkdir(parents=True)
            (codex / "skills" / "mine" / "SKILL.md").write_text("---\nname: mine\n---\n")
            run = lambda *extra: axp.main(["install", "--claude-dir", str(claude), "--codex-dir", str(codex), *extra])
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(run("--codex-rules"), 0)
            skill = (claude / "skills" / "axp-review" / "SKILL.md").read_text()
            self.assertNotIn("__AXP_HOME__", skill)
            self.assertIn(str(AXP_PATH), skill)
            self.assertTrue(skill.startswith("---\n# " + axp.INSTALL_MARK))
            axp.parse_frontmatter(skill)
            rules = (codex / "rules" / "default.rules").read_text()
            self.assertIn(f'prefix_rule(pattern=["{AXP_PATH}", "council"], decision="allow")', rules)
            self.assertTrue((codex / "agents" / "axp-judge.toml").exists())
            (claude / "skills" / "axp-status" / "SKILL.md").write_text("---\nname: mine-now\n---\n")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(run(), 2)  # a skill it didn't render is never overwritten
            (claude / "skills" / "axp-status" / "SKILL.md").unlink()
            (claude / "skills" / "axp-status").rmdir()
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(run("--uninstall"), 0)
            self.assertEqual(sorted(p.name for p in (codex / "skills").iterdir()), ["mine"])
            self.assertEqual(list((claude / "agents").glob("axp-*")), [])
            self.assertNotIn("axp", (codex / "rules" / "default.rules").read_text())


# ---------------------------------------------------------------- fixes from the gpt-6-luna review

class TestWorkerStopsItself(unittest.TestCase):
    def test_worker_killed_when_cancel_marker_exists_after_pid_write(self):
        with tempfile.TemporaryDirectory() as d:
            marker = Path(d, "stage-debate.cancelled")
            marker.touch()
            with mock.patch.object(axp, "codex_command", lambda *a, **k: ["sleep", "30"]):
                started = time.time()
                out = axp.run_engine("codex", model="m", effort="low", edit=False, worktree=False, handle=None,
                                     brief="", raw_dir=Path(d), task_id="t_1", env=dict(os.environ), cwd=d,
                                     timeout=60, max_usd=None, cancel_files=[marker])
            self.assertLess(time.time() - started, 10)
            self.assertEqual(out["error"], "worker stopped by signal 15")
            self.assertFalse(Path(d, "t_1.pid").exists())


class TestCancelRace(PanelTest):
    def start(self, extra=""):
        data = self.council("run", "--inline", INLINE + " checkpoints: after-each-stage orchestrator: claude/opus@high" + extra)
        return data, axp.Store.find(), self.run_dir(data["run"])

    def test_cancel_marks_tasks_that_have_no_pid_yet(self):
        data, store, d = self.start()
        with store.lock():
            store.ledger(data["run"], "task.dispatched", "test", "t_nopid", stage="debate", member="critic")
        axp.mutate_state(store, data["run"], lambda s: s["stages"].__setitem__("debate", {"status": "running"}))
        code, r, err = self.axp("stage", "cancel", "debate")
        self.assertEqual(code, 0, err)
        self.assertEqual((r["stopped_tasks"], (d / "raw" / "t_nopid.cancelled").exists()), ([], True))
        self.assertTrue(axp.stage_cancel_marker(d, "debate").exists())
        code, r, err = self.axp("stage", "redo", "debate")
        self.assertEqual(code, 0, err)
        self.assertFalse(axp.stage_cancel_marker(d, "debate").exists())  # a redo starts clean

    def test_dispatch_refuses_to_start_a_worker_in_a_cancelled_stage(self):
        data, store, d = self.start()
        axp.stage_cancel_marker(d, "debate").touch()
        calls = len(self.fake.calls)
        code, s = axp.dispatch(store, data["run"], member="late", role="critic", brief="Review", stage="debate")
        self.assertEqual((code, s["status"], s["error"]), (1, "failed", "cancelled"))
        self.assertEqual(len(self.fake.calls), calls)  # no engine was started

    def test_cancelled_rebuttal_starts_no_more_rounds(self):
        fake = self.fake
        original = FakePanel.__call__

        def cancel_in_round_one(engine, **kw):
            out = original(fake, engine, **kw)
            if "Panel stage: rebuttal" in kw["brief"] and "Round 1." in kw["brief"]:
                store = axp.Store.find()
                run_id = kw["env"]["AXP_RUN"]
                axp.mutate_state(store, run_id, lambda s: s["stages"]["rebuttal"].update(status="cancelled"))
                axp.stage_cancel_marker(store.run_dir(run_id), "rebuttal").touch()
            return out

        self.patch.stop()
        self.patch = mock.patch.object(axp, "run_engine", cancel_in_round_one)
        self.patch.start()
        data = self.council("run", "--inline", INLINE + " rounds: 3 checkpoints: none orchestrator: claude/opus@high")
        rebut = [c for c in fake.calls if "Panel stage: rebuttal" in c["brief"]]
        self.assertEqual({re.search(r"Round (\d+)", c["brief"]).group(1) for c in rebut}, {"1"})
        self.assertEqual(data["checkpoint"]["kind"], "cancelled")


class TestChildClose(PanelTest):
    def parent_and_child(self):
        parent = self.council("run", "--inline", INLINE + " checkpoints: after-each-stage orchestrator: claude/opus@high")
        code, child, err = self.axp("run", "new", "child", "--parent", parent["run"], "--wait", "--no-switch")
        self.assertEqual(code, 0, err)
        store = axp.Store.find()
        with store.lock():
            art, rec = axp.write_artifact(store, child["run"], kind="decision", title="D", content="x",
                                          derived_from=[], author={"engine": "claude", "member": "orchestrator"})
            store.index(rec)
        return parent["run"], child["run"], art

    def test_parent_failure_leaves_child_open_and_retry_delivers(self):
        pid, cid, art = self.parent_and_child()
        state_file = self.run_dir(pid) / "council.json"
        good = state_file.read_text()
        state_file.write_text("{not json")
        code, _, err = self.axp("run", "close", "--run", cid, "--result", art)
        self.assertEqual(code, 2)
        self.assertIn("unreadable panel state", err)
        self.assertEqual(json.loads((self.run_dir(cid) / "run.json").read_text())["status"], "open")
        state_file.write_text(good)
        code, r, err = self.axp("run", "close", "--run", cid, "--result", art)
        self.assertEqual(code, 0, err)
        ps = json.loads(state_file.read_text())
        self.assertEqual((ps["waiting_on"], ps["child_results"]), ([], [art]))

    def test_closed_child_redelivers_to_a_parent_still_waiting(self):
        pid, cid, art = self.parent_and_child()
        run_file = self.run_dir(cid) / "run.json"
        run = json.loads(run_file.read_text())
        run.update(status="closed", results=[art])  # closed by the old code, parent never told
        run_file.write_text(json.dumps(run))
        code, r, err = self.axp("run", "close", "--run", cid)
        self.assertEqual((code, r["redelivered"]), (0, True), err)
        self.assertEqual(json.loads((self.run_dir(pid) / "council.json").read_text())["child_results"], [art])
        code, r, _ = self.axp("run", "close", "--run", cid)
        self.assertFalse(r["redelivered"])  # idempotent
