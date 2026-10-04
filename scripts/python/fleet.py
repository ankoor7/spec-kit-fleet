#!/usr/bin/env python3
"""spec-kit-fleet helper.

One script for every mechanical step of a fleet, so that no agent edits a
status file by hand: planning queues from tasks.md, publishing the branches,
claiming and closing items, logging conflicts and release blockers, building
launch arguments, and (local transport) starting chain sessions and carrying
their messages.

Standard library only; Python 3.9 or newer; git on PATH.
Run it from anywhere inside the project repository (a chain worktree counts).
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import glob
import json
import math
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

try:  # POSIX only; the lock is a convenience for several local sessions.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore

EXT_DIR_REL = ".specify/extensions/fleet"
STATUS_ROOT = ".fleet"
ITEM_STATUSES = ["pending", "running", "done", "blocked", "ready"]
CONFLICT_CLASSES = ["overlap", "dependency", "contradiction"]
CONFLICT_STATUSES = ["logged", "closed", "open", "resolved"]
HOLD_STATUSES = ["open", "closed"]
SESSION_RE = re.compile(r"^(session_[A-Za-z0-9]+|local_[A-Za-z0-9]+)$")
SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

DEFAULTS: Dict[str, Any] = {
    "transport": "cloud",
    "remote": "origin",
    "branches": {
        "trunk": "claude/fleet",
        "record": "claude/fleet-record",
        "release_target": "main",
        "chain_prefix": "claude/fleet-",
    },
    "models": {"work": "claude-opus-5-5", "light": "claude-sonnet-5-5"},
    "gate": {"quick": [], "full": []},
    "regenerate": [],
    "tasks_per_item": 6,
    "max_items_per_chain": 7,
    "reserved_sequences": {},
    "reserve_block": 5,
    "local": {
        "agent_command": "claude -p --model {model} --permission-mode acceptEdits",
        "worktrees_dir": "../{repo}-fleet",
    },
}


class FleetError(Exception):
    """A refusal with a reason; exits 1 with the message."""


class Retry(Exception):
    """A push lost a race; re-run the transaction."""


# --------------------------------------------------------------------------
# small utilities


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(args: List[str], cwd: Optional[str] = None, check: bool = True,
        input: Optional[str] = None, env: Optional[Dict[str, str]] = None) -> str:
    proc = subprocess.run(args, cwd=cwd, input=input, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    if check and proc.returncode != 0:
        raise FleetError(f"{' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout


def git(*args: str, cwd: Optional[str] = None, check: bool = True) -> str:
    return run(["git", *args], cwd=cwd, check=check)


def git_ok(*args: str, cwd: Optional[str] = None) -> bool:
    return subprocess.run(["git", *args], cwd=cwd, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0


def repo_root() -> Path:
    try:
        return Path(git("rev-parse", "--show-toplevel").strip())
    except FleetError:
        raise FleetError("not inside a git repository") from None


def common_dir() -> Path:
    p = Path(git("rev-parse", "--git-common-dir").strip())
    return p if p.is_absolute() else (Path.cwd() / p).resolve()


def runtime_dir(fleet: str) -> Path:
    """Machine-local state for the local transport: shared by every worktree
    of the repository because it lives in the common git directory."""
    d = common_dir() / "speckit-fleet" / fleet
    d.mkdir(parents=True, exist_ok=True)
    return d


def jsonl_load(text: str, where: str) -> List[Dict[str, Any]]:
    rows = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise FleetError(f"{where}:{n}: not valid JSON ({e.msg})") from None
    return rows


def jsonl_dump(rows: List[Dict[str, Any]]) -> str:
    return "".join(json.dumps(r, separators=(", ", ": ")) + "\n" for r in rows)


def latest(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Latest line per id of an append-only log, in first-seen order."""
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        out[r.get("id")] = r
    return list(out.values())


def emit(obj: Any) -> None:
    print(json.dumps(obj, indent=2))


# --------------------------------------------------------------------------
# configuration: a small YAML subset, so the helper needs no third-party module


def _scalar(s: str) -> Any:
    s = s.strip()
    if s == "" or s in ("~", "null"):
        return None
    if s[0] in "'\"" and s[-1] == s[0] and len(s) >= 2:
        return s[1:-1] if s[0] == "'" else json.loads(s)
    if s == "[]":
        return []
    if s == "{}":
        return {}
    if s.startswith("[") and s.endswith("]"):
        return [_scalar(x) for x in _split_inline(s[1:-1])]
    if s in ("true", "True", "yes"):
        return True
    if s in ("false", "False", "no"):
        return False
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    return s


def _split_inline(s: str) -> List[str]:
    parts, cur, q = [], "", ""
    for ch in s:
        if q:
            cur += ch
            if ch == q:
                q = ""
        elif ch in "'\"":
            q = ch
            cur += ch
        elif ch == ",":
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur)
    return parts


def _strip_comment(line: str) -> str:
    q = ""
    for i, ch in enumerate(line):
        if q:
            if ch == q:
                q = ""
        elif ch in "'\"":
            q = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i].rstrip()
    return line.rstrip()


def parse_yaml(text: str) -> Dict[str, Any]:
    """Maps, lists of scalars and inline scalars: all fleet-config.yml uses."""
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(text)
        return data or {}
    except ImportError:
        pass
    lines = [(len(l) - len(l.lstrip(" ")), l.strip())
             for l in (_strip_comment(x) for x in text.splitlines()) if l.strip()]

    def block(i: int, indent: int) -> Tuple[Any, int]:
        if i < len(lines) and lines[i][1].startswith("- "):
            out_l: List[Any] = []
            while i < len(lines) and lines[i][0] == indent and lines[i][1].startswith("- "):
                out_l.append(_scalar(lines[i][1][2:]))
                i += 1
            return out_l, i
        out: Dict[str, Any] = {}
        while i < len(lines) and lines[i][0] == indent:
            key, _, rest = lines[i][1].partition(":")
            key = key.strip().strip("'\"")
            i += 1
            if rest.strip():
                out[key] = _scalar(rest)
            elif i < len(lines) and lines[i][0] > indent:
                out[key], i = block(i, lines[i][0])
            elif i < len(lines) and lines[i][0] == indent and lines[i][1].startswith("- "):
                out[key], i = block(i, indent)
            else:
                out[key] = None
        return out, i

    data, _ = block(0, lines[0][0]) if lines else ({}, 0)
    return data if isinstance(data, dict) else {}


def _merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        elif v is not None:
            out[k] = v
    return out


def load_config(root: Optional[Path] = None) -> Dict[str, Any]:
    root = root or repo_root()
    cfg = dict(DEFAULTS)
    for name in ("fleet-config.yml", "local-config.yml"):
        p = root / EXT_DIR_REL / name
        if p.is_file():
            cfg = _merge(cfg, parse_yaml(p.read_text()))
    if os.environ.get("SPECKIT_FLEET_TRANSPORT"):
        cfg["transport"] = os.environ["SPECKIT_FLEET_TRANSPORT"]
    if cfg["transport"] not in ("cloud", "local"):
        raise FleetError(f"transport '{cfg['transport']}' is not cloud or local")
    return cfg


# --------------------------------------------------------------------------
# the status branch


