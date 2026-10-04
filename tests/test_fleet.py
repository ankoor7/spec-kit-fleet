"""End-to-end tests of fleet.py against a throwaway bare remote.

Run: python3 -m unittest discover -s tests -v
Needs git and python3 only. No agent is started: the local transport's agent
command is replaced by a small Python one-liner that records its prompt.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXT = HERE.parent
FLEET_PY = EXT / "scripts" / "python" / "fleet.py"
sys.path.insert(0, str(FLEET_PY.parent))
import fleet  # noqa: E402

ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
           GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com",
           GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
ENV.pop("SPECKIT_FLEET_TRANSPORT", None)
ENV.pop("SPECKIT_FLEET_SESSION_ID", None)


def sh(*args, cwd, check=True, env=None):
    p = subprocess.run(list(args), cwd=cwd, env=env or ENV, text=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and p.returncode != 0:
        raise AssertionError(f"{args} -> {p.returncode}\n{p.stdout}\n{p.stderr}")
    return p


class FleetTest(unittest.TestCase):
    transport = "local"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fleet-test-"))
        self.origin = self.tmp / "origin.git"
        sh("git", "init", "-q", "--bare", "-b", "main", str(self.origin), cwd=self.tmp)
        self.repo = self.tmp / "app"
        sh("git", "clone", "-q", str(self.origin), str(self.repo), cwd=self.tmp)
        feat = self.repo / "specs" / "001-albums"
        feat.mkdir(parents=True)
        shutil.copy(HERE / "fixtures" / "tasks.md", feat / "tasks.md")
        (feat / "spec.md").write_text("# Spec\n")
        (self.repo / "db" / "migrations").mkdir(parents=True)
        (self.repo / "db" / "migrations" / "0007_init.sql").write_text("--\n")
        ext = self.repo / ".specify" / "extensions" / "fleet"
        shutil.copytree(EXT / "templates", ext / "templates")
        shutil.copytree(EXT / "commands", ext / "commands")
        (ext / "fleet-config.yml").write_text(f"""
transport: {self.transport}
tasks_per_item: 2
reserved_sequences:
  migrations: "db/migrations/*.sql"
reserve_block: 3
gate:
  quick: ["true"]
  full: []
local:
  agent_command: "{sys.executable} -c 'import sys,os; open(\\"prompt.txt\\",\\"w\\").write(os.environ[\\"SPECKIT_FLEET_SESSION_ID\\"]+chr(10)+sys.argv[1])'"
  worktrees_dir: "../wt"
