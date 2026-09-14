"""`hamilton check` -- the verification gate.

Reads `spec/requirements.md`, `spec/actors.md` and `.hamilton/config`, runs the
project's test command, scans the configured test paths for `@covers
R-nnnn/ACn` tags, and compares every acceptance criterion against
`.hamilton/verified` (the hashes recorded the last time `check` passed).

The model is one tree (D-014): `spec/requirements.md`, whose interior nodes are
the architecture and carry an `Interface:`. `spec/actors.md` is a flat list.
There is no components/modules model. Rules:

  no-test-command    .hamilton/config has no (or a blank) test_command
  tests-failed       test_command ran and did not exit 0
  uncovered          an AC has no @covers tag in a file under test_paths
  orphan-tag         a tag names a requirement or AC that does not exist
  orphan-requirement a requirement with no Parent and no Actor (a root must
                     name the actor whose goal it is)
  dangling-ref       a Parent or Actor value names no such entity
  cyclic-parent      a requirement's Parent chain loops
  stale              an AC's text changed since check last passed
  malformed          a requirement has no ACs, no Statement, a repeated id,
                     or an unparseable line

Every problem in a run is reported, not just the first. On a fully clean run
with at least one requirement, `check` rewrites `.hamilton/verified` and exits
0. Exit 1 on any finding; exit 2 when it cannot run at all (`spec/requirements.md`
or `.hamilton/config` missing). `--json` emits
{"ok": bool, "findings": [...], "warnings": [...], "notices": [...],
"requirements": int, "acceptance_criteria": int} or {"error": "..."}.
`notices` flag config that is set but does nothing (e.g. `mutation_command`,
which is reserved and unimplemented); they never change the exit code.

Advisory **warnings** never change the exit code and never fail an existing
project:

  long-statement    a Statement over 20 words -- it is several requirements
                    welded together; split it and push detail into ACs
  long-description  an Actor Description that is more than one sentence
  no-interface      an interior requirement (has children) with no `Interface:`
                    line -- expected while a subsystem is still being decomposed

The finding messages are the tool's real interface: the primary reader is an
agent repairing a mistake it just made, so each one states where, which rule
fired, what was expected, what was found, and the concrete next action.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import unicodedata

REQ_REL = "spec/requirements.md"
CONFIG_REL = ".hamilton/config"
VERIFIED_REL = ".hamilton/verified"
KNOWN_FIELDS = {"Parent", "Actor", "Statement", "Criteria", "Interface"}
# Fields a past model used; recognised and ignored so an older `requirements.md`
# still parses (D-014). Not stored, not flagged.
RETIRED_FIELDS = {"Component"}
TAG_RE = re.compile(r"@covers\s+(R-\d{4})/(AC\d+)\b")

STATEMENT_WORD_LIMIT = 20
# a sentence terminator with real text on both sides -> a second sentence;
# `\w{2,}` before the dot skips abbreviations like "e.g." / "U.S."
_SECOND_SENTENCE_RE = re.compile(r"\w{2,}[.!?]['\")\]]?\s+[A-Z(\[]")


class UsageError(Exception):
    """Missing spec or config file -> exit 2."""


def normalize(text: str) -> str:
    """Canonical form of an AC string, used for hashing. It defines when an AC
    counts as changed: an edit that survives normalisation changes the hash;
    one that does not is cosmetic.

      1. Unicode NFC, so canonically-equivalent forms hash identically.
      2. Every run of whitespace -- ASCII or any Unicode whitespace --
         collapses to a single ASCII space.
      3. Leading and trailing whitespace is removed.
      4. Case is preserved: a case-only edit is a real edit.
      5. Punctuation is preserved: a trailing "." can be a real edit.
    """
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def sha(text: str) -> str:
    """"sha256:" + the SHA-256 of ``normalize(text)``, UTF-8 encoded."""
    return "sha256:" + hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def read_config(root: str) -> dict:
    """Parse `.hamilton/config` into {key: (value, lineno)}.

    `key=value` per line; a line whose first non-blank char is `#` is a
    comment; the value is the literal remainder of the line after the first
    `=`. Raises UsageError (exit 2) if the file is absent.
    """
    path = os.path.join(root, CONFIG_REL)
    if not os.path.isfile(path):
        raise UsageError(f"{CONFIG_REL}: not found (run `hamilton init`, or "
                         f"create it with test_command and test_paths lines)")
    cfg = {}
    with open(path, "r", encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            if line.lstrip().startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            cfg[key.strip()] = (value.rstrip("\n"), n)
    return cfg


def run_tests(root: str, cfg: dict):
    """Run test_command in ``root``. Returns (ok, detail, lineno). The command's
    own output is redirected to stderr so `--json` stdout stays clean."""
    entry = cfg.get("test_command")
    if entry is None or not entry[0].strip():
        return False, "test_command is not set in .hamilton/config", (entry[1] if entry else 1)
    cmd, lineno = entry[0], entry[1]
    print(f"hamilton check: running test_command: {cmd.strip()}", file=sys.stderr)
    try:
        p = subprocess.run(cmd, shell=True, cwd=root, stdout=sys.stderr, timeout=1800)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"test_command could not be run ({exc})", lineno
    if p.returncode == 0:
        return True, "", lineno
    return False, f"test_command {cmd.strip()!r} exited {p.returncode}", lineno


def extract(path: str):
    """Tolerant line matcher (not a parser). Returns (reqs, duplicates, malformed).

    reqs       -- {R-id: {title, statement, statement_line, parent, actor,
                          interface, open_line, acs:{AC-id:{text,line}}}}
                  title/parent/actor/interface are None when absent. A
                  `Component:` line (a retired field) is recognised and ignored.
    duplicates -- [(R-id, line, first_line)]   -- the same `## R-nnnn` twice
    malformed  -- [(R-id, line, reason)]       -- reason is a full agent-facing sentence

    Lines inside a fenced code block (``` or ~~~) are ignored, so the commented
    example that `hamilton init` writes is not parsed as a real requirement.
    """
    with open(path, "r", encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    reqs, duplicates, malformed, cur, in_fence = {}, [], [], None, False
    fields = ", ".join(sorted(KNOWN_FIELDS))

    for n, raw in enumerate(lines, 1):
        stripped = raw.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence or not stripped:
            continue
        head = re.match(r"(#{1,6})\s+(.*)$", stripped)
        if head:
            rid = re.match(r"(R-\d{4})\b", head.group(2).strip())
            if rid and head.group(1) == "##":
                cur = rid.group(1)
                if cur in reqs:
                    duplicates.append((cur, n, reqs[cur]["open_line"]))
                else:
                    title = head.group(2).strip()[len(cur):].strip().strip('"').strip()
                    reqs[cur] = {"title": title or None, "statement": None,
                                 "statement_line": None, "parent": None,
                                 "actor": None, "interface": None,
                                 "open_line": n, "acs": {}}
            elif cur is not None:
                malformed.append((cur, n,
                    f"unexpected heading {stripped!r} while inside {cur}. "
                    f"Expected: the only headings in {REQ_REL} are '## R-nnnn' "
                    f"requirement openers. Found: a heading at another level, "
                    f"or '## ' not followed by an R-nnnn id. Fix: if it opens "
                    f"a new requirement write it as '## R-nnnn'; otherwise drop "
                    f"the leading '#'(s) or delete the line."))
            continue
        if cur is None:
            continue  # prose before the first requirement is ignored
        ac = re.match(r"-\s+(AC\d+):\s?(.*)$", stripped)
        if ac:
            acid = ac.group(1)
            if acid in reqs[cur]["acs"]:
                first = reqs[cur]["acs"][acid]["line"]
                malformed.append((cur, n,
                    f"{cur} defines {acid} twice. Expected: each AC id appears "
                    f"once within its requirement. Found: {acid} first defined "
                    f"at {REQ_REL}:{first}, redefined here -- the later text "
                    f"would silently win. Fix: renumber this criterion, or "
                    f"merge it into the first {acid}."))
            else:
                reqs[cur]["acs"][acid] = {"text": ac.group(2).strip(), "line": n}
            continue
        field = re.match(r"([A-Za-z][\w -]*?):\s?(.*)$", stripped)
        if field and field.group(1) in RETIRED_FIELDS:
            continue  # recognised, ignored (D-014)
        if field and field.group(1) in KNOWN_FIELDS:
            key, val = field.group(1), field.group(2).strip()
            if key == "Statement":
                reqs[cur]["statement"] = val
                reqs[cur]["statement_line"] = n
            elif key == "Parent":
                m = re.match(r"(R-\d{4})", val)
                reqs[cur]["parent"] = m.group(1) if m else (val or None)
            elif key == "Actor":
                m = re.match(r"(A-\d{4})", val)
                reqs[cur]["actor"] = m.group(1) if m else (val or None)
            elif key == "Interface":
                reqs[cur]["interface"] = val or None
            continue
        if stripped.startswith("- "):
            malformed.append((cur, n,
                f"malformed acceptance criterion inside {cur}: a '- ' bullet "
                f"that is not '- AC<n>: <condition> -> <outcome>'. Expected: "
                f"the label 'AC' in capitals, one or more digits, ': ', then "
                f"the criterion text. Found: a bullet that does not match. "
                f"Fix: rewrite it as '- AC<n>: ... -> ...', or remove the "
                f"leading '- ' if it is not a criterion."))
        else:
            malformed.append((cur, n,
                f"unparseable line inside {cur}. Expected: a '## R-nnnn' "
                f"heading, a 'Key: value' field ({fields}), or a "
                f"'- AC<n>: <condition> -> <outcome>' criterion. Found: a "
                f"non-blank line matching none of these. Fix: reword it to a "
                f"recognised field or criterion, fold it into the Statement, "
                f"or delete it. Note: each field must be a single line -- a "
                f"wrapped continuation lands here."))
    return reqs, duplicates, malformed


def _iter_files(root: str):
    """Yield repo-relative paths, honouring .gitignore when inside a git repo."""
    try:
        r = subprocess.run(
            ["git", "-C", root, "ls-files", "--others", "--cached",
             "--exclude-standard", "-z"],
            capture_output=True, timeout=15)
        if r.returncode == 0:
            yield from (p for p in r.stdout.decode("utf-8", "replace").split("\0") if p)
            return
    except (OSError, subprocess.SubprocessError):
        pass
    for dpath, dnames, fnames in os.walk(root):
        dnames[:] = [d for d in dnames if d != ".git"]
        for fn in fnames:
            yield os.path.relpath(os.path.join(dpath, fn), root)


def scan(root: str, test_paths: str):
    """Return [(R-id, AC-id, relpath, line)] for `@covers R-nnnn/ACn` tags found
    in files under one of the ``test_paths`` prefixes (space-separated, relative
    to root). A tag anywhere else -- README, the implementation, a notes file --
    does not count. Also skips .git/, spec/, .hamilton/, files over 2 MB, and
    git-ignored paths. The tag is matched as raw text, so any comment syntax in
    any language works.
    """
    prefixes = [p.replace("\\", "/").strip("/") for p in test_paths.split() if p.strip()]
    hits = []
    if not prefixes:
        return hits
    for rel in _iter_files(root):
        r = rel.replace("\\", "/")
        if r.split("/", 1)[0] in ("spec", ".hamilton", ".git"):
            continue
        if not any(r == p or r.startswith(p + "/") for p in prefixes):
            continue
        full = os.path.join(root, rel)
        try:
            if os.path.getsize(full) > 2_000_000:
                continue
            with open(full, "r", encoding="utf-8") as fh:
                for i, line in enumerate(fh, 1):
                    for m in TAG_RE.finditer(line):
                        hits.append((m.group(1), m.group(2), rel, i))
        except (OSError, UnicodeDecodeError):
            continue
    return hits


def read_verified(root: str) -> dict:
    """{"R-nnnn/ACn": "sha256:..."} from `.hamilton/verified`, one `id hash`
    per line. This is the state left by the last passing `check`; a missing or
    empty file means no run has passed yet, so nothing is stale."""
    path = os.path.join(root, VERIFIED_REL)
    out = {}
    if not os.path.isfile(path):
        return out
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            parts = line.split()
            if len(parts) == 2:
                out[parts[0]] = parts[1]
    return out


def write_verified(root: str, reqs: dict) -> None:
    """Record the current hash of every AC, sorted by id for a stable diff.
    Called only on a fully clean run. Commit this file so staleness is
    meaningful on other machines and in CI."""
    lines = sorted(f"{rid}/{acid} {sha(ac['text'])}"
                   for rid, r in reqs.items() for acid, ac in r["acs"].items())
    with open(os.path.join(root, VERIFIED_REL), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + ("\n" if lines else ""))


def _sample(ids, limit=8):
    """A bounded, sorted preview of an id collection for a finding message."""
    ids = sorted(ids)
    if not ids:
        return "none"
    if len(ids) <= limit:
        return ", ".join(ids)
    return ", ".join(ids[:limit]) + f", ... ({len(ids)} total)"


def _finding(rule, detail, file, line, req=None, ac=None):
    message = f"{file}:{line}: {rule}: {detail}"
    return {"rule": rule, "file": file, "line": line, "req": req, "ac": ac,
            "message": message}


def _warning(rule, detail, file, line):
    """Advisory only: never counted as a finding, never changes the exit code.
    The `warning:` word in the message keeps it distinct from a finding."""
    return {"rule": rule, "file": file, "line": line,
            "message": f"{file}:{line}: warning: {rule}: {detail}"}


def _one_sentence(text: str) -> bool:
    collapsed = " ".join(text.split())
    return not _SECOND_SENTENCE_RE.search(collapsed)


def _config_notices(cfg: dict) -> list:
    """Config that is set but does nothing yet. A line that looks active and is
    silently ignored is the failure the falsification ledger had, so say so
    every run."""
    out = []
    mc = cfg.get("mutation_command")
    if mc is not None and mc[0].strip():
        out.append(
            f"{CONFIG_REL}:{mc[1]}: mutation_command is set "
            f"({mc[0].strip()!r}) but mutation testing is not implemented -- it "
            f"is NOT being run and the gate does not depend on it. Unset it "
            f"until Hamilton wires it up.")
    return out


def collect_warnings(root: str, reqs: dict, actors=None):
    """The advisory checks (see module docstring). Kept apart from `run` so a
    caller can ask for them without the gate; `run` passes the actors it has
    already parsed so `spec/actors.md` is not re-read. Returns [warning...]."""
    from hamilton_core import model  # local: model imports this module
    actors = model.parse_actors(root) if actors is None else actors
    has_child = {r["parent"] for r in reqs.values() if r["parent"] in reqs}

    out = []
    for rid, r in reqs.items():
        stmt = r["statement"]
        if stmt:
            words = len(stmt.split())
            if words > STATEMENT_WORD_LIMIT:
                out.append(_warning("long-statement",
                    f"{rid}'s Statement is {words} words. A Statement is one "
                    f"sentence, under {STATEMENT_WORD_LIMIT} words, describing "
                    f"one behaviour. At this length it is several requirements "
                    f"welded together, which makes the spec unreviewable. Fix: "
                    f"split it into separate '## R-nnnn' requirements and move "
                    f"the detail down into acceptance criteria, where the gate "
                    f"can act on it (SKILL.md, Specify).",
                    REQ_REL, r["statement_line"] or r["open_line"]))
        if rid in has_child and not r["interface"]:
            out.append(_warning("no-interface",
                f"{rid} has child requirements, so it is a subsystem boundary, "
                f"but no 'Interface:' line. Expected: an 'Interface:' naming "
                f"what crosses the boundary this requirement owns -- that is "
                f"the integration-test surface. Found: none. "
                f"Fix: add an 'Interface:' line once the boundary is settled; "
                f"before then this is expected.",
                REQ_REL, r["open_line"]))

    for aid, a in actors.items():
        desc = a.get("description")
        if desc and not _one_sentence(desc):
            out.append(_warning("long-description",
                f"the Description of actor {aid} is more than one sentence. An "
                f"Actor Description is a single sentence: the external role and "
                f"what it needs from the system. Fix: cut it to one sentence.",
                model.ACTORS_REL, a["line"]))

    out.sort(key=lambda w: (w["file"], w["line"], w["rule"]))
    return out


def run(root: str):
    """Returns (findings, warnings, notices, requirement_count, ac_count).
    `warnings` are advisory (module docstring); `notices` flag configuration
    that is set but does nothing. Neither changes the exit code."""
    if not os.path.isfile(os.path.join(root, REQ_REL)):
        raise UsageError(f"{REQ_REL}: not found (run hamilton check from the "
                         f"project root, the directory that holds spec/)")
    cfg = read_config(root)
    notices = _config_notices(cfg)

    reqs, duplicates, malformed = extract(os.path.join(root, REQ_REL))
    n_reqs = len(reqs)
    n_acs = sum(len(r["acs"]) for r in reqs.values())

    if not reqs:
        return ([_finding("malformed",
            "spec/requirements.md defines no requirements. Expected: at least "
            "one '## R-nnnn' block -- with a Statement and acceptance criteria "
            "-- outside any fenced code block. Found: only the fenced example, "
            "or an empty file. Fix: write a real requirement below the "
            "example, then re-run hamilton check.",
            REQ_REL, 1)], [], notices, n_reqs, n_acs)

    out = []
    from hamilton_core import model as _model   # local: model imports this module
    actors = _model.parse_actors(root)
    warnings = collect_warnings(root, reqs, actors)

    tc = cfg.get("test_command")
    if tc is None or not tc[0].strip():
        out.append(_finding("no-test-command",
            f"{CONFIG_REL} has {'no' if tc is None else 'a blank'} "
            f"test_command, so the gate has no suite to run and cannot certify "
            f"anything. Expected: a 'test_command=<command>' line naming what "
            f"runs the project's tests (exit 0 on success). Found: "
            f"{'the key is absent' if tc is None else 'the value is empty'}. "
            f"Fix: set test_command in {CONFIG_REL}, e.g. "
            f"'test_command=python -m pytest -q'.",
            CONFIG_REL, tc[1] if tc else 1))
    else:
        ok, detail, cfg_line = run_tests(root, cfg)
        if not ok:
            cmd = tc[0].strip()
            out.append(_finding("tests-failed",
                f"{detail}. Expected: the project's own test suite to pass "
                f"before the gate certifies anything. Found: it did not. Fix: "
                f"run '{cmd}' yourself from the project root to see why it "
                f"fails and repair the implementation or the test; or "
                f"set/correct test_command in {CONFIG_REL}.",
                CONFIG_REL, cfg_line))

    for rid, line, first in duplicates:
        out.append(_finding("malformed",
            f"{rid} is declared a second time at this line. Expected: each "
            f"'## R-nnnn' id appears once in {REQ_REL}; ids are allocated once "
            f"and never reused. Found: {rid} was first declared at "
            f"{REQ_REL}:{first}. Fix: give one of the two a fresh unused id "
            f"and repoint its @covers tags.",
            REQ_REL, line, req=rid))

    for rid, line, reason in malformed:
        out.append(_finding("malformed", reason, REQ_REL, line, req=rid))

    for rid, r in reqs.items():
        if r["statement"] is None:
            out.append(_finding("malformed",
                f"{rid} has no Statement. Expected: a 'Statement: <what shall "
                f"be true>' line between '## {rid}' and its criteria -- the "
                f"requirement has to say what it requires (concept 4.1). "
                f"Found: none. Fix: add a Statement line.",
                REQ_REL, r["open_line"], req=rid))
        if not r["acs"]:
            out.append(_finding("malformed",
                f"{rid} has no acceptance criteria. Expected: at least one "
                f"'- AC<n>: <observable condition> -> <expected outcome>' line "
                f"before the next '##' heading or end of file. Found: none. "
                f"Fix: add one or more '- AC1: ... -> ...' lines under {rid}.",
                REQ_REL, r["open_line"], req=rid))

    # --- requirement tree (D-014) ---
    # actors were parsed once near the top of run().
    for rid, r in reqs.items():
        par, act = r["parent"], r.get("actor")

        if par is not None and par not in reqs:
            out.append(_finding("dangling-ref",
                f"{rid} has 'Parent: {par}', which is not a declared "
                f"requirement. Expected: Parent names a '## R-nnnn' heading in "
                f"{REQ_REL}. Found: {REQ_REL} declares {_sample(reqs)}. Fix: "
                f"correct the Parent, or add '## {par}'.",
                REQ_REL, r["open_line"], req=rid))

        if act is not None and re.fullmatch(r"A-\d{4}", act) and act not in actors:
            have = _sample(actors) if actors else f"{_model.ACTORS_REL} declares none"
            out.append(_finding("dangling-ref",
                f"{rid} has 'Actor: {act}', which is not declared. Expected: "
                f"Actor names a '## A-nnnn' block in {_model.ACTORS_REL}. Found: "
                f"{have}. Fix: correct the Actor, or add '## {act}'.",
                REQ_REL, r["open_line"], req=rid))

        if par is None and not act:
            out.append(_finding("orphan-requirement",
                f"{rid} has no Parent, so it is a system goal -- and a system "
                f"goal must name the actor whose goal it is. Expected: an "
                f"'Actor: A-nnnn' line, or a 'Parent:' line making {rid} a "
                f"child of another requirement. Found: neither. Fix: add the "
                f"actor (spec/actors.md), or give it a parent (D-014).",
                REQ_REL, r["open_line"], req=rid))

    for rid in reqs:
        chain, node = [], rid
        while node in reqs and node not in chain:
            chain.append(node)
            node = reqs[node]["parent"]
        if node in chain:
            loop = chain[chain.index(node):] + [node]
            out.append(_finding("cyclic-parent",
                f"the Parent chain of {rid} forms a cycle: "
                f"{' -> '.join(loop)}. Expected: following Parent links always "
                f"reaches a root. Found: a loop. Fix: re-point one Parent so "
                f"the chain terminates.",
                REQ_REL, reqs[rid]["open_line"], req=rid))
            break

    test_paths = cfg.get("test_paths", ("", 0))[0]
    covered = set()
    for req, ac, file, line in scan(root, test_paths):
        if req not in reqs:
            out.append(_finding("orphan-tag",
                f"the tag '@covers {req}/{ac}' names requirement {req}, which "
                f"does not exist. Expected: every tag references a '## R-nnnn' "
                f"heading in {REQ_REL}. Found: {REQ_REL} declares "
                f"{_sample(reqs)}. Fix: correct the tag to an existing "
                f"requirement id, or add '## {req}' in spec phase.",
                file, line, req=req, ac=ac))
        elif ac not in reqs[req]["acs"]:
            out.append(_finding("orphan-tag",
                f"the tag '@covers {req}/{ac}' names criterion {ac}, which "
                f"{req} does not define. Expected: {ac} listed as a "
                f"'- {ac}: ...' line under '## {req}'. Found: {req} defines "
                f"{_sample(reqs[req]['acs'])}. Fix: point the tag at one of "
                f"those, or add '- {ac}: <condition> -> <outcome>' under {req}.",
                file, line, req=req, ac=ac))
        else:
            covered.add((req, ac))

    verified = read_verified(root)
    has_paths = bool(test_paths.split())
    for rid, r in reqs.items():
        for acid, ac in sorted(r["acs"].items()):
            qual = f"{rid}/{acid}"
            if (rid, acid) not in covered:
                where = (f"no file under test_paths ({test_paths}) contains it"
                         if has_paths else
                         f"test_paths is not set in {CONFIG_REL}, so no tag "
                         f"can count")
                out.append(_finding("uncovered",
                    f"{qual} has an acceptance criterion with no test claiming "
                    f"it. Expected: a comment '@covers {qual}' in a file under "
                    f"test_paths (any language -- the tag text is matched, not "
                    f"the comment syntax). Found: {where}. Fix: add "
                    f"'@covers {qual}' to the test that exercises this "
                    f"criterion.",
                    REQ_REL, ac["line"], req=rid, ac=acid))
            want, seen = sha(ac["text"]), verified.get(qual)
            if seen is not None and seen != want:
                out.append(_finding("stale",
                    f"{qual} was reworded since hamilton check last passed. "
                    f"Expected: the AC text to still hash to {seen} (recorded "
                    f"in {VERIFIED_REL} at the last green run). Found: it now "
                    f"hashes to {want}. Its test and implementation may no "
                    f"longer match what it says. Fix: re-check the "
                    f"implementation and the '@covers {qual}' test against the "
                    f"new wording; a clean hamilton check records the new hash.",
                    REQ_REL, ac["line"], req=rid, ac=acid))

    out.sort(key=lambda f: (f["file"] or "", f["line"] or 0, f["rule"]))
    # Re-record the AC hashes whenever nothing but `stale` is outstanding: the
    # tests pass, every AC is covered, the spec parses. `stale` is then a
    # single red run after an AC edit -- it forces one more `hamilton check`
    # (which re-runs the suite against the new wording) and then clears.
    if not [f for f in out if f["rule"] != "stale"]:
        write_verified(root, reqs)
    return out, warnings, notices, n_reqs, n_acs


def main(as_json: bool = False) -> int:
    try:
        findings, warnings, notices, n_reqs, n_acs = run(os.getcwd())
    except UsageError as exc:
        if as_json:
            print(json.dumps({"error": str(exc)}))
        else:
            print(f"hamilton check: {exc}", file=sys.stderr)
        return 2
    if as_json:
        print(json.dumps({"ok": not findings, "findings": findings,
                          "warnings": warnings, "notices": notices,
                          "requirements": n_reqs,
                          "acceptance_criteria": n_acs}))
    else:
        for f in findings:
            print(f["message"])
        for w in warnings:
            print(w["message"], file=sys.stderr)
        for n in notices:
            print(f"hamilton check: notice: {n}", file=sys.stderr)
        noun = "criterion" if n_acs == 1 else "criteria"
        print(f"hamilton check: {n_reqs} requirement(s), {n_acs} acceptance {noun}",
              file=sys.stderr)
        if warnings:
            print(f"hamilton check: {len(warnings)} warning(s) — advisory, "
                  f"not failures", file=sys.stderr)
        print(f"hamilton check: {'ok' if not findings else str(len(findings)) + ' problem(s)'}",
              file=sys.stderr)
    return 1 if findings else 0