class Fleet:
    def __init__(self, name: str, cfg: Optional[Dict[str, Any]] = None):
        if not ID_RE.match(name):
            raise FleetError(f"fleet name '{name}' must be lowercase letters, digits and hyphens")
        self.name = name
        self.cfg = cfg or load_config()
        b = self.cfg["branches"]
        self.remote = self.cfg["remote"]
        self.trunk, self.record = b["trunk"], b["record"]
        self.release_target, self.chain_prefix = b["release_target"], b["chain_prefix"]
        self.dir = f"{STATUS_ROOT}/{name}"
        self._manifest: Optional[Dict[str, Any]] = None

    # reading
    def fetch(self, branch: str) -> bool:
        return git_ok("fetch", "-q", self.remote,
                      f"+refs/heads/{branch}:refs/remotes/{self.remote}/{branch}")

    def ref(self, branch: str) -> str:
        return f"{self.remote}/{branch}"

    def show(self, path: str) -> Optional[str]:
        p = subprocess.run(["git", "show", f"{self.ref(self.record)}:{path}"],
                           text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        return p.stdout if p.returncode == 0 else None

    def load(self, fetch: bool = True) -> Dict[str, Any]:
        if fetch and not self.fetch(self.record):
            raise FleetError(f"cannot fetch {self.record} from {self.remote}")
        text = self.show(f"{self.dir}/manifest.json")
        if text is None:
            raise FleetError(f"no fleet {self.name} on {self.record}")
        self._manifest = json.loads(text)
        return self._manifest

    @property
    def manifest(self) -> Dict[str, Any]:
        return self._manifest if self._manifest is not None else self.load()

    def chain(self, chain_id: str) -> Dict[str, Any]:
        for c in self.manifest["chains"]:
            if c["id"] == chain_id:
                return c
        raise FleetError(f"no chain {chain_id} in fleet {self.name}")

    def queue(self, chain_id: str) -> List[Dict[str, Any]]:
        text = self.show(f"{self.dir}/{chain_id}.queue.jsonl")
        if text is None:
            raise FleetError(f"no queue for chain {chain_id}")
        return jsonl_load(text, f"{chain_id}.queue.jsonl")

    def log(self, name: str) -> List[Dict[str, Any]]:
        path = f"{STATUS_ROOT}/holds.jsonl" if name == "holds" else f"{self.dir}/{name}.jsonl"
        return jsonl_load(self.show(path) or "", path)

    def item_status(self, need: str) -> str:
        c, _, i = need.partition("/")
        try:
            q = self.queue(c)
        except FleetError:
            return "missing"
        for r in q:
            if r["id"] == i:
                return r["status"]
        return "missing"

    def item(self, need: str) -> Optional[Dict[str, Any]]:
        c, _, i = need.partition("/")
        for r in self.queue(c):
            if r["id"] == i:
                return r
        return None

    # writing
    @contextlib.contextmanager
    def lock(self) -> Iterator[None]:
        if fcntl is None:
            yield
            return
        path = common_dir() / "speckit-fleet.lock"
        with open(path, "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def transact(self, message: str, mutate: Callable[[Path], Any],
                 orphan: bool = False, attempts: int = 6) -> Any:
        """Check out the status branch in a scratch worktree, apply `mutate`,
        validate, commit and push. A push refused because another session
        pushed first re-runs the whole transaction on the new head."""
        last: Optional[Exception] = None
        for attempt in range(attempts):
            with self.lock():
                tmp = tempfile.mkdtemp(prefix="speckit-fleet-")
                wt = Path(tmp) / "record"
                exists = self.fetch(self.record)
                try:
                    if exists:
                        git("worktree", "add", "-q", "--detach", str(wt), self.ref(self.record))
                    elif orphan:
                        git("worktree", "add", "-q", "--detach", str(wt))
                        git("checkout", "-q", "--orphan", f"speckit-fleet-tmp-{secrets.token_hex(4)}", cwd=str(wt))
                        git("rm", "-rqf", "--ignore-unmatch", ".", cwd=str(wt))
                    else:
                        raise FleetError(f"no {self.record} on {self.remote}; run publish first")
                    result = mutate(wt)
                    problems = check_tree(wt, self.name, self.cfg) if (wt / self.dir).is_dir() else []
                    if problems:
                        raise FleetError("refused, the status files would be invalid:\n  " + "\n  ".join(problems))
                    git("add", "-A", ".", cwd=str(wt))
                    if not git("status", "--porcelain", cwd=str(wt)).strip():
                        return result
                    git("-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", message, cwd=str(wt))
                    # Push from the caller's checkout, not the scratch worktree: a
                    # remote given as a relative path resolves against the cwd.
                    sha = git("rev-parse", "HEAD", cwd=str(wt)).strip()
                    push = subprocess.run(["git", "push", "-q", self.remote, f"{sha}:refs/heads/{self.record}"],
                                          text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    if push.returncode != 0:
                        raise Retry(push.stderr.strip())
                    self._manifest = None
                    return result
                except Retry as e:
                    last = e
                finally:
                    git("worktree", "remove", "--force", str(wt), check=False)
                    shutil.rmtree(tmp, ignore_errors=True)
                    if orphan:
                        for b in git("branch", "--list", "speckit-fleet-tmp-*", check=False).split():
                            git("branch", "-D", b.strip("* "), check=False)
            time.sleep(min(2 ** attempt, 16))
        raise FleetError(f"push to {self.record} kept failing: {last}")

    def edit_queue(self, chain_id: str, message: str,
                   fn: Callable[[List[Dict[str, Any]]], Any]) -> Any:
        def mutate(wt: Path) -> Any:
            p = wt / self.dir / f"{chain_id}.queue.jsonl"
            if not p.exists():
                raise FleetError(f"no queue for chain {chain_id}")
            rows = jsonl_load(p.read_text(), p.name)
            result = fn(rows)
            p.write_text(jsonl_dump(rows))
            return result
        return self.transact(message, mutate)

    def append_log(self, name: str, row: Dict[str, Any], message: str) -> Dict[str, Any]:
        def mutate(wt: Path) -> Dict[str, Any]:
            p = wt / (f"{STATUS_ROOT}/holds.jsonl" if name == "holds" else f"{self.dir}/{name}.jsonl")
            p.parent.mkdir(parents=True, exist_ok=True)
            rows = jsonl_load(p.read_text(), p.name) if p.exists() else []
            if not row.get("id"):
                prefix = "h" if name == "holds" else "c"
                who = row.get("found_by") or (row.get("opened_by") or {}).get("chain") or "x"
                n = 1 + sum(1 for r in latest(rows) if str(r.get("id", "")).startswith(f"{prefix}-{who}-"))
                row["id"] = f"{prefix}-{who}-{n}"
            with open(p, "a") as fh:
                fh.write(json.dumps(row, separators=(", ", ": ")) + "\n")
            return row
        return self.transact(message, mutate)


# --------------------------------------------------------------------------
# validation (check-status)


def _ts_problem(v: Any, limit: str) -> Optional[str]:
    if v is None:
        return None
    if not isinstance(v, str) or not TIME_RE.match(v):
        return "is not an RFC3339 UTC time"
    if v > limit:
        return "is in the future"
    return None


def check_tree(root: Path, fleet: str, cfg: Optional[Dict[str, Any]] = None) -> List[str]:
    """Every problem in a checkout of the status files, as messages."""
    d = root / STATUS_ROOT / fleet
    probs: List[str] = []
    limit = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    mpath = d / "manifest.json"
    if not mpath.is_file():
        return [f"no {mpath}"]
    try:
        m = json.loads(mpath.read_text())
    except json.JSONDecodeError as e:
        return [f"manifest.json: not valid JSON ({e.msg})"]
    orig = m.get("originating_session") or ""
    if orig and not SESSION_RE.match(orig):
        probs.append(f"manifest.json: originating_session '{orig}' is not a session id")
    chain_ids = [c.get("id") for c in m.get("chains", [])]
    for cid in m.get("merge_order", []):
        if cid not in chain_ids:
            probs.append(f"manifest.json: merge_order names {cid}, which is no chain")
    queues: Dict[str, List[Dict[str, Any]]] = {}
    for cid in chain_ids:
        p = d / f"{cid}.queue.jsonl"
        if not p.is_file():
            probs.append(f"{cid}.queue.jsonl: missing")
            continue
        try:
            queues[cid] = jsonl_load(p.read_text(), p.name)
        except FleetError as e:
            probs.append(str(e))
    all_ids = {f"{c}/{r.get('id')}" for c, q in queues.items() for r in q if isinstance(r, dict)}
    for cid, q in queues.items():
        f = f"{cid}.queue.jsonl"
        if any(not isinstance(r, dict) for r in q):
            probs.append(f"{f}: a line is not a JSON object")
            continue
        ids = [r.get("id") for r in q]
        for i in sorted({i for i in ids if ids.count(i) > 1}):
            probs.append(f"{f}: id {i} appears {ids.count(i)} times")
        running = [r["id"] for r in q if r.get("status") == "running"]
        if len(running) > 1:
            probs.append(f"{f}: {len(running)} items running: {', '.join(running)}")
        for r in q:
            rid = r.get("id")
            if r.get("status") not in ITEM_STATUSES:
                probs.append(f"{f}: {rid}: status {json.dumps(r.get('status'))} is not one of {', '.join(ITEM_STATUSES)}")
            sid = r.get("session_id")
            if sid is not None and not SESSION_RE.match(str(sid)):
                probs.append(f"{f}: {rid}: session_id {json.dumps(sid)} is not a session id")
            if r.get("status") == "running" and (not sid or not r.get("started_at")):
                probs.append(f"{f}: {rid}: running without session_id and started_at")
            for k in ("started_at", "finished_at"):
                why = _ts_problem(r.get(k), limit)
                if why:
                    probs.append(f"{f}: {rid}: {k} {r.get(k)} {why}")
            if not all(isinstance(s, str) and SHA_RE.match(s) for s in r.get("commits") or []):
                probs.append(f"{f}: {rid}: commits must be commit SHAs")
            for pk in r.get("picked") or []:
                if not isinstance(pk.get("from"), str) or not all(SHA_RE.match(str(s)) for s in pk.get("commits") or []):
                    probs.append(f"{f}: {rid}: picked needs a from and commit SHAs")
            mg = r.get("merged")
            if mg is not None:
                if not SHA_RE.match(str(mg.get("sha", ""))):
                    probs.append(f"{f}: {rid}: merged.sha is not a commit SHA")
                why = _ts_problem(mg.get("at"), limit)
                if why:
                    probs.append(f"{f}: {rid}: merged.at {mg.get('at')} {why}")
            for need in r.get("needs") or []:
                if need not in all_ids:
                    probs.append(f"{f}: {rid}: needs {need}, which is no item of this fleet")
                else:
                    nc, _, ni = need.partition("/")
                    nr = next(x for x in queues[nc] if x.get("id") == ni)
                    if nr.get("status") == "done" and not nr.get("commits") and \
                            not re.search("nothing to build", nr.get("note") or "", re.I):
                        probs.append(f"{nc}.queue.jsonl: {ni} is done and needed by {cid}/{rid}, but lists no commits")
    for name, statuses in (("conflicts", CONFLICT_STATUSES),):
        p = d / f"{name}.jsonl"
        if p.is_file():
            try:
                for r in jsonl_load(p.read_text(), p.name):
                    if r.get("status") not in statuses:
                        probs.append(f"{name}.jsonl: {r.get('id')}: status {json.dumps(r.get('status'))} is not one of {', '.join(statuses)}")
                    if r.get("class") not in CONFLICT_CLASSES:
                        probs.append(f"{name}.jsonl: {r.get('id')}: class {json.dumps(r.get('class'))} is not one of {', '.join(CONFLICT_CLASSES)}")
            except FleetError as e:
                probs.append(str(e))
    hp = root / STATUS_ROOT / "holds.jsonl"
    if hp.is_file():
        try:
            for r in jsonl_load(hp.read_text(), hp.name):
                if r.get("status") not in HOLD_STATUSES:
                    probs.append(f"holds.jsonl: {r.get('id')}: status {json.dumps(r.get('status'))} is not one of {', '.join(HOLD_STATUSES)}")
        except FleetError as e:
            probs.append(str(e))
    return probs


# --------------------------------------------------------------------------
# planning: tasks.md -> chains and queues

PHASE_RE = re.compile(r"^##\s+Phase\s+(\d+)\s*[:.-]\s*(.+?)\s*$")
TASK_RE = re.compile(r"^\s*-\s+\[( |x|X)\]\s+(T\d+)\b\s*(.*)$")
STORY_RE = re.compile(r"User Story\s+(\d+)", re.I)
PATH_RE = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)+[\w.-]+\.[A-Za-z0-9]+)")