""")
        sh("git", "add", "-A", cwd=self.repo)
        sh("git", "commit", "-qm", "init", cwd=self.repo)
        sh("git", "push", "-q", "origin", "main", cwd=self.repo)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def f(self, *args, check=True, env=None):
        return sh(sys.executable, str(FLEET_PY), *args, cwd=self.repo, check=check, env=env)

    def publish(self):
        self.f("plan", "albums", "specs/001-albums", "--out", str(self.tmp / "draft"))
        orig = "session_abc123" if self.transport == "cloud" else "local_orig1"
        self.f("publish", "albums", "--from", str(self.tmp / "draft"), "--originating", orig)

    def queue(self, chain):
        out = self.f("get", "albums", f"{chain}.queue.jsonl").stdout
        return [json.loads(l) for l in out.splitlines() if l.strip()]


class PlanTest(FleetTest):
    def test_parse_and_partition(self):
        phases = fleet.parse_tasks(HERE / "fixtures" / "tasks.md")
        self.assertEqual([p["kind"] for p in phases], ["setup", "foundational", "story", "story", "polish"])
        self.assertTrue(phases[0]["tasks"][0]["done"])
        self.assertIn("src/services/albums.py", phases[3]["tasks"][3]["paths"])

        out = self.f("plan", "albums", "specs/001-albums", "--out", str(self.tmp / "draft")).stdout
        d = self.tmp / "draft" / ".fleet" / "albums"
        m = json.loads((d / "manifest.json").read_text())
        self.assertEqual(m["merge_order"], ["foundation", "us1", "us2", "polish"], out)
        found = [json.loads(l) for l in (d / "foundation.queue.jsonl").read_text().splitlines()]
        # T001 is done, so the foundation chain has T002-T004 in two items of 2.
        self.assertEqual([i["id"] for i in found],
                         ["foundation-1", "foundation-2", "foundation-integrate", "foundation-register"])
        self.assertEqual(found[0]["tasks"], ["T002", "T003"])
        us1 = [json.loads(l) for l in (d / "us1.queue.jsonl").read_text().splitlines()]
        self.assertEqual(us1[0]["needs"], ["foundation/foundation-1", "foundation/foundation-2"])
        pol = [json.loads(l) for l in (d / "polish.queue.jsonl").read_text().splitlines()]
        self.assertIn("us2/us2-2", pol[0]["needs"])
        # Migrations: highest is 0007, blocks of 3 per chain from 8.
        self.assertEqual(m["chains"][0]["reserved"]["migrations"], [8, 10])
        self.assertEqual(m["chains"][1]["reserved"]["migrations"], [11, 13])

    def test_cloud_cap_widens_items(self):
        cfg = dict(fleet.DEFAULTS, transport="cloud", tasks_per_item=1, max_items_per_chain=4)
        cfg["branches"] = fleet.DEFAULTS["branches"]
        m, q = fleet.plan_fleet("x", [self.repo / "specs" / "001-albums"], cfg, self.repo)
        self.assertTrue(all(len(v) <= 4 for v in q.values()), {k: len(v) for k, v in q.items()})
        self.assertEqual(len(q["us2"]), 4)  # 4 tasks -> 2 work items of 2, + integrate, register

    def test_yaml_subset(self):
        text = 'a: 1\nb:\n  c: "x # y"  # comment\n  d: [p, "q"]\nl:\n  - one\n  - 2\ne: {}\n'
        orig = sys.modules.get("yaml")
        sys.modules["yaml"] = None  # force the fallback parser
        try:
            self.assertEqual(fleet.parse_yaml(text),
                             {"a": 1, "b": {"c": "x # y", "d": ["p", "q"]}, "l": ["one", 2], "e": {}})
        finally:
            if orig is None:
                sys.modules.pop("yaml", None)
            else:
                sys.modules["yaml"] = orig


class LifecycleTest(FleetTest):
    def test_publish_claim_wait_share_close(self):
        self.publish()
        heads = sh("git", "ls-remote", "--heads", str(self.origin), cwd=self.tmp).stdout
        for b in ("claude/fleet", "claude/fleet-record", "claude/fleet-albums-us1"):
            self.assertIn(f"refs/heads/{b}\n", heads + "\n")
        # The status branch is an orphan: it shares no history with main.
        self.f("check", "albums")
        sh("git", "fetch", "-q", "origin", cwd=self.repo)
        base = sh("git", "merge-base", "origin/main", "origin/claude/fleet-record", cwd=self.repo, check=False)
        self.assertNotEqual(base.returncode, 0)

        # us1's first item waits for the foundation: claim closes it as a wait.
        r = self.f("claim", "albums", "us1", "--session", "local_s1", check=False)
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertEqual(self.queue("us1")[0]["status"], "blocked")
        self.assertIn("waits for foundation/foundation-1", self.queue("us1")[0]["note"])
        r = self.f("launch", "albums", "us1", check=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("blocked, not pending", r.stderr)

        # Foundation runs both work items and records its commits.
        sha = sh("git", "rev-parse", "HEAD", cwd=self.repo).stdout.strip()
        for item in ("foundation-1", "foundation-2"):
            got = json.loads(self.f("claim", "albums", "foundation", "--session", "local_s2").stdout)
            self.assertEqual(got["id"], item)
            r = self.f("claim", "albums", "foundation", "--session", "local_s3", check=False)
            self.assertEqual(r.returncode, 1)
            self.assertIn("running", r.stderr)
            self.f("close", "albums", "foundation", item, "--status", "done", "--commits", sha)

        # The wait ends: set pending, and launch is allowed.
        self.f("set", "albums", "us1", "us1-1", "--status", "pending", "--note", "needs done")
        got = json.loads(self.f("claim", "albums", "us1", "--session", "local_s4").stdout)
        self.assertEqual(got["id"], "us1-1")
        self.assertNotIn("finished_at", got)
        picks = json.loads(self.f("picks", "albums", "us1", "us1-1").stdout)
        self.assertEqual(picks[0]["commits"], [])  # already an ancestor of HEAD
        self.f("record-pick", "albums", "us1", "us1-1", "--from", "foundation/foundation-1", "--commits", sha)
        self.assertEqual(self.queue("us1")[0]["picked"][0]["commits"], [sha])

    def test_refusals(self):
        self.publish()
        r = self.f("claim", "albums", "foundation", "--session", "2e9a1f0c-b", check=False)
        self.assertIn("not a session id", r.stderr)
        r = self.f("close", "albums", "foundation", "foundation-1", "--status", "done",
                   "--commits", "not-a-sha", "--force", check=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("commits must be commit SHAs", r.stderr)
        # Nothing was written by the refused close.
        self.assertEqual(self.queue("foundation")[0]["status"], "pending")
        r = self.f("publish", "albums", "--from", str(self.tmp / "draft"), "--originating", "local_x", check=False)
        self.assertIn("already exists", r.stderr)

    def test_conflict_stops_claims(self):
        self.publish()
        self.f("conflict", "albums", "--data", json.dumps({
            "class": "contradiction", "status": "open", "found_by": "us2",
            "chains": ["us1", "us2"], "title": "share visibility"}))
        rows = json.loads(self.f("conflicts", "albums", "--open").stdout)
        self.assertEqual(rows[0]["id"], "c-us2-1")
        r = self.f("claim", "albums", "us1", "--session", "local_s1", check=False)
        self.assertIn("open conflict c-us2-1", r.stderr)
        self.f("conflict", "albums", "--data", json.dumps({
            "id": "c-us2-1", "class": "contradiction", "status": "resolved", "decision": "x"}))
        self.assertEqual(json.loads(self.f("conflicts", "albums", "--open").stdout), [])
        r = self.f("conflict", "albums", "--data", json.dumps({"class": "nope", "status": "open"}), check=False)
        self.assertEqual(r.returncode, 1)

    def test_hold_put_get_state(self):
        self.publish()
        self.f("hold", "albums", "--data", json.dumps({
            "status": "open", "title": "half a migration", "opened_by": {"chain": "us1"}}))
        p = self.tmp / "h.md"
        p.write_text("# handoff\n")
        self.f("put", "albums", "HANDOFF-us1.md", "--file", str(p))
        self.assertEqual(self.f("get", "albums", "HANDOFF-us1.md").stdout, "# handoff\n")
        r = self.f("put", "albums", "../escape.md", "--file", str(p), check=False)
        self.assertEqual(r.returncode, 1)
        st = json.loads(self.f("state", "albums", "--json").stdout)
        self.assertEqual(st["holds"][0]["id"], "h-us1-1")
        self.assertEqual(st["wrap_up"], "not yet run")
        self.assertEqual(st["chains"][0]["next"]["id"], "foundation-1")
        text = self.f("state", "albums").stdout
        self.assertIn("foundation: 0/4 done; next: foundation-1 [pending]", text)

    def test_local_launch_message_inbox_alive(self):
        self.publish()
        rec = json.loads(self.f("launch", "albums", "foundation", "--first").stdout)
        self.assertTrue(rec["session_id"].startswith("local_"))
        wt = Path(rec["worktree"])
        for _ in range(50):
            if (wt / "prompt.txt").exists() and self.f("alive", rec["session_id"], check=False).returncode == 1:
                break
            time.sleep(0.1)
        sid, _, prompt = (wt / "prompt.txt").read_text().partition("\n")
        self.assertEqual(sid, rec["session_id"])
        self.assertIn("chain foundation in fleet albums", prompt)
        self.assertIn("Channel check", prompt)
        self.assertIn("speckit.fleet.chain.md", prompt)
        self.assertEqual(sh("git", "branch", "--show-current", cwd=wt).stdout.strip(),
                         "claude/fleet-albums-foundation")
        self.assertEqual(self.f("alive", rec["session_id"], check=False).returncode, 1)
        self.assertEqual(self.f("alive", "local_nope", check=False).returncode, 1)

        self.f("message", "albums", "foundation", "started", "--text", "hello")
        msgs = json.loads(self.f("inbox", "albums").stdout)
        self.assertEqual(msgs[0]["message"], "Fleet albums, chain foundation: started.\nhello")
        self.assertEqual(json.loads(self.f("inbox", "albums").stdout), [])

        # A second launch from inside the worktree reuses it (baton).
        sh(sys.executable, str(FLEET_PY), "launch", "albums", "foundation", "--dry-run", cwd=wt)

    def test_relative_remote(self):
        # Status writes push from the caller's checkout, so a relative remote
        # works for them; a local launch refuses it with the fix.
        sh("git", "remote", "set-url", "origin", "../origin.git", cwd=self.repo)
        self.publish()
        self.f("set", "albums", "us1", "us1-1", "--note", "x")
        r = self.f("launch", "albums", "foundation", check=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("git remote set-url origin", r.stderr)

    def test_session_env(self):
        env = dict(ENV, SPECKIT_FLEET_SESSION_ID="local_abc")
        self.assertEqual(self.f("session", env=env).stdout.strip(), "local_abc")
        self.assertEqual(self.f("session", check=False).returncode, 2)


class CloudTest(FleetTest):
    transport = "cloud"

    def test_launch_args_and_message(self):
        # A cloud session clones an https URL; route that URL to the bare repo.
        sh("git", "config", f"url.{self.origin}.insteadOf", "https://github.com/acme/app", cwd=self.repo)
        sh("git", "remote", "set-url", "origin", "https://github.com/acme/app", cwd=self.repo)
        r = self.f("plan", "albums", "specs/001-albums", "--out", str(self.tmp / "draft"))
        r = self.f("publish", "albums", "--from", str(self.tmp / "draft"), check=False)
        self.assertIn("--originating", r.stderr)
        self.publish()
        args = json.loads(self.f("launch", "albums", "foundation").stdout)
        self.assertEqual(set(args), {"prompt", "title", "source_url", "source_revision", "model"})
        self.assertEqual(args["source_revision"], "claude/fleet-albums-foundation")
        self.assertEqual(args["source_url"], "https://github.com/acme/app")
        self.assertEqual(args["model"], "claude-opus-5-5")
        self.assertEqual(args["title"], "albums / foundation / foundation-1")
        self.assertNotIn("Channel check", args["prompt"])
        self.assertIn("session_abc123", args["prompt"])
        msg = json.loads(self.f("message", "albums", "us1", "blocked", "--text", "x").stdout)
        self.assertEqual(msg["session_id"], "session_abc123")
        wd = self.f("prompt", "watchdog", "albums").stdout
        self.assertIn("speckit.fleet.tick.md", wd)
        self.assertEqual(self.f("alive", "session_abc", check=False).returncode, 2)

    def test_launch_refuses_non_https_source(self):
        self.publish()
        r = self.f("launch", "albums", "foundation", check=False)
        self.assertIn("is not an https URL", r.stderr)


if __name__ == "__main__":
    unittest.main()
