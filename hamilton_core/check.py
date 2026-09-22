"""`hamilton check` -- the verification gate.

Reads `spec/requirements.md`, `spec/actors.md` and `.hamilton/config`, runs the
project's test command, scans the configured method paths for `@covers
R-nnnn/ACn` tags, and checks that every tag that counts carries a current
review suffix (D-020). `check` is read-only: it never writes a file.

A review suffix -- `@covers R-0005/AC2 #3f9a2c.81d0e4` -- is written only by
`hamilton review` when its reviewer passes the test. It is two 6-hex-digit
SHA-256 prefixes: the *obligation* (the AC id, its requirement's Statement,
the AC text with marker, and the definitions of the methods under whose paths
the test lies) and the *test* (the tag's region: the file's preamble plus the
tag's section, see `regions`). A change to either side leaves the suffix out of
date, and the tag unreviewed.

The model is one tree (D-014): `spec/requirements.md`, headed by a
`## Verification methods` section. Every AC ends in a marker naming how it is
verified (`[browser]`, `[unit, http]`); a test for it counts only under the
`paths.<method>` directories of one of those methods (D-019).
`spec/actors.md` is a flat list. Rules:

  no-test-command    .hamilton/config has no (or a blank) test_command
  tests-failed       test_command ran and did not exit 0
  retired-config     .hamilton/config still sets test_paths
  no-method          an AC has no `[method]` marker
  unknown-method     a marker names a method `## Verification methods` does
                     not define
  no-method-paths    a method in use has no `paths.<method>` in the config
  uncovered          an AC's method has no @covers tag under its paths (each
                     method of a multi-method AC needs its own); `manual`
                     needs none
  wrong-method       an AC is tagged, but under none of its methods' paths
  orphan-tag         a tag names a requirement or AC that does not exist
  orphan-requirement a requirement with no Parent and no Actor (a root must
                     name the actor whose goal it is)
  dangling-ref       a Parent or Actor value names no such entity
  cyclic-parent      a requirement's Parent chain loops
  unreviewed         a counting tag has no review suffix, or its AC or its
                     test changed since the review (one finding per tag)
  malformed          a requirement has no ACs, no Statement, a repeated id,
                     or an unparseable line

Every problem in a run is reported, not just the first. Exit 0 on a clean run
with at least one requirement, 1 on any finding; exit 2 when it cannot run at
all (`spec/requirements.md` or `.hamilton/config` missing). `--json` emits
{"ok": bool, "findings": [...], "warnings": [...], "notices": [...],
"manual": ["R-nnnn/ACn", ...], "requirements": int,
"acceptance_criteria": int} or {"error": "..."}. `manual` lists the criteria a
person verifies, which the gate does not. `notices` flag config that is set
but does nothing (e.g. `mutation_command`, which is reserved and
unimplemented) and files nothing reads any more (`.hamilton/verified`); they
never change the exit code.

Advisory **warnings** never change the exit code and never fail an existing
project:

  long-statement    a Statement over 20 words -- it is several requirements
                    welded together; split it and push detail into ACs
  long-description  an Actor Description that is more than one sentence
  root-unit-only    a root requirement whose ACs are all `unit` -- nothing
                    verifies the actor's goal end to end

A person reads the gate as the spec: every requirement, each criterion
under it with a mark (`view`), then the suite's result, then whatever is
about no one criterion. The suite's own output is not shown -- it goes, as it
runs, into a temp file named on failure (a green suite's is deleted), and its
end travels with the `tests-failed` finding; `--suite-output` streams it
instead.

The finding messages -- in `--json`, and handed to the agents `hamilton
build` runs -- are the tool's real interface to an agent: each states where,
which rule fired, what was expected, what was found, and the concrete next
action.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from typing import NamedTuple

REQ_REL = "spec/requirements.md"
CONFIG_REL = ".hamilton/config"
# retired by D-020; a leftover file only earns a notice
RETIRED_VERIFIED_REL = ".hamilton/verified"
KNOWN_FIELDS = {"Parent", "Actor", "Statement", "Criteria"}
# Fields a past model used; recognised and ignored so an older `requirements.md`
# still parses (D-014, D-019). Not stored, not flagged.
RETIRED_FIELDS = {"Component", "Interface"}
# `@covers R-0005/AC2`, optionally followed by its review suffix `#3f9a2c.81d0e4`
TAG_RE = re.compile(r"@covers\s+(R-\d{4})/(AC\d+)\b(?:\s+#(\S+))?")

# the review state of a counting tag (D-020)
REVIEWED = "reviewed"
NEVER_REVIEWED = "no review yet"
AC_CHANGED = "AC changed"
TEST_CHANGED = "test changed"
BOTH_CHANGED = "AC and test changed"

METHODS_HEADING = "Verification methods"
# a method needs no tag: a person verifies it, the gate only lists it
MANUAL = "manual"
_METHOD_NAME = r"[a-z][a-z0-9-]*"
# `- **browser** — the running site in a real browser`
_METHOD_DEF_RE = re.compile(rf"-\s+\*\*({_METHOD_NAME})\*\*\s*[—–:-]\s*(.+)$")
# the trailing `[browser]` / `[unit, http]` of an AC
_METHOD_MARKER_RE = re.compile(
    rf"\[\s*({_METHOD_NAME}(?:\s*,\s*{_METHOD_NAME})*)\s*\]$")
PATHS_PREFIX = "paths."

STATEMENT_WORD_LIMIT = 20
# a sentence terminator with real text on both sides -> a second sentence;
# `\w{2,}` before the dot skips abbreviations like "e.g." / "U.S."
_SECOND_SENTENCE_RE = re.compile(r"\w{2,}[.!?]['\")\]]?\s+[A-Z(\[]")


class UsageError(Exception):
    """Missing spec or config file -> exit 2."""


class Tag(NamedTuple):
    """One `@covers` tag: where it is, and its review suffix without the `#`
    (None when it has none)."""
    rid: str
    acid: str
    file: str
    line: int
    suffix: str | None


def normalize(text: str) -> str:
    """Canonical form of hashed text -- an obligation or a test region. It
    defines when either counts as changed: an edit that survives normalisation
    changes the hash; one that does not is cosmetic.

      1. Unicode NFC, so canonically-equivalent forms hash identically.
      2. Every run of whitespace -- ASCII or any Unicode whitespace --
         collapses to a single ASCII space.
      3. Leading and trailing whitespace is removed.
      4. Case is preserved: a case-only edit is a real edit.
      5. Punctuation is preserved: a trailing "." can be a real edit.
    """
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def digest(text: str) -> str:
    """The first 6 hex digits of the SHA-256 of ``normalize(text)``, UTF-8
    encoded: one half of a review suffix."""
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()[:6]


def obligation(rid: str, acid: str, req: dict, methods, defined: dict) -> str:
    """The obligation half of a review suffix: what a test for ``rid/acid``
    owes. It covers the AC id, the requirement's Statement, the AC text with
    its marker, and the definition of each of ``methods`` -- the AC's methods
    under whose paths the test lies -- sorted by name. Renumbering an AC,
    rewording it or its Statement, or redefining its method all change it."""
    parts = [f"{rid}/{acid}", req["statement"] or "", req["acs"][acid]["text"]]
    parts += [f"{m}: {defined[m]['description']}" for m in sorted(methods)]
    return digest("\n".join(normalize(p) for p in parts))


def strip_suffixes(line: str) -> str:
    """``line`` with the review suffix dropped from every tag on it."""
    return TAG_RE.sub(lambda m: f"@covers {m.group(1)}/{m.group(2)}", line)


def regions(lines) -> dict:
    """{tag line number: region text} for the lines of one file.

    Hamilton does not parse tests, so "the test" is a layout rule. A *tag
    block* is a run of consecutive lines that each hold a `@covers` tag. A
    tag's *region* is the file's preamble (every line before the first tag
    block) plus its own section: its tag block down to the line before the
    next tag block, or the end of the file. Review suffixes are stripped, so
    writing one never changes a region."""
    tagged = [bool(TAG_RE.search(line)) for line in lines]
    starts = [i for i, t in enumerate(tagged) if t and (i == 0 or not tagged[i - 1])]
    if not starts:
        return {}
    preamble = list(lines[:starts[0]])
    out = {}
    for k, start in enumerate(starts):
        stop = starts[k + 1] if k + 1 < len(starts) else len(lines)
        text = "\n".join(preamble + [strip_suffixes(ln) for ln in lines[start:stop]])
        end = start
        while end < stop and tagged[end]:
            out[end + 1] = text
            end += 1
    return out


def suffix(obligation_half: str, region: str) -> str:
    """The review suffix, without its `#`, for an obligation half and a region
    text."""
    return f"{obligation_half}.{digest(region)}"


def review_state(found: str | None, want: str) -> str:
    """How a tag's suffix ``found`` compares to the current one ``want``:
    REVIEWED, or which half no longer matches."""
    if found == want:
        return REVIEWED
    halves = (found or "").split(".")
    if len(halves) != 2:
        return NEVER_REVIEWED
    ob_ok, test_ok = (h == w for h, w in zip(halves, want.split(".")))
    if ob_ok:
        return TEST_CHANGED
    return AC_CHANGED if test_ok else BOTH_CHANGED


def read_config(root: str) -> dict:
    """Parse `.hamilton/config` into {key: (value, lineno)}.

    `key=value` per line; a line whose first non-blank char is `#` is a
    comment; the value is the literal remainder of the line after the first
    `=`. Raises UsageError (exit 2) if the file is absent.
    """
    path = os.path.join(root, CONFIG_REL)
    if not os.path.isfile(path):
        raise UsageError(f"{CONFIG_REL}: not found (run `hamilton init`, or "
                         f"create it with test_command and paths.<method> lines)")
    cfg = {}
    with open(path, "r", encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            if line.lstrip().startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            cfg[key.strip()] = (value.rstrip("\n"), n)
    return cfg


def method_paths(cfg: dict) -> dict:
    """{method: [dir, ...]} from the `paths.<method>` keys of the config: the
    directories where a test verifying that method lives. A blank value counts
    as unset."""
    out = {}
    for key, (value, _line) in cfg.items():
        dirs = [d.replace("\\", "/").strip("/") for d in value.split()]
        if key.startswith(PATHS_PREFIX) and dirs:
            out[key[len(PATHS_PREFIX):]] = dirs
    return out


def under(rel: str, dirs) -> bool:
    """True if repo-relative path ``rel`` lies in one of ``dirs``."""
    r = rel.replace("\\", "/")
    return any(r == d or r.startswith(d + "/") for d in dirs)


def missing_methods(methods, paths: dict, files) -> list:
    """The methods of an AC that none of its tags satisfies. A tag in ``files``
    satisfies a method when it lies under that method's ``paths``; `manual`
    needs no tag. A method without paths is always missing."""
    return [m for m in methods
            if m != MANUAL and not any(under(f, paths.get(m, ())) for f in files)]


SUITE_TAIL = 200         # lines of a failed suite's output a finding carries


SUITE_TIMEOUT = 1800     # seconds before a suite that never ends is stopped


def new_log() -> str:
    """A file for a suite's output -- outside the project, so the gate still
    never writes to it. Named before the suite starts, so it can be followed
    while it runs."""
    fd, path = tempfile.mkstemp(prefix="hamilton-suite-", suffix=".log")
    os.close(fd)
    return path


def follow_hint(log: str) -> str:
    """How to watch a suite that is running: its log, in the runner's own
    words."""
    return f"follow it: tail -f {log}"


def run_tests(root: str, cfg: dict, echo: bool = False, log: str | None = None):
    """Run test_command in ``root``. Returns (ok, detail, lineno, output).

    The suite's output is not shown: a person reading the gate wants to know
    *whether* it passed, and the details are for whoever fixes it. It goes,
    as it comes, into `log` (a new temp file if none is given), which anyone
    who wants to watch can follow. `echo` streams it to stderr instead, for
    CI logs and debugging."""
    entry = cfg.get("test_command")
    if entry is None or not entry[0].strip():
        return (False, "test_command is not set in .hamilton/config",
                (entry[1] if entry else 1), "")
    cmd, lineno = entry[0], entry[1]
    try:
        if echo:
            print(f"hamilton check: running test_command: {cmd.strip()}", file=sys.stderr)
            p = subprocess.run(cmd, shell=True, cwd=root, stdout=sys.stderr,
                               timeout=SUITE_TIMEOUT)
            output = ""
        else:
            log = log or new_log()
            with open(log, "w", encoding="utf-8") as fh:
                p = subprocess.Popen(cmd, shell=True, cwd=root, stdout=fh,
                                     stderr=subprocess.STDOUT)
                try:
                    p.wait(timeout=SUITE_TIMEOUT)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()
                    raise
            with open(log, encoding="utf-8", errors="replace") as fh:
                output = fh.read()
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"test_command could not be run ({exc})", lineno, ""
    if p.returncode == 0:
        return True, "", lineno, output
    return False, f"test_command {cmd.strip()!r} exited {p.returncode}", lineno, output


def spec_lines(path: str):
    """[(lineno, stripped)] for the non-blank lines of ``path`` outside fenced
    code blocks (``` or ~~~), so the commented example that `hamilton init`
    writes is never parsed as real content."""
    with open(path, "r", encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    out, in_fence = [], False
    for n, raw in enumerate(lines, 1):
        stripped = raw.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
        elif not in_fence and stripped:
            out.append((n, stripped))
    return out


def extract_methods(path: str) -> dict:
    """{method: {description, line}} from the `## Verification methods` section
    that precedes the first requirement. Each method is a bullet
    `- **name** — what is real and what is stubbed`; other lines in the
    section are prose and ignored."""
    methods, inside = {}, False
    for n, stripped in spec_lines(path):
        head = re.match(r"#{1,6}\s+(.*)$", stripped)
        if head:
            if re.match(r"R-\d{4}\b", head.group(1)):
                break
            inside = head.group(1).strip() == METHODS_HEADING
            continue
        m = _METHOD_DEF_RE.match(stripped) if inside else None
        if m and m.group(1) not in methods:
            methods[m.group(1)] = {"description": m.group(2).strip(), "line": n}
    return methods


def extract(path: str):
    """Tolerant line matcher (not a parser). Returns (reqs, duplicates, malformed).

    reqs       -- {R-id: {title, statement, statement_line, parent, actor,
                          open_line, acs:{AC-id:{text,methods,line}}}}
                  title/parent/actor are None when absent. An AC's text keeps
                  its method marker, so the hash covers it; `methods` is the
                  marker's names, empty without one. A retired field line
                  (`Component:`, `Interface:`) is recognised and ignored.
    duplicates -- [(R-id, line, first_line)]   -- the same `## R-nnnn` twice
    malformed  -- [(R-id, line, reason)]       -- reason is a full agent-facing sentence
    """
    reqs, duplicates, malformed, cur = {}, [], [], None
    fields = ", ".join(sorted(KNOWN_FIELDS))

    for n, stripped in spec_lines(path):
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
                                 "actor": None, "open_line": n, "acs": {}}
            elif cur is not None:
                malformed.append((cur, n,
                    f"unexpected heading {stripped!r} while inside {cur}. "
                    f"Expected: the only headings in {REQ_REL} after the first "
                    f"requirement are '## R-nnnn' requirement openers. Found: a "
                    f"heading at another level, or '## ' not followed by an "
                    f"R-nnnn id. Fix: if it opens a new requirement write it as "
                    f"'## R-nnnn'; if it is '## {METHODS_HEADING}', move it above "
                    f"the first requirement; otherwise drop the leading '#'(s) "
                    f"or delete the line."))
            continue
        if cur is None:
            continue  # prose and the methods section before the first requirement
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
                text = ac.group(2).strip()
                marker = _METHOD_MARKER_RE.search(text)
                methods = ([m.strip() for m in marker.group(1).split(",")]
                           if marker else [])
                reqs[cur]["acs"][acid] = {"text": text, "methods": methods,
                                          "line": n}
            continue
        field = re.match(r"([A-Za-z][\w -]*?):\s?(.*)$", stripped)
        if field and field.group(1) in RETIRED_FIELDS:
            continue  # recognised, ignored (D-014, D-019)
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
            continue
        if stripped.startswith("- "):
            malformed.append((cur, n,
                f"malformed acceptance criterion inside {cur}: a '- ' bullet "
                f"that is not '- AC<n>: <condition> -> <outcome> [method]'. "
                f"Expected: the label 'AC' in capitals, one or more digits, "
                f"': ', then the criterion text. Found: a bullet that does not "
                f"match. Fix: rewrite it as '- AC<n>: ... -> ... [method]', or "
                f"remove the leading '- ' if it is not a criterion."))
        else:
            malformed.append((cur, n,
                f"unparseable line inside {cur}. Expected: a '## R-nnnn' "
                f"heading, a 'Key: value' field ({fields}), or a "
                f"'- AC<n>: <condition> -> <outcome> [method]' criterion. "
                f"Found: a non-blank line matching none of these. Fix: reword "
                f"it to a recognised field or criterion, fold it into the "
                f"Statement, or delete it. Note: each field must be a single "
                f"line -- a wrapped continuation lands here."))
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


def read_lines(root: str, rel: str) -> list:
    """The lines of a text file, numbered as `scan` numbers them."""
    with open(os.path.join(root, rel), "r", encoding="utf-8") as fh:
        return fh.read().split("\n")


def scan(root: str, dirs):
    """Return [Tag] for the `@covers R-nnnn/ACn` tags found in files under one
    of ``dirs`` (relative to root, as `method_paths` gives them). A tag
    anywhere else -- README, the implementation, a notes file -- does not
    count. Also skips .git/, spec/, .hamilton/, files over 2 MB, and
    git-ignored paths. The tag is matched as raw text, so any comment syntax in
    any language works.
    """
    dirs = list(dirs)
    hits = []
    if not dirs:
        return hits
    for rel in _iter_files(root):
        r = rel.replace("\\", "/")
        if r.split("/", 1)[0] in ("spec", ".hamilton", ".git"):
            continue
        if not under(r, dirs):
            continue
        full = os.path.join(root, rel)
        try:
            if os.path.getsize(full) > 2_000_000:
                continue
            for i, line in enumerate(read_lines(root, rel), 1):
                for m in TAG_RE.finditer(line):
                    hits.append(Tag(m.group(1), m.group(2), rel, i, m.group(3)))
        except (OSError, UnicodeDecodeError):
            continue
    return hits


def counting_methods(ac: dict, file: str, defined: dict, paths: dict) -> list:
    """The methods of ``ac`` whose paths hold ``file``. A tag there counts
    toward those methods; with none it does not count at all."""
    return [m for m in ac["methods"]
            if m != MANUAL and m in defined and under(file, paths.get(m, ()))]


class Counted(NamedTuple):
    """A tag that counts toward coverage, with what its review covers."""
    tag: Tag
    methods: list      # the AC's methods the tag counts toward
    region: str        # the test text the review covers
    want: str          # the current review suffix, without its `#`

    @property
    def state(self) -> str:
        return review_state(self.tag.suffix, self.want)


def counted(root: str, reqs: dict, defined: dict, paths: dict, tags) -> list:
    """[Counted] for the ``tags`` that count: they name an existing AC and lie
    under the paths of one of its defined methods. Only these need a review;
    a `wrong-method` or `orphan-tag` tag is not a review candidate."""
    out, files = [], {}
    for t in tags:
        ac = reqs.get(t.rid, {}).get("acs", {}).get(t.acid)
        methods = counting_methods(ac, t.file, defined, paths) if ac else []
        if not methods:
            continue
        if t.file not in files:
            files[t.file] = regions(read_lines(root, t.file))
        region = files[t.file][t.line]
        want = suffix(obligation(t.rid, t.acid, reqs[t.rid], methods, defined), region)
        out.append(Counted(t, methods, region, want))
    return out


def _sample(ids, limit=8):
    """A bounded, sorted preview of an id collection for a finding message."""
    ids = sorted(ids)
    if not ids:
        return "none"
    if len(ids) <= limit:
        return ", ".join(ids)
    return ", ".join(ids[:limit]) + f", ... ({len(ids)} total)"


def _finding(rule, detail, file, line, req=None, ac=None, methods=None,
             state=None):
    """`state` is set for `unreviewed`: which of the review states it is in,
    so a reader does not have to parse it back out of the prose. `hamilton
    build` routes on it -- a changed criterion needs the test written again,
    anything else only needs reviewing."""
    message = f"{file}:{line}: {rule}: {detail}"
    return {"rule": rule, "file": file, "line": line, "req": req, "ac": ac,
            "methods": methods, "state": state, "message": message}


def _warning(rule, detail, file, line):
    """Advisory only: never counted as a finding, never changes the exit code.
    The `warning:` word in the message keeps it distinct from a finding."""
    return {"rule": rule, "file": file, "line": line,
            "message": f"{file}:{line}: warning: {rule}: {detail}"}


def _one_sentence(text: str) -> bool:
    collapsed = " ".join(text.split())
    return not _SECOND_SENTENCE_RE.search(collapsed)


def _notices(root: str, cfg: dict) -> list:
    """Config that is set but does nothing yet, and state files nothing reads
    any more. A line that looks active and is silently ignored is the failure
    the falsification ledger had, so say so every run."""
    out = []
    if os.path.exists(os.path.join(root, RETIRED_VERIFIED_REL)):
        out.append(
            f"{RETIRED_VERIFIED_REL} is no longer used -- reviews are recorded "
            f"in the '@covers' tags' suffixes now (D-020), and hamilton check "
            f"neither reads nor writes it. Delete it.")
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
        acs = r["acs"].values()
        if r["parent"] is None and acs and all(ac["methods"] == ["unit"] for ac in acs):
            out.append(_warning("root-unit-only",
                f"{rid} is a root requirement -- an actor's goal -- but every "
                f"one of its criteria is verified by 'unit'. Nothing then "
                f"exercises the goal the way the actor reaches it, so the gate "
                f"can be green while the product is broken. Fix: give at least "
                f"one criterion an actor-facing method (e.g. through the UI or "
                f"the API the actor uses); if none fits, ask the engineer.",
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


def run(root: str, suite: bool = True, echo: bool = False, on_log=None):
    """Returns (findings, warnings, notices, manual, requirement_count,
    ac_count). `warnings` are advisory (module docstring); `notices` flag
    configuration that is set but does nothing. Neither changes the exit code.
    `manual` lists the "R-nnnn/ACn" a person verifies instead of the gate.

    `on_log` is told the suite's log file just before the suite starts.

    `suite=False` leaves the project's own test command unrun, and with it the
    only finding it produces (`tests-failed`). The gate always runs it; the
    build loop asks for the shape of the spec between its steps, and a suite
    that takes ten minutes is not worth re-running to learn that a tag is
    still unreviewed."""
    if not os.path.isfile(os.path.join(root, REQ_REL)):
        raise UsageError(f"{REQ_REL}: not found (run hamilton check from the "
                         f"project root, the directory that holds spec/)")
    cfg = read_config(root)
    notices = _notices(root, cfg)

    reqs, duplicates, malformed = extract(os.path.join(root, REQ_REL))
    defined = extract_methods(os.path.join(root, REQ_REL))
    n_reqs = len(reqs)
    n_acs = sum(len(r["acs"]) for r in reqs.values())

    if not reqs:
        return ([_finding("malformed",
            "spec/requirements.md defines no requirements. Expected: at least "
            "one '## R-nnnn' block -- with a Statement and acceptance criteria "
            "-- outside any fenced code block. Found: only the fenced example, "
            "or an empty file. Fix: write a real requirement below the "
            "example, then re-run hamilton check.",
            REQ_REL, 1)], [], notices, [], n_reqs, n_acs)

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
    elif suite:
        log = None if echo else new_log()
        if log and on_log is not None:
            on_log(log)                     # before it starts: it can be followed
        ok, detail, cfg_line, output = run_tests(root, cfg, echo, log)
        if ok and log:
            os.remove(log)                  # a green suite's output is not needed
        if not ok:
            cmd = tc[0].strip()
            log = log if output else ""
            failed = _finding("tests-failed",
                f"{detail}. Expected: the project's own test suite to pass "
                f"before the gate certifies anything. Found: it did not"
                f"{' -- its full output is in ' + log if log else ''}. Fix: "
                f"run '{cmd}' yourself from the project root to see why it "
                f"fails and repair the implementation or the test; or "
                f"set/correct test_command in {CONFIG_REL}.",
                CONFIG_REL, cfg_line)
            # the end of the output, for whoever fixes it (`hamilton build`
            # hands it to its coding step), and where the whole of it is
            failed["output"] = "\n".join(output.splitlines()[-SUITE_TAIL:])
            failed["log"] = log
            out.append(failed)

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

    retired = cfg.get("test_paths")
    if retired is not None:
        out.append(_finding("retired-config",
            f"{CONFIG_REL} still sets test_paths, which is retired: a test now "
            f"counts only under the paths of its criterion's verification "
            f"method. Expected: one 'paths.<method>=<dirs>' line per method "
            f"defined in '## {METHODS_HEADING}' in {REQ_REL}. Found: "
            f"'test_paths={retired[0].strip()}'. Fix: split those directories "
            f"into paths.<method> keys (e.g. 'paths.unit=tests/unit', "
            f"'paths.browser=tests/browser') and delete the test_paths line.",
            CONFIG_REL, retired[1]))

    paths = method_paths(cfg)
    scanned = scan(root, [d for ds in paths.values() for d in ds])
    tags = {}
    for req, ac, file, line, _suffix in scanned:
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
                f"those, or add '- {ac}: <condition> -> <outcome> [method]' "
                f"under {req}.",
                file, line, req=req, ac=ac))
        else:
            tags.setdefault((req, ac), []).append((file, line))

    manual, unpathed = [], {}
    for rid, r in reqs.items():
        for acid, ac in sorted(r["acs"].items()):
            qual, methods = f"{rid}/{acid}", ac["methods"]
            here = tags.get((rid, acid), [])
            at = "; ".join(f"{f}:{ln}" for f, ln in here)
            if not methods:
                out.append(_finding("no-method",
                    f"{qual} does not say how it is verified. Expected: the "
                    f"criterion ends in a marker naming a method from '## "
                    f"{METHODS_HEADING}', e.g. '... -> ... [browser]'. Found: "
                    f"no marker; {REQ_REL} defines {_sample(defined)}. Fix: "
                    f"add the marker in a design session -- the method is spec, "
                    f"ratified by the engineer.",
                    REQ_REL, ac["line"], req=rid, ac=acid, methods=methods))
            unknown = [m for m in methods if m != MANUAL and m not in defined]
            if unknown:
                out.append(_finding("unknown-method",
                    f"{qual} names {', '.join(unknown)}, which '## "
                    f"{METHODS_HEADING}' does not define. Expected: every "
                    f"method in a marker is a bullet '- **name** — what is real "
                    f"and what is stubbed' in that section above the first "
                    f"requirement (or '{MANUAL}'). Found: {REQ_REL} defines "
                    f"{_sample(defined)}. Fix: correct the marker, or define "
                    f"the method -- both in a design session.",
                    REQ_REL, ac["line"], req=rid, ac=acid, methods=methods))
            if MANUAL in methods:
                manual.append(qual)
            for m in methods:
                if m in defined and m != MANUAL and m not in paths:
                    unpathed.setdefault(m, qual)
            checkable = [m for m in methods if m in defined and m in paths]
            missing = missing_methods(checkable, paths, [f for f, _ in here])
            if missing and here and missing == checkable:
                need = "; ".join(f"paths.{m} ({' '.join(paths[m])})" for m in missing)
                out.append(_finding("wrong-method",
                    f"{qual} is tagged, but not where its method is verified. "
                    f"Expected: '@covers {qual}' in a test under {need}. "
                    f"Found: the tag at {at}, outside those paths -- that test "
                    f"verifies the criterion some other way. Fix: write a test "
                    f"that exercises it by that method, under those paths, and "
                    f"tag it; moving the tag alone is not enough.",
                    REQ_REL, ac["line"], req=rid, ac=acid, methods=methods))
            elif missing:
                need = "; ".join(f"paths.{m} ({' '.join(paths[m])})" for m in missing)
                found = (f"tagged only at {at}" if here else "no tagged test")
                out.append(_finding("uncovered",
                    f"{qual} has no test for {', '.join(missing)}. Expected: a "
                    f"comment '@covers {qual}' in a test under {need} (any "
                    f"language -- the tag text is matched, not the comment "
                    f"syntax); each method of the criterion needs its own. "
                    f"Found: {found}. Fix: add a test that exercises this "
                    f"criterion by that method and tag it '@covers {qual}'.",
                    REQ_REL, ac["line"], req=rid, ac=acid, methods=methods))

    # every counting tag needs its own review: another reviewed tag for the
    # same AC does not excuse it
    for c in counted(root, reqs, defined, paths, scanned):
        if c.state != REVIEWED:
            out.append(_unreviewed(c, reqs[c.tag.rid]["acs"][c.tag.acid]))

    for m, first in sorted(unpathed.items()):
        out.append(_finding("no-method-paths",
            f"method '{m}' is used (first by {first}) but {CONFIG_REL} has no "
            f"paths.{m}, so no test can verify it. Expected: a "
            f"'paths.{m}=<dirs>' line naming where its tests live. Found: none. "
            f"Fix: choose the test tool and layout for '{m}' and set paths.{m} "
            f"in {CONFIG_REL} (a build-phase decision).",
            REQ_REL, defined[m]["line"]))

    out.sort(key=lambda f: (f["file"] or "", f["line"] or 0, f["rule"]))
    return out, warnings, notices, manual, n_reqs, n_acs


# what an out-of-date suffix means, and what the agent does about it
_UNREVIEWED = {
    NEVER_REVIEWED: (
        "the tag has no review suffix, so no reviewer has judged that this "
        "test proves the criterion",
        "run 'hamilton review {qual}'"),
    AC_CHANGED: (
        "the criterion, its requirement's Statement or its method's "
        "definition changed since the review, so the test may no longer "
        "prove what the criterion now says",
        "rewrite the test against the current wording (a fresh subagent, "
        "SKILL.md Test authoring), then run 'hamilton review {qual}'"),
    TEST_CHANGED: (
        "the test changed since the review -- its section, or the preamble "
        "of its file",
        "run 'hamilton review {qual}' to have the changed test judged again"),
    BOTH_CHANGED: (
        "both the criterion (or its Statement or method definition) and the "
        "test changed since the review",
        "check the test against the current wording, rewrite it if it no "
        "longer fits, then run 'hamilton review {qual}'"),
}


def _unreviewed(c: Counted, ac: dict):
    t, state = c.tag, c.state
    qual = f"{t.rid}/{t.acid}"
    found, fix = _UNREVIEWED[state]
    return _finding("unreviewed",
        f"the '@covers {qual}' test is unreviewed ({state}). Expected: a "
        f"current review suffix, which 'hamilton review' writes when its "
        f"reviewer passes the test. Found: "
        f"{'#' + t.suffix if t.suffix else 'no suffix'} -- {found}. Fix: "
        f"{fix.format(qual=qual)}. Never write or edit a suffix yourself.",
        t.file, t.line, req=t.rid, ac=t.acid, methods=ac["methods"], state=state)


def _one(root: str, only: str, as_json: bool) -> int:
    m = _QUAL_RE.fullmatch(only)
    if not m:
        raise UsageError(f"{only!r} is not an acceptance criterion id; give it "
                         f"as R-nnnn/ACn, e.g. R-0001/AC2")
    rid, acid = m.groups()
    findings, _w, _n, manual, _nr, _na = run(root, suite=False)
    reqs, _dupes, _malformed = extract(os.path.join(root, REQ_REL))
    if acid not in reqs.get(rid, {}).get("acs", {}):
        raise UsageError(f"{only} is not declared in {REQ_REL}")
    mine = [f for f in findings if (f.get("req"), f.get("ac")) == (rid, acid)]
    if as_json:
        print(json.dumps({"ok": not mine, "findings": mine,
                          "manual": [q for q in manual if q == only]}))
        return 1 if mine else 0
    from hamilton_core.session.console import Paint, supports_color
    one = {rid: dict(reqs[rid], acs={acid: reqs[rid]["acs"][acid]})}
    lines, _other = view(mine, one, manual, Paint(supports_color(sys.stdout)))
    for text in lines:
        print(text)
    print(f"hamilton check: {only} only -- the suite was not run", file=sys.stderr)
    return 1 if mine else 0


# How a criterion reads in the human view, decided by the worst finding for
# it: a missing or unusable test first, then a missing review.
_AC_MARKS = {
    "uncovered": ("✗", "no test by its method"),
    "wrong-method": ("✗", "tested, but not by its method"),
    "no-method": ("✗", "no [method] marker"),
    "unknown-method": ("✗", "names a method the spec does not define"),
    "unreviewed": ("?", "not reviewed"),
}


def view(findings: list, reqs: dict, manual: list, paint) -> tuple[list, list]:
    """The gate as a person reads it: every requirement, then each of its
    criteria with a mark -- `✓` fine, `✗` no usable test, `?` not reviewed,
    `○` verified by a person. Returns (lines, the findings that are about no
    one criterion); those are shown after, in full."""
    by_ac: dict = {}
    other = []
    for f in findings:
        rid, acid = f.get("req"), f.get("ac")
        if f["rule"] in _AC_MARKS and acid in reqs.get(rid, {}).get("acs", {}):
            by_ac.setdefault((rid, acid), []).append(f)
        else:
            other.append(f)
    lines = []
    for rid, r in reqs.items():
        title = r.get("title") or " ".join((r.get("statement") or "").split())
        lines.append(paint.bold(f"{rid} {title}".rstrip()))
        for acid, ac in r["acs"].items():
            found = by_ac.get((rid, acid))
            if found:
                worst = min(found, key=lambda f: "✗?".index(_AC_MARKS[f["rule"]][0]))
                mark, why = _AC_MARKS[worst["rule"]]
                if worst["rule"] == "unreviewed" and worst.get("state"):
                    why = f"not reviewed ({worst['state']})"
                colour = paint.red if mark == "✗" else paint.yellow
                lines.append(f"- {colour(mark)} {acid} {ac['text']}  {paint.dim(why)}")
            elif f"{rid}/{acid}" in manual:
                lines.append(f"- {paint.dim('○')} {acid} {ac['text']}  "
                             f"{paint.dim('verified by a person')}")
            else:
                lines.append(f"- {paint.green('✓')} {acid} {ac['text']}")
    return lines, other


_QUAL_RE = re.compile(r"(R-\d{4})/(AC\d+)")


def main(as_json: bool = False, suite_output: bool = False,
         only: str | None = None) -> int:
    """`only` ("R-nnnn/ACn") narrows the gate to one criterion's status -- its
    tags and reviews -- without running the suite: the question a step working
    on that criterion asks, answered in a second. The full gate is the run
    without it."""
    root = os.getcwd()
    try:
        if only is not None:
            return _one(root, only, as_json)
        findings, warnings, notices, manual, n_reqs, n_acs = run(
            root, echo=suite_output,
            on_log=lambda log: print(follow_hint(log), file=sys.stderr))
    except UsageError as exc:
        if as_json:
            print(json.dumps({"error": str(exc)}))
        else:
            print(f"hamilton check: {exc}", file=sys.stderr)
        return 2
    if as_json:
        print(json.dumps({"ok": not findings, "findings": findings,
                          "warnings": warnings, "notices": notices,
                          "manual": manual, "requirements": n_reqs,
                          "acceptance_criteria": n_acs}))
        return 1 if findings else 0

    from hamilton_core.session.console import Paint, supports_color
    paint = Paint(supports_color(sys.stdout))
    reqs, _dupes, _malformed = extract(os.path.join(root, REQ_REL))
    lines, other = view(findings, reqs, manual, paint)
    for text in lines:
        print(text)

    failed = next((f for f in other if f["rule"] == "tests-failed"), None)
    ran = not any(f["rule"] == "no-test-command" for f in other)
    if failed:
        where = f" -- full output: {failed['log']}" if failed.get("log") else ""
        print(f"\nSuite {paint.red('✗')} failed{where}")
    elif ran:
        print(f"\nSuite {paint.green('✓')} passed")
    rest = [f for f in other if f is not failed]
    if rest:
        print(f"\n{paint.bold('Other findings')}")
        for f in rest:
            print(f"  {paint.red('✗')} {f['message']}")

    for w in warnings:
        print(w["message"], file=sys.stderr)
    for n in notices:
        print(f"hamilton check: notice: {n}", file=sys.stderr)
    noun = "criterion" if n_acs == 1 else "criteria"
    print(f"hamilton check: {n_reqs} requirement(s), {n_acs} acceptance {noun}",
          file=sys.stderr)
    if manual:
        noun = "criterion" if len(manual) == 1 else "criteria"
        print(f"hamilton check: {len(manual)} {noun} verified manually, "
              f"not by the gate", file=sys.stderr)
    if warnings:
        print(f"hamilton check: {len(warnings)} warning(s) — advisory, "
              f"not failures", file=sys.stderr)
    print(f"hamilton check: {'ok' if not findings else str(len(findings)) + ' problem(s)'}",
          file=sys.stderr)
    return 1 if findings else 0