def parse_tasks(path: Path) -> List[Dict[str, Any]]:
    """Phases of a Spec Kit tasks.md, each with its open tasks."""
    phases: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    for line in path.read_text().splitlines():
        m = PHASE_RE.match(line)
        if m:
            title = m.group(2)
            sm = STORY_RE.search(title)
            if sm:
                kind, story = "story", int(sm.group(1))
            elif re.search(r"setup", title, re.I):
                kind, story = "setup", None
            elif re.search(r"foundation", title, re.I):
                kind, story = "foundational", None
            elif re.search(r"polish|cross-cutting", title, re.I):
                kind, story = "polish", None
            else:
                kind, story = "other", None
            cur = {"number": int(m.group(1)), "title": title.replace("🎯", "").strip(),
                   "kind": kind, "story": story, "tasks": []}
            phases.append(cur)
            continue
        if line.startswith("## "):
            cur = None
            continue
        t = TASK_RE.match(line)
        if t and cur is not None:
            text = t.group(3)
            cur["tasks"].append({
                "id": t.group(2),
                "done": t.group(1) != " ",
                "parallel": "[P]" in text,
                "text": re.sub(r"\s+", " ", re.sub(r"\[(P|US\d+)\]", "", text)).strip(),
                "paths": sorted(set(PATH_RE.findall(text))),
            })
    return phases


def _groups(tasks: List[Dict[str, Any]], size: int) -> List[List[Dict[str, Any]]]:
    return [tasks[i:i + size] for i in range(0, len(tasks), size)] or []


def _max_seq(pattern: str, root: Path) -> int:
    best = 0
    for f in glob.glob(str(root / pattern)):
        m = re.match(r"(\d+)", os.path.basename(f))
        if m:
            best = max(best, int(m.group(1)))
    return best


def plan_fleet(fleet: str, features: List[Path], cfg: Dict[str, Any], root: Path,
               title: Optional[str] = None) -> Tuple[Dict[str, Any], Dict[str, List[Dict[str, Any]]]]:
    if not features:
        raise FleetError("name at least one feature directory (specs/NNN-name)")
    multi = len(features) > 1
    per_item = int(cfg["tasks_per_item"])
    cap = int(cfg["max_items_per_chain"]) if cfg["transport"] == "cloud" else 0
    chains: List[Dict[str, Any]] = []
    queues: Dict[str, List[Dict[str, Any]]] = {}

    def add_chain(cid: str, ctitle: str, feature: Path, phases: List[Dict[str, Any]], stories: List[int]) -> None:
        tasks = [t for p in phases for t in p["tasks"] if not t["done"]]
        if not tasks:
            return
        size = per_item
        if cap and math.ceil(len(tasks) / size) + 2 > cap:
            size = math.ceil(len(tasks) / (cap - 2))
        branch = f"{cfg['branches']['chain_prefix']}{fleet}-{cid}"
        spec = str((feature / "spec.md").relative_to(root)) if (feature / "spec.md").exists() else str(feature.relative_to(root))
        items: List[Dict[str, Any]] = []
        for n, g in enumerate(_groups(tasks, size), 1):
            items.append({
                "id": f"{cid}-{n}", "kind": "work",
                "title": f"{ctitle}: {g[0]['id']}–{g[-1]['id']}" if len(g) > 1 else f"{ctitle}: {g[0]['id']}",
                "tasks": [t["id"] for t in g],
                "brief": "\n".join(f"{t['id']} {'[P] ' if t['parallel'] else ''}{t['text']}" for t in g),
                "spec": spec, "tasks_file": str((feature / "tasks.md").relative_to(root)),
                "branch": branch, "status": "pending", "model": cfg["models"]["work"], "needs": [],
            })
        for kind in ("integrate", "register"):
            items.append({"id": f"{cid}-{kind}", "kind": kind, "title": f"{ctitle}: {kind}",
                          "spec": spec, "branch": branch, "status": "pending",
                          "model": cfg["models"]["light"], "needs": []})
        chains.append({
            "id": cid, "title": ctitle, "branch": branch, "queue": f"{cid}.queue.jsonl",
            "feature": str(feature.relative_to(root)), "stories": [f"US{s}" for s in stories],
            "phases": [p["number"] for p in phases],
            "surfaces": {"paths": sorted({x for t in tasks for x in t["paths"]}),
                         "tasks": [t["id"] for t in tasks]},
            "reserved": {},
        })
        queues[cid] = items

    for feature in features:
        feature = feature.resolve()
        tf = feature / "tasks.md"
        if not tf.is_file():
            raise FleetError(f"{tf} not found: run the tasks command for this feature first")
        phases = parse_tasks(tf)
        pre = ""
        if multi:
            m = re.match(r"(\d+)", feature.name)
            pre = (m.group(1) if m else feature.name.lower()) + "-"
        found = [p for p in phases if p["kind"] in ("setup", "foundational")]
        before = len(chains)
        add_chain(f"{pre}foundation", f"{feature.name} setup and foundation", feature, found, [])
        found_id = chains[-1]["id"] if len(chains) > before else None
        story_ids = []
        for p in phases:
            if p["kind"] in ("story", "other"):
                cid = f"{pre}us{p['story']}" if p["kind"] == "story" else f"{pre}p{p['number']}"
                before = len(chains)
                add_chain(cid, p["title"], feature, [p], [p["story"]] if p["story"] else [])
                if len(chains) > before:
                    story_ids.append(cid)
        polish = [p for p in phases if p["kind"] == "polish"]
        before = len(chains)
        add_chain(f"{pre}polish", f"{feature.name} polish", feature, polish, [])
        polish_id = chains[-1]["id"] if len(chains) > before else None

        def work(cid: str) -> List[str]:
            return [f"{cid}/{i['id']}" for i in queues[cid] if i["kind"] == "work"]

        if found_id:
            for cid in story_ids + ([polish_id] if polish_id else []):
                queues[cid][0]["needs"] += work(found_id)
        if polish_id:
            for cid in story_ids:
                queues[polish_id][0]["needs"] += work(cid)

    for cid, q in queues.items():
        if cap and len(q) > cap:
            raise FleetError(f"chain {cid} has {len(q)} items; the cloud cap is {cap}")
    for name, pattern in (cfg.get("reserved_sequences") or {}).items():
        start = _max_seq(pattern, root) + 1
        block = int(cfg["reserve_block"])
        for n, c in enumerate(chains):
            lo = start + n * block
            c["reserved"][name] = [lo, lo + block - 1]
    manifest = {
        "fleet": fleet, "title": title or f"Fleet {fleet}", "created_at": now(),
        "originating_session": None, "transport": cfg["transport"],
        "trunk": cfg["branches"]["trunk"], "record": cfg["branches"]["record"],
        "release_target": cfg["branches"]["release_target"],
        "features": [str(f.resolve().relative_to(root)) for f in features],
        "merge_order": [c["id"] for c in chains], "chains": chains,
    }
    return manifest, queues


def write_draft(out: Path, fleet: str, manifest: Dict[str, Any],
                queues: Dict[str, List[Dict[str, Any]]]) -> Path:
    d = out / STATUS_ROOT / fleet
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    for cid, q in queues.items():
        (d / f"{cid}.queue.jsonl").write_text(jsonl_dump(q))
    (d / "conflicts.jsonl").touch()
    dec = d / "owner-decisions.md"
    if not dec.exists():
        dec.write_text(OWNER_DECISIONS.format(fleet=fleet))
    return d


OWNER_DECISIONS = """# Fleet {fleet}: owner decisions

## Pre-flight

_None yet._

## Stopped

_None yet._

## Wrap-up

_Not yet run._
"""


# --------------------------------------------------------------------------
# launching


def ext_dir(root: Optional[Path] = None) -> Path:
    here = Path(__file__).resolve().parent.parent.parent
    installed = (root or repo_root()) / EXT_DIR_REL
    return installed if (installed / "templates").is_dir() else here


def render(template: str, values: Dict[str, str]) -> str:
    out = template
    for k, v in values.items():
        out = out.replace("{" + k + "}", v)
    return out


def template_block(name: str, first: bool = False) -> str:
    text = (ext_dir() / "templates" / name).read_text()
    m = re.search(r"```text\n(.*?)\n```", text, re.S)
    if not m:
        raise FleetError(f"templates/{name} has no ```text block")
    body = m.group(1)
    marker = "--- first session only ---"
    if marker in body:
        head, _, tail = body.partition(marker)
        body = head.rstrip("\n") + ("\n" + tail.strip("\n") if first else "")
    return body


def fleet_values(f: Fleet, chain: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    m = f.manifest
    v = {"fleet": f.name, "record": f.record, "trunk": f.trunk, "remote": f.remote,
         "release_target": f.release_target, "transport": f.cfg["transport"],
         "originating": m.get("originating_session") or "", "ext": EXT_DIR_REL}
    if chain:
        v.update({"chain": chain["id"], "branch": chain["branch"]})
    return v


def launchable(f: Fleet, chain_id: str) -> Dict[str, Any]:
    q = f.queue(chain_id)
    running = [r["id"] for r in q if r["status"] == "running"]
    if running:
        raise FleetError(f"chain {chain_id} has a running item ({', '.join(running)}); its session hands the baton")
    nxt = next((r for r in q if r["status"] != "done"), None)
    if nxt is None:
        raise FleetError(f"chain {chain_id} has no item left")
    if nxt["status"] != "pending":
        raise FleetError(f"next item {nxt['id']} is {nxt['status']}, not pending")
    for need in nxt.get("needs") or []:
        s = f.item_status(need)
        if s != "done":
            raise FleetError(f"next item {nxt['id']} needs {need}, which is {s}")
    return nxt


def repo_url() -> str:
    # The configured URL, not `remote get-url`, which applies insteadOf rewrites.
    url = git("config", "--get", f"remote.{load_config()['remote']}.url").strip()
    url = re.sub(r"^https?://[^/]*@?127\.0\.0\.1:\d+/git/", "https://github.com/", url)
    url = re.sub(r"^git@github\.com:", "https://github.com/", url)
    return re.sub(r"\.git$", "", url)


def launch(f: Fleet, chain_id: str, first: bool, dry_run: bool) -> Dict[str, Any]:
    chain = f.chain(chain_id)
    if f.cfg["transport"] == "cloud" and not SESSION_RE.match(f.manifest.get("originating_session") or ""):
        raise FleetError("manifest has no originating_session")
    nxt = launchable(f, chain_id)
    prompt = render(template_block("chain-prompt.md", first), fleet_values(f, chain))
    model = nxt.get("model") or f.cfg["models"]["work" if nxt.get("kind") == "work" else "light"]
    title = f"{f.name} / {chain_id} / {nxt['id']}"
    if f.cfg["transport"] == "cloud":
        args = {"prompt": prompt, "title": title, "source_url": repo_url(),
                "source_revision": chain["branch"], "model": model}
        missing = [k for k, v in args.items() if not v]
        if missing:
            raise FleetError(f"create_session arguments would lack {', '.join(missing)}")
        if not args["source_url"].startswith("https://"):
            raise FleetError(f"source_url '{args['source_url']}' is not an https URL; a cloud session cannot clone it")
        return args
    return spawn_local(f, chain, nxt, prompt, model, title, dry_run)


def chain_worktree(f: Fleet, chain: Dict[str, Any]) -> Path:
    # Worktrees sit beside the main checkout, whichever worktree launches.
    cd = common_dir()
    root = cd.parent if cd.name == ".git" else repo_root()
    base = render(f.cfg["local"]["worktrees_dir"], {"repo": root.name, "fleet": f.name})
    base_p = (root / base).resolve() if not os.path.isabs(base) else Path(base)
    wt = base_p / f"{f.name}-{chain['id']}"
    if not wt.exists():
        f.fetch(chain["branch"])
        base_p.mkdir(parents=True, exist_ok=True)
        if git_ok("show-ref", "--verify", "-q", f"refs/heads/{chain['branch']}"):
            git("worktree", "add", "-q", str(wt), chain["branch"])
        else:
            git("worktree", "add", "-q", "--track", "-b", chain["branch"], str(wt), f.ref(chain["branch"]))
    return wt


def spawn_local(f: Fleet, chain: Dict[str, Any], item: Dict[str, Any], prompt: str,
                model: str, title: str, dry_run: bool) -> Dict[str, Any]:
    url = git("remote", "get-url", f.remote).strip()
    if not re.match(r"^([a-z][a-z0-9+.-]*://|[^/]+@[^/]+:|/)", url):
        # git resolves a relative remote path against each command's cwd, so a
        # session in a worktree elsewhere could not fetch or push.
        raise FleetError(f"remote {f.remote} is the relative path '{url}'; chain worktrees cannot reach it. "
                         f"Run: git remote set-url {f.remote} \"$(realpath '{url}')\"")
    sid = "local_" + secrets.token_hex(6)
    rt = runtime_dir(f.name)
    wt = chain_worktree(f, chain)
    argv = [render(a, {"model": model}) for a in shlex.split(f.cfg["local"]["agent_command"])] + [prompt]
    log = rt / "logs" / f"{chain['id']}-{item['id']}-{sid}.log"
    log.parent.mkdir(exist_ok=True)
    rec = {"session_id": sid, "chain": chain["id"], "item": item["id"], "title": title,
           "worktree": str(wt), "log": str(log), "model": model, "argv": argv[:-1],
           "started_at": now(), "pid": None}
    if not dry_run:
        env = dict(os.environ, SPECKIT_FLEET_SESSION_ID=sid, SPECKIT_FLEET_TRANSPORT="local")
        with open(log, "w") as out:
            proc = subprocess.Popen(argv, cwd=str(wt), stdout=out, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, env=env, start_new_session=True)
        rec["pid"] = proc.pid
    (rt / "sessions").mkdir(exist_ok=True)
    (rt / "sessions" / f"{sid}.json").write_text(json.dumps(rec, indent=2))
    return rec


def local_alive(sid: str) -> Optional[bool]:
    for p in glob.glob(str(common_dir() / "speckit-fleet" / "*" / "sessions" / f"{sid}.json")):
        pid = json.loads(Path(p).read_text()).get("pid")
        if not pid:
            return False
        try:
            os.kill(int(pid), 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        # A zombie (exited, not yet reaped) counts as dead.
        stat = Path(f"/proc/{pid}/stat")
        if stat.exists() and stat.read_text().split(") ")[-1].startswith("Z"):
            return False
        return True
    return None


# --------------------------------------------------------------------------
# commands


def cmd_config(a: argparse.Namespace) -> None:
    emit(load_config())


def cmd_plan(a: argparse.Namespace) -> None:
    cfg = load_config()
    root = repo_root()
    feats = [Path(x) if os.path.isabs(x) else (Path.cwd() / x) for x in a.features]
    manifest, queues = plan_fleet(a.fleet, feats, cfg, root, a.title)
    out = Path(a.out).resolve()
    d = write_draft(out, a.fleet, manifest, queues)
    probs = check_tree(out, a.fleet, cfg)
    if probs:
        raise FleetError("planned files are invalid:\n  " + "\n  ".join(probs))
    print(f"draft: {d}")
    for c in manifest["chains"]:
        q = queues[c["id"]]
        needs = sorted({n for i in q for n in i.get("needs", [])})
        print(f"  {c['id']}: {len(q)} items, tasks {', '.join(c['surfaces']['tasks'])}"
              + (f"; needs {', '.join(needs)}" if needs else ""))


def cmd_check(a: argparse.Namespace) -> None:
    cfg = load_config()
    if a.dir:
        probs = check_tree(Path(a.dir), a.fleet, cfg)
    else:
        f = Fleet(a.fleet, cfg)
        f.fetch(f.record)
        with tempfile.TemporaryDirectory() as tmp:
            files = git("ls-tree", "-r", "--name-only", f.ref(f.record), STATUS_ROOT).split()
            for p in files:
                dest = Path(tmp) / p
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(f.show(p) or "")
            probs = check_tree(Path(tmp), a.fleet, cfg)
    if probs:
        for p in probs:
            print(p, file=sys.stderr)
        raise FleetError(f"{len(probs)} problem(s)")
    print(f"check: {a.fleet} ok")


def cmd_publish(a: argparse.Namespace) -> None:
    cfg = load_config()
    f = Fleet(a.fleet, cfg)
    src = Path(a.source).resolve() / STATUS_ROOT / a.fleet
    if not (src / "manifest.json").is_file():
        raise FleetError(f"no draft at {src}; run plan first")
    if cfg["transport"] == "cloud" and not SESSION_RE.match(a.originating or ""):
        raise FleetError("--originating must be this session's id (get_session returns it)")
    manifest = json.loads((src / "manifest.json").read_text())
    manifest["originating_session"] = a.originating or "local_" + secrets.token_hex(6)
    (src / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    probs = check_tree(src.parent.parent, a.fleet, cfg)
    if probs:
        raise FleetError("draft is invalid:\n  " + "\n  ".join(probs))
    if not f.fetch(f.release_target):
        raise FleetError(f"cannot fetch {f.release_target} from {f.remote}")
    if not f.fetch(f.trunk):
        git("push", "-q", f.remote, f"{f.ref(f.release_target)}:refs/heads/{f.trunk}")
        f.fetch(f.trunk)
        print(f"created {f.trunk} from {f.release_target}")

    def mutate(wt: Path) -> None:
        d = wt / f.dir
        if (d / "manifest.json").exists() and not a.force:
            raise FleetError(f"fleet {a.fleet} already exists on {f.record} (use --force to replace it)")
        if d.exists():
            shutil.rmtree(d)
        shutil.copytree(src, d)
        readme = wt / STATUS_ROOT / "README.md"
        if not readme.exists():
            readme.write_text("Fleet status files, written by spec-kit-fleet. Do not edit by hand:\n"
                              "use `fleet.py` (claim, close, set, conflict, hold, put).\n")
        (wt / STATUS_ROOT / "holds.jsonl").touch()

    f.transact(f"fleet {a.fleet}: publish", mutate, orphan=True)
    for c in manifest["chains"]:
        if not f.fetch(c["branch"]):
            git("push", "-q", f.remote, f"{f.ref(f.trunk)}:refs/heads/{c['branch']}")
            print(f"created {c['branch']} from {f.trunk}")
    print(f"published fleet {a.fleet} to {f.record}; originating session {manifest['originating_session']}")


def cmd_state(a: argparse.Namespace) -> None:
    f = Fleet(a.fleet)
    m = f.load()
    f.fetch(f.trunk)
    f.fetch(f.release_target)
    out: Dict[str, Any] = {"fleet": f.name, "record": f.record, "trunk": f.trunk,
                           "release_target": f.release_target, "transport": f.cfg["transport"],
                           "originating_session": m.get("originating_session"), "chains": []}
    for cid in m["merge_order"]:
        q = f.queue(cid)
        nxt = next((r for r in q if r["status"] != "done"), None)
        out["chains"].append({
            "id": cid, "done": sum(r["status"] == "done" for r in q), "items": len(q),
            "next": None if nxt is None else {k: nxt.get(k) for k in ("id", "status", "needs", "note")},
            "needs_done": None if nxt is None else all(f.item_status(n) == "done" for n in nxt.get("needs") or []),
            "running": [{"id": r["id"], "session_id": r.get("session_id")} for r in q if r["status"] == "running"],
        })
    out["conflicts"] = [c for c in latest(f.log("conflicts")) if c.get("status") in ("open", "logged")]
    out["holds"] = [h for h in latest(f.log("holds")) if h.get("status") == "open"]
    t, r = f.ref(f.trunk), f.ref(f.release_target)
    if git_ok("rev-parse", "-q", "--verify", t) and git_ok("rev-parse", "-q", "--verify", r):
        out["trunk_state"] = {"head": git("rev-parse", "--short", t).strip(),
                              "ahead": int(git("rev-list", "--count", f"{r}..{t}")),
                              "behind": int(git("rev-list", "--count", f"{t}..{r}"))}
    dec = f.show(f"{f.dir}/owner-decisions.md")
    out["wrap_up"] = "unknown" if dec is None else ("not yet run" if "_Not yet run._" in dec.split("## Wrap-up")[-1] else "done")
    if a.json:
        emit(out)
        return
    print(f"fleet {f.name}  transport {out['transport']}  record {f.record}  trunk {f.trunk}  release target {f.release_target}")
    print(f"originating session {out['originating_session'] or '<none>'}\n")
    for c in out["chains"]:
        n = c["next"]
        line = f"{c['id']}: {c['done']}/{c['items']} done; next: "
        line += "none (chain finished)" if n is None else f"{n['id']} [{n['status']}]" + \
            (f" needs {','.join(n['needs'])}{'' if c['needs_done'] else ' (not all done)'}" if n.get("needs") else "")
        if c["running"]:
            line += "; running: " + ", ".join(f"{r['id']} {r['session_id']}" for r in c["running"])
        print(line)
    print("\nconflicts not closed or resolved:")
    print("\n".join(f"  {c['id']} {c['class']} {c['status']}: {c.get('title')}" for c in out["conflicts"]) or "  none")
    print("open release blockers:")
    print("\n".join(f"  {h['id']}: {h.get('title')}" for h in out["holds"]) or "  none")
    ts = out.get("trunk_state")
    print("\n" + (f"trunk head {ts['head']}; ahead of {f.release_target} by {ts['ahead']}, behind by {ts['behind']}"
                  if ts else "trunk or release target not found on the remote"))
    print(f"wrap-up: {out['wrap_up']}")


def cmd_launch(a: argparse.Namespace) -> None:
    f = Fleet(a.fleet)
    f.load()
    emit(launch(f, a.chain, a.first, a.dry_run))


def cmd_prompt(a: argparse.Namespace) -> None:
    f = Fleet(a.fleet)
    f.load()
    if a.which == "watchdog":
        print(render(template_block("watchdog-prompt.md"), fleet_values(f)))
    else:
        if not a.chain:
            raise FleetError("prompt chain needs --chain")
        print(render(template_block("chain-prompt.md", a.first), fleet_values(f, f.chain(a.chain))))


def cmd_claim(a: argparse.Namespace) -> None:
    if not SESSION_RE.match(a.session):
        raise FleetError(f"'{a.session}' is not a session id")
    f = Fleet(a.fleet)
    f.load()
    blockers = [c for c in latest(f.log("conflicts"))
                if c.get("status") == "open" and a.chain in (c.get("chains") or [])]
    if blockers:
        raise FleetError(f"open conflict {blockers[0]['id']} names chain {a.chain}: stop without claiming")
    need_status = {}
    for r in f.queue(a.chain):
        for n in r.get("needs") or []:
            need_status[n] = f.item_status(n)

    def fn(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        if any(r["status"] == "running" for r in rows):
            raise FleetError("another item of this chain is running: stop")
        nxt = next((r for r in rows if r["status"] != "done"), None)
        if nxt is None:
            raise FleetError("every item of this chain is done: stop")
        if nxt["status"] != "pending":
            raise FleetError(f"next item {nxt['id']} is {nxt['status']}: stop")
        unmet = [n for n in nxt.get("needs") or [] if need_status.get(n) != "done"]
        if unmet:
            nxt.update(status="blocked", finished_at=now(), session_id=a.session,
                       note=f"waits for {', '.join(unmet)}")
            return {"waiting": True, **nxt}
        nxt.update(status="running", session_id=a.session, started_at=now())
        nxt.pop("finished_at", None)
        return nxt

    res = f.edit_queue(a.chain, f"fleet {f.name}: {a.chain} claim by {a.session}", fn)
    emit(res)
    if res.get("waiting"):
        sys.exit(4)


def _ids_set(f: Fleet, chain: str, item: str, fn: Callable[[Dict[str, Any]], None], msg: str) -> Dict[str, Any]:
    def edit(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        for r in rows:
            if r["id"] == item:
                fn(r)
                return r
        raise FleetError(f"no item {item} in chain {chain}")
    return f.edit_queue(chain, msg, edit)


def cmd_close(a: argparse.Namespace) -> None:
    f = Fleet(a.fleet)
    f.load()

    def fn(r: Dict[str, Any]) -> None:
        if r["status"] != "running" and not a.force:
            raise FleetError(f"{a.item} is {r['status']}, not running (use --force)")
        r["status"] = a.status
        r["finished_at"] = now()
        if a.note:
            r["note"] = a.note
        if a.commits:
            r["commits"] = a.commits
        if a.merged:
            r["merged"] = {"sha": a.merged, "at": now()}
    emit(_ids_set(f, a.chain, a.item, fn, f"fleet {f.name}: {a.chain}/{a.item} {a.status}"))


def cmd_set(a: argparse.Namespace) -> None:
    f = Fleet(a.fleet)
    f.load()

    def fn(r: Dict[str, Any]) -> None:
        if a.status:
            if a.status == "pending":
                for k in ("session_id", "started_at", "finished_at"):
                    r.pop(k, None)
            r["status"] = a.status
        if a.note:
            r["note"] = a.note
        for n in a.add_need or []:
            if n not in r.setdefault("needs", []):
                r["needs"].append(n)
    emit(_ids_set(f, a.chain, a.item, fn, f"fleet {f.name}: {a.chain}/{a.item} set"))


def cmd_add_item(a: argparse.Namespace) -> None:
    f = Fleet(a.fleet)
    f.load()
    chain = f.chain(a.chain)

    def fn(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        if any(r["id"] == a.id for r in rows):
            raise FleetError(f"item {a.id} already exists")
        row = {"id": a.id, "kind": "work", "title": a.title, "brief": a.brief or a.title,
               "spec": rows[0].get("spec") if rows else None, "branch": chain["branch"],
               "status": "pending", "model": f.cfg["models"]["work"], "needs": a.need or []}
        pos = next((i for i, r in enumerate(rows) if r.get("kind") in ("integrate", "register") and r["status"] != "done"), len(rows)) \
            if a.before_integrate else len(rows)
        rows.insert(pos, row)
        return row
    emit(f.edit_queue(a.chain, f"fleet {f.name}: {a.chain} add {a.id}", fn))


def cmd_picks(a: argparse.Namespace) -> None:
    """The sibling commits an item's needs name that are not yet on HEAD."""
    f = Fleet(a.fleet)
    f.load()
    item = next((r for r in f.queue(a.chain) if r["id"] == a.item), None)
    if item is None:
        raise FleetError(f"no item {a.item} in chain {a.chain}")
    out = []
    for need in item.get("needs") or []:
        src = f.item(need)
        if src is None or src["status"] != "done":
            raise FleetError(f"{need} is not done")
        f.fetch(f.chain(need.split("/")[0])["branch"])
        todo = [s for s in src.get("commits") or []
                if not git("log", "--format=%H", "--grep", f"cherry picked from commit {s}", "HEAD").strip()
                and not git_ok("merge-base", "--is-ancestor", s, "HEAD")]
        out.append({"from": need, "commits": todo})
    emit(out)


def cmd_record_pick(a: argparse.Namespace) -> None:
    f = Fleet(a.fleet)
    f.load()

    def fn(r: Dict[str, Any]) -> None:
        r.setdefault("picked", []).append({"from": a.source, "commits": a.commits, "at": now()})
    emit(_ids_set(f, a.chain, a.item, fn, f"fleet {f.name}: {a.chain}/{a.item} picked {a.source}"))


def _row_from(a: argparse.Namespace) -> Dict[str, Any]:
    row = json.loads(Path(a.file).read_text() if a.file else a.data)
    if not isinstance(row, dict):
        raise FleetError("the row must be a JSON object")
    return row


def cmd_conflict(a: argparse.Namespace) -> None:
    f = Fleet(a.fleet)
    f.load()
    row = _row_from(a)
    if row.get("class") not in CONFLICT_CLASSES:
        raise FleetError(f"class must be one of {', '.join(CONFLICT_CLASSES)}")
    if row.get("status") not in CONFLICT_STATUSES:
        raise FleetError(f"status must be one of {', '.join(CONFLICT_STATUSES)}")
    if row["status"] in ("closed", "resolved"):
        if not row.get("id"):
            raise FleetError("closing a conflict needs its id")
        row.setdefault("resolved_at", now())
    else:
        row.setdefault("found_at", now())
    emit(f.append_log("conflicts", row, f"fleet {f.name}: conflict {row.get('id') or 'new'} {row['status']}"))


def cmd_hold(a: argparse.Namespace) -> None:
    f = Fleet(a.fleet)
    f.load()
    row = _row_from(a)
    if row.get("status") not in HOLD_STATUSES:
        raise FleetError("status must be open or closed")
    row.setdefault("opened_at" if row["status"] == "open" else "closed_at", now())
    emit(f.append_log("holds", row, f"fleet {f.name}: hold {row.get('id') or 'new'} {row['status']}"))


def cmd_conflicts(a: argparse.Namespace) -> None:
    f = Fleet(a.fleet)
    f.load()
    rows = latest(f.log("conflicts"))
    if a.open:
        rows = [r for r in rows if r.get("status") in ("open", "logged")]
    emit(rows)


def cmd_put(a: argparse.Namespace) -> None:
    if not re.fullmatch(r"[A-Za-z0-9._-]+\.md", a.name):
        raise FleetError("put writes only a Markdown file directly in the fleet's directory")
    f = Fleet(a.fleet)
    f.load()
    text = Path(a.file).read_text()

    def mutate(wt: Path) -> None:
        (wt / f.dir / a.name).write_text(text)
    f.transact(f"fleet {f.name}: {a.name}", mutate)
    print(f"wrote {f.dir}/{a.name} on {f.record}")


def cmd_get(a: argparse.Namespace) -> None:
    f = Fleet(a.fleet)
    f.load()
    text = f.show(f"{f.dir}/{a.name}")
    if text is None:
        raise FleetError(f"no {a.name}")
    sys.stdout.write(text)


def cmd_message(a: argparse.Namespace) -> None:
    f = Fleet(a.fleet)
    m = f.load()
    text = a.text if a.text is not None else Path(a.file).read_text()
    head = f"Fleet {f.name}, chain {a.chain}: {a.kind}."
    body = text if text.startswith(f"Fleet {f.name}") else f"{head}\n{text}".rstrip()
    if f.cfg["transport"] == "cloud":
        emit({"session_id": m["originating_session"], "message": body,
              "tool": "mcp__claude-code-remote__send_message"})
        return
    p = runtime_dir(f.name) / "inbox.jsonl"
    with f.lock(), open(p, "a") as fh:
        fh.write(json.dumps({"at": now(), "chain": a.chain, "kind": a.kind, "message": body}) + "\n")
    print(f"delivered to {p}")


def cmd_inbox(a: argparse.Namespace) -> None:
    f = Fleet(a.fleet)
    rt = runtime_dir(f.name)
    p, cur = rt / "inbox.jsonl", rt / "inbox.cursor"
    rows = jsonl_load(p.read_text(), p.name) if p.exists() else []
    start = int(cur.read_text()) if cur.exists() and not a.all else 0
    emit(rows[start:])
    if not a.peek:
        cur.write_text(str(len(rows)))


def cmd_alive(a: argparse.Namespace) -> None:
    if a.session.startswith("session_"):
        print("cloud session: ask get_session (mcp__claude-code-remote__get_session)")
        sys.exit(2)
    state = local_alive(a.session)
    print({True: "alive", False: "dead", None: "unknown"}[state])
    sys.exit(0 if state else 1)


def cmd_gate(a: argparse.Namespace) -> None:
    cfg = load_config()
    cmds = cfg["regenerate"] if a.which == "regenerate" else cfg["gate"][a.which]
    if not cmds:
        print(f"gate {a.which}: no commands configured")
        return
    for c in cmds:
        print(f"$ {c}", flush=True)
        if subprocess.run(c, shell=True).returncode != 0:
            raise FleetError(f"gate {a.which} failed at: {c}")
    print(f"gate {a.which}: passed")


def cmd_session(a: argparse.Namespace) -> None:
    sid = os.environ.get("SPECKIT_FLEET_SESSION_ID")
    if sid:
        print(sid)
        return
    print("cloud: get_session with no argument returns this session's id")
    sys.exit(2)


def main(argv: Optional[List[str]] = None) -> None:
    p = argparse.ArgumentParser(prog="fleet.py", description=__doc__.split("\n\n")[0])
    sp = p.add_subparsers(dest="cmd", required=True)

    def sub(name: str, fn: Callable, help: str) -> argparse.ArgumentParser:
        s = sp.add_parser(name, help=help)
        s.set_defaults(fn=fn)
        return s

    sub("config", cmd_config, "print the resolved configuration")
    s = sub("plan", cmd_plan, "draft a fleet from one or more feature directories")
    s.add_argument("fleet"); s.add_argument("features", nargs="+")
    s.add_argument("--out", required=True); s.add_argument("--title")
    s = sub("check", cmd_check, "validate status files (a draft dir, or the status branch)")
    s.add_argument("fleet"); s.add_argument("dir", nargs="?")
    s = sub("publish", cmd_publish, "push a draft: trunk, status branch, chain branches")
    s.add_argument("fleet"); s.add_argument("--from", dest="source", required=True)
    s.add_argument("--originating"); s.add_argument("--force", action="store_true")
    s = sub("state", cmd_state, "one read of the whole fleet")
    s.add_argument("fleet"); s.add_argument("--json", action="store_true")
    s = sub("launch", cmd_launch, "start a chain's next item (cloud: print create_session args)")
    s.add_argument("fleet"); s.add_argument("chain")
    s.add_argument("--first", action="store_true"); s.add_argument("--dry-run", action="store_true")
    s = sub("prompt", cmd_prompt, "print the chain or watchdog prompt")
    s.add_argument("which", choices=["chain", "watchdog"]); s.add_argument("fleet")
    s.add_argument("--chain"); s.add_argument("--first", action="store_true")
    s = sub("claim", cmd_claim, "claim a chain's next item (exit 4: closed as a wait)")
    s.add_argument("fleet"); s.add_argument("chain"); s.add_argument("--session", required=True)
    s = sub("close", cmd_close, "close a running item")
    s.add_argument("fleet"); s.add_argument("chain"); s.add_argument("item")
    s.add_argument("--status", required=True, choices=["done", "blocked", "ready"])
    s.add_argument("--note"); s.add_argument("--commits", nargs="*"); s.add_argument("--merged")
    s.add_argument("--force", action="store_true")
    s = sub("set", cmd_set, "set an item's status or note, or add a need")
    s.add_argument("fleet"); s.add_argument("chain"); s.add_argument("item")
    s.add_argument("--status", choices=ITEM_STATUSES); s.add_argument("--note")
    s.add_argument("--add-need", action="append")
    s = sub("add-item", cmd_add_item, "queue a new work item (a fix, or rework after a decision)")
    s.add_argument("fleet"); s.add_argument("chain"); s.add_argument("id")
    s.add_argument("--title", required=True); s.add_argument("--brief")
    s.add_argument("--need", action="append"); s.add_argument("--before-integrate", action="store_true")
    s = sub("picks", cmd_picks, "sibling commits an item's needs name, not yet on HEAD")
    s.add_argument("fleet"); s.add_argument("chain"); s.add_argument("item")
    s = sub("record-pick", cmd_record_pick, "record cherry-picked commits on an item")
    s.add_argument("fleet"); s.add_argument("chain"); s.add_argument("item")
    s.add_argument("--from", dest="source", required=True); s.add_argument("--commits", nargs="+", required=True)
    for name, fn, h in (("conflict", cmd_conflict, "append a conflicts.jsonl line"),
                        ("hold", cmd_hold, "append a release blocker line")):
        s = sub(name, fn, h)
        s.add_argument("fleet"); g = s.add_mutually_exclusive_group(required=True)
        g.add_argument("--data"); g.add_argument("--file")
    s = sub("conflicts", cmd_conflicts, "latest state of every conflict")
    s.add_argument("fleet"); s.add_argument("--open", action="store_true")
    s = sub("put", cmd_put, "write a Markdown file (handoff, owner decisions) to the fleet directory")
    s.add_argument("fleet"); s.add_argument("name"); s.add_argument("--file", required=True)
    s = sub("get", cmd_get, "print a file from the fleet directory")
    s.add_argument("fleet"); s.add_argument("name")
    s = sub("message", cmd_message, "report upward (local: inbox; cloud: send_message args)")
    s.add_argument("fleet"); s.add_argument("chain")
    s.add_argument("kind", choices=["started", "blocked", "conflict", "ready", "merged", "shared", "finished"])
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--text"); g.add_argument("--file")
    s = sub("inbox", cmd_inbox, "local transport: unread chain messages")
    s.add_argument("fleet"); s.add_argument("--peek", action="store_true"); s.add_argument("--all", action="store_true")
    s = sub("alive", cmd_alive, "is a session still running (local: exit 0 alive, 1 dead)")
    s.add_argument("session")
    s = sub("gate", cmd_gate, "run the configured quick or full gate, or the regenerators")
    s.add_argument("which", choices=["quick", "full", "regenerate"])
    sub("session", cmd_session, "this session's id (local transport)")

    a = p.parse_args(argv)
    try:
        a.fn(a)
    except FleetError as e:
        print(f"fleet.py {a.cmd}: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
