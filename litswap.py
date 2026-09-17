#!/usr/bin/env python
r"""Rename a string constant across a tree of Python scripts, and report every case the rules cannot decide.

litswap rewrites text inside string literals only. A comment is never edited, an
identifier is never edited, and a hit the rules cannot classify is reported for
review instead of being changed. Nothing is written without --apply, and every
file that is written is backed up first to a name that never clobbers an
existing backup.

The failure it is built for: you rename a database, a server, a share or an API
host, and three hundred production scripts carry the old name as a string
literal. `sed -i` is wrong three ways in the same pass. It rewrites the comment
that explains the old name, it rewrites a variable named after it, and it cannot
see that the hit sits inside a triple-quoted SQL statement where the old
qualifier is still correct. Nothing fails on the day. The scripts still parse,
still import, and fail at 2am on a schedule.

`sed` and an editor's Replace in Files do line-oriented substitution well, and
both are far faster than this. Neither knows what a Python string literal is.
`rope` and `libcst` do know, and they rename identifiers correctly, which is a
different job: the thing being renamed here is data inside a literal, not a
symbol. `libcst` will hand you the parsed tree and let you write the codemod.
This is that codemod, with the decision rules already attached and with the
cases it refuses to decide written down.

    python litswap.py --self-test
    python litswap.py scripts/ --rename "OLDHOST=newhost.example.org"
    python litswap.py scripts/ --strip-prefix OldDb.SCHEMA --review-suffix .sde
    python litswap.py scripts/ --rename "OLDHOST=newhost.example.org" --apply

Exit codes: 0 nothing left to do, 1 work outstanding (replacements pending, a
review item, or a file that errored), 2 a flag value was rejected, 64 usage
error.
"""

from __future__ import print_function

import argparse
import fnmatch
import io
import os
import re
import shutil
import sys
import tempfile
import tokenize
from datetime import datetime

# =============================================================================
# CONFIGURATION. Deliberately not flags. Change here, not at the call site.
# =============================================================================

# A string literal holding one of these words is read as embedded SQL rather
# than as a path or a hostname. A qualifier inside a SELECT/FROM/JOIN is
# demoted to REVIEW, never rewritten: a database qualifier usually stays valid
# in SQL after the object it names is re-cataloged, so mechanically stripping
# it out of an embedded T-SQL statement breaks a working pyodbc or OPENQUERY
# script rather than fixing a broken one.
SQL_KEYWORDS = ("SELECT", "FROM", "JOIN", "OPENQUERY", "WHERE", "EXEC",
                "INSERT", "UPDATE", "DELETE", "MERGE")

# What counts as part of the same token when deciding whether a rename hit is
# the whole name or a fragment of a longer one. A hostname carries hyphens and
# dots, so both are token characters here. Without the dot, renaming OLDHOST to
# newhost.example.org would rewrite the already-qualified OLDHOST.example.org
# into newhost.example.org.example.org.
TOKEN_CHARS = ("ABCDEFGHIJKLMNOPQRSTUVWXYZ"
               "abcdefghijklmnopqrstuvwxyz"
               "0123456789_-.")

# Directories never descended into.
SKIP_DIRS = ("__pycache__", "node_modules")

# Extensions scanned unless --ext says otherwise. A .pyt is an ArcGIS Python
# toolbox, which is a .py file with a different name.
DEFAULT_EXTENSIONS = (".py", ".pyt")

# How much of the source line is shown beside a finding.
CONTEXT_WIDTH = 110

# =============================================================================
# End of CONFIGURATION.
# =============================================================================

REPLACE = "REPLACE"
REVIEW = "REVIEW"

_SQL_RE = re.compile(r"\b(?:%s)\b" % "|".join(SQL_KEYWORDS), re.IGNORECASE)


class ScanError(ValueError):
    """A source this tool refuses to reason about.

    Raised instead of returning findings, because a partial answer about a file
    that could not be tokenized is the one answer worse than none: it names a
    few hits, implies the rest are clean, and the operator moves on.
    """


# --------------------------------------------------------------- source model

class Finding(object):
    """One decision about one hit, at one place in one file."""

    def __init__(self, kind, lineno, col, before, after, context, note=""):
        self.kind = kind
        self.lineno = lineno
        self.col = col
        self.before = before
        self.after = after
        self.context = context
        self.note = note


class PreflightHit(object):
    """Evidence that the rename may already have run over this file."""

    def __init__(self, name, lineno, context, target, strong):
        self.name = name
        self.lineno = lineno
        self.context = context
        self.target = target
        self.strong = strong


class Rules(object):
    """The renames to make and the guards that decide when not to make them.

    Nothing is baked in. The caller supplies every rule, so this file carries no
    hostname, no database name and no site of its own.
    """

    def __init__(self, renames=(), strip_prefixes=(), review_suffixes=()):
        self.renames = []
        for old, new in renames:
            if not old:
                raise ValueError("a rename needs a non-empty old value")
            if old == new:
                raise ValueError("rename %r to itself does nothing" % old)
            self.renames.append((old, new, re.compile(re.escape(old),
                                                      re.IGNORECASE)))

        self.strip_prefixes = []
        for prefix in strip_prefixes:
            # The replacement is everything after the first dot, taken from the
            # source rather than from this list, so the casing a script actually
            # wrote is what survives. That needs a dot to split on.
            if "." not in prefix.strip("."):
                raise ValueError(
                    "a strip prefix must be Qualifier.Kept, not %r" % prefix)
            self.strip_prefixes.append(prefix)

        if not self.renames and not self.strip_prefixes:
            raise ValueError("no rules: pass --rename or --strip-prefix")

        self.review_suffixes = tuple(s.lower() for s in review_suffixes)

        alternation = "|".join(re.escape(p) for p in self.strip_prefixes)
        if alternation:
            # Group 2 stops at a backslash, so a Dataset\Object double
            # qualifier yields TWO independent matches on one line and both are
            # rewritten. It does NOT stop at a dot, so a dot-joined double
            # qualifier matches once with the second prefix inside group 2;
            # strip_all then re-scans the replacement until no prefix is left.
            self.strip_re = re.compile(
                r"(" + alternation + r")"
                r"(\.[A-Za-z_][A-Za-z0-9_.]*)",
                re.IGNORECASE)
            # The same prefix with a trailing dot and NO literal object name
            # after it: f"Db.Schema.{layer}", "Db.Schema." + name,
            # "Db.Schema.%s" % name. The static pattern above cannot see these,
            # and a rename that silently misses them is the failure this tool
            # exists to prevent. They are surfaced as REVIEW, never rewritten,
            # because the object is computed at run time. The negative
            # lookahead is what keeps the two patterns from ever both firing on
            # the same hit.
            self.dynamic_re = re.compile(
                r"(" + alternation + r")\.(?![A-Za-z_])", re.IGNORECASE)
        else:
            self.strip_re = None
            self.dynamic_re = None


# ----------------------------------------------------------------- tokenizing

def line_offsets(source):
    """Absolute offset where each line starts, indexed from zero."""
    offsets = [0]
    for m in re.finditer("\n", source):
        offsets.append(m.end())
    return offsets


def offset_of(offsets, row, col):
    """Absolute offset of a 1-based tokenize (row, col) position."""
    index = row - 1
    if index >= len(offsets):
        index = len(offsets) - 1
    return offsets[index] + col


def line_for_offset(offsets, pos):
    """1-based line number holding an absolute offset."""
    lo, hi = 0, len(offsets) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if offsets[mid] <= pos:
            lo = mid
        else:
            hi = mid - 1
    return lo + 1


def context_for(source, offsets, lineno):
    """The source line holding a finding, stripped and truncated."""
    start = offsets[lineno - 1]
    end = offsets[lineno] - 1 if lineno < len(offsets) else len(source)
    return source[start:end].strip()[:CONTEXT_WIDTH]


def literal_spans(source):
    """Return (strings, comments) as lists of (start, end) absolute offsets.

    This is `tokenize` from the standard library, not a hand-rolled scanner.
    Raw prefixes, byte prefixes, triple quotes that run over many lines, a #
    inside a literal and a quote inside a comment are all the tokenizer's
    problem, and it is the same tokenizer the interpreter agrees with.

    An f-string is returned as ONE span covering the whole literal. Python 3.12
    split f-strings into FSTRING_START, FSTRING_MIDDLE and FSTRING_END tokens,
    where 3.9 through 3.11 emit a single STRING. Merging them back means this
    function answers the same thing on ArcGIS Pro's Python and on a current
    python3, which is the whole point of asking the tokenizer instead of
    guessing.
    """
    offsets = line_offsets(source)
    strings = []
    comments = []
    fstring_open = None
    depth = 0
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            kind, _text, start, end, _line = token
            name = tokenize.tok_name.get(kind, "")
            pos = offset_of(offsets, start[0], start[1])
            stop = offset_of(offsets, end[0], end[1])
            if name == "FSTRING_START":
                if depth == 0:
                    fstring_open = pos
                depth += 1
            elif name == "FSTRING_END":
                depth -= 1
                if depth == 0:
                    strings.append((fstring_open, stop))
            elif kind == tokenize.STRING:
                # A plain literal nested in an f-string's replacement field is
                # already inside the outer span.
                if depth == 0:
                    strings.append((pos, stop))
            elif kind == tokenize.COMMENT:
                comments.append((pos, stop))
    except (tokenize.TokenError, SyntaxError) as exc:
        raise ScanError("cannot tokenize this file, so it was not scanned: %s"
                        % exc)
    strings.sort()
    comments.sort()
    return strings, comments


def span_at(spans, pos):
    """The (start, end) span containing pos, or None."""
    lo, hi = 0, len(spans) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        start, end = spans[mid]
        if pos < start:
            hi = mid - 1
        elif pos >= end:
            lo = mid + 1
        else:
            return spans[mid]
    return None


# --------------------------------------------------------------- the decision

def strip_all(text, strip_re):
    """Remove every strip prefix in `text`, including one nested in another.

    "Db.SCHEMA.Area.Db.SDE.Lookup" reduces to "SCHEMA.Area.SDE.Lookup" rather
    than leaving the second qualifier behind, because each pass re-scans what
    the previous pass produced. No iteration guard is needed: a substitution
    only ever deletes characters, so the text shrinks every pass until the
    pattern stops matching.
    """

    def sub(m):
        # The kept portion comes from the source, so a script that wrote
        # "Db.SchemaName.Parcels" keeps SchemaName and does not acquire the
        # SCHEMANAME spelling that happened to be typed on the command line.
        return m.group(1).split(".", 1)[1] + m.group(2)

    previous = None
    while previous != text:
        previous = text
        text = strip_re.sub(sub, text)
    return text


def _left_boundary(source, pos):
    """True when pos does not start in the middle of a word."""
    if pos == 0:
        return True
    return source[pos - 1] not in (
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_")


def _token_boundaries(source, start, end):
    """True when [start, end) is a whole token, not a fragment of a longer one."""
    left = start == 0 or source[start - 1] not in TOKEN_CHARS
    right = end >= len(source) or source[end] not in TOKEN_CHARS
    return left and right


def _literal_text(source, span):
    """The source text of a literal, closing quotes and trailing space removed."""
    return source[span[0]:span[1]].rstrip("\"'").rstrip()


def scan_source(source, rules):
    """Return every Finding in one source string. Raises ScanError.

    Pure: no file system, no network, no arcpy. Everything this tool decides is
    decided here, so the self-test exercises the real thing.
    """
    strings, comments = literal_spans(source)
    offsets = line_offsets(source)
    findings = []

    def place(pos):
        lineno = line_for_offset(offsets, pos)
        col = pos - offsets[lineno - 1] + 1
        return lineno, col, context_for(source, offsets, lineno)

    if rules.strip_re is not None:
        _scan_strip(source, rules, strings, comments, place, findings)
    for old, new, pattern in rules.renames:
        _scan_rename(source, rules, strings, comments, place, findings,
                     old, new, pattern)

    findings.sort(key=lambda f: (f.lineno, f.col, f.kind, f.before))
    return findings


def _scan_strip(source, rules, strings, comments, place, findings):
    """Qualifier prefixes: Db.SCHEMA.Object becomes SCHEMA.Object."""
    for m in rules.strip_re.finditer(source):
        pos = m.start()
        if span_at(comments, pos) is not None:
            # A comment explaining the old name is documentation of why the
            # rename happened. Rewriting it is how sed destroys the only record.
            continue
        lineno, col, context = place(pos)
        if span_at(strings, pos) is None:
            findings.append(Finding(
                REVIEW, lineno, col, m.group(0), "", context,
                "outside a string literal, so it is code or documentation "
                "rather than data"))
            continue
        if not _left_boundary(source, pos):
            findings.append(Finding(
                REVIEW, lineno, col, m.group(0), "", context,
                "runs on from the word before it, so the left edge is ambiguous"))
            continue
        span = span_at(strings, pos)
        if _SQL_RE.search(source[span[0]:span[1]]):
            findings.append(Finding(
                REVIEW, lineno, col, m.group(0), "", context,
                "inside an embedded SQL statement, where the qualifier is "
                "still valid; stripping it here would break the statement "
                "rather than fix it"))
            continue
        findings.append(Finding(
            REPLACE, lineno, col, m.group(0),
            strip_all(m.group(0), rules.strip_re), context))

    for m in rules.dynamic_re.finditer(source):
        pos = m.start()
        if span_at(comments, pos) is not None:
            continue
        if span_at(strings, pos) is None:
            # Kept for symmetry with the static pass and not reachable from any
            # source this tool will read: a qualifier with a trailing dot and no
            # object name after it is not valid Python outside a literal, so a
            # file containing one in code never gets past the tokenizer.
            continue
        if not _left_boundary(source, pos):
            continue
        lineno, col, context = place(pos)
        suggested = m.group(1).split(".", 1)[1] + "."
        findings.append(Finding(
            REVIEW, lineno, col, m.group(0), "", context,
            "qualifier with a computed object name (suggest %r -> %r); the "
            "object is built at run time, so only the qualifier can be fixed, "
            "and it must be fixed by hand" % (m.group(0), suggested)))


def _scan_rename(source, rules, strings, comments, place, findings,
                 old, new, pattern):
    """A whole value: OLDHOST becomes newhost.example.org."""
    for m in pattern.finditer(source):
        pos = m.start()
        if span_at(comments, pos) is not None:
            continue
        lineno, col, context = place(pos)
        span = span_at(strings, pos)
        if span is None:
            findings.append(Finding(
                REVIEW, lineno, col, m.group(0), "", context,
                "outside a string literal, so it is an identifier or a comment "
                "and not this tool's to edit"))
            continue
        literal = _literal_text(source, span)
        suffix = _matching_suffix(literal, rules.review_suffixes)
        if suffix is not None:
            # Checked BEFORE the token boundary on purpose. A name embedded in
            # a connection filename fails the boundary test too, and "this
            # belongs to the connection-file tool" is the more useful answer
            # than "the edges are ambiguous".
            findings.append(Finding(
                REVIEW, lineno, col, m.group(0), "", context,
                "inside a literal ending in %s, which is a connection file "
                "and is not edited as text" % suffix))
            continue
        if not _token_boundaries(source, pos, m.end()):
            findings.append(Finding(
                REVIEW, lineno, col, m.group(0), "", context,
                "part of a longer token, so rewriting it would corrupt the "
                "rest of that token"))
            continue
        findings.append(Finding(REPLACE, lineno, col, m.group(0), new, context))


def _matching_suffix(literal, suffixes):
    for suffix in suffixes:
        if literal.lower().endswith(suffix):
            return suffix
    return None


def apply_findings(source, findings):
    """Return `source` with every REPLACE applied. Raises ScanError on drift.

    Pure. Each replacement is made at the column it was found at, right to left
    within a line so earlier columns stay valid, and the text at that column is
    checked first. When it does not match, the source moved under the findings
    and this raises rather than falling back on a first-occurrence replace: a
    blind replace on a stale finding edits the wrong hit on the line.
    """
    replacements = [f for f in findings if f.kind == REPLACE]
    if not replacements:
        return source

    by_line = {}
    for f in replacements:
        by_line.setdefault(f.lineno, []).append(f)

    lines = source.splitlines(True)
    for lineno in sorted(by_line):
        if lineno > len(lines):
            raise ScanError(
                "apply refused: the source has %d lines but a finding names "
                "line %d. Re-run the scan." % (len(lines), lineno))
        line = lines[lineno - 1]
        for f in sorted(by_line[lineno], key=lambda f: f.col, reverse=True):
            start = f.col - 1
            segment = line[start:start + len(f.before)]
            if segment.lower() != f.before.lower():
                raise ScanError(
                    "apply refused: line %d column %d holds %r, not the %r the "
                    "scan found there. Re-run the scan; nothing was written."
                    % (f.lineno, f.col, segment, f.before))
            line = line[:start] + f.after + line[start + len(f.before):]
        lines[lineno - 1] = line
    return "".join(lines)


# ------------------------------------------------------------------ preflight

def preflight(sources, rules):
    """Evidence that this rename has already run, per source.

    `sources` maps a name to its text. The check exists because applying a
    rename twice, or applying it after the thing it points at has already moved,
    is the ordering mistake that leaves a tree half converted. It WARNS by
    default and only refuses on strong evidence, so an operator with one
    ambiguous hit is not pushed into using --force for everything.
    """
    hits = []
    if rules.strip_re is not None:
        hits.extend(_preflight_strip(sources, rules))
    for old, new, pattern in rules.renames:
        hits.extend(_preflight_rename(sources, old, new, pattern))
    return hits


def _bare_pattern(strip_prefixes):
    """Match Kept.Object where the qualifier in front of it is already gone."""
    kept = sorted({p.split(".", 1)[1] for p in strip_prefixes},
                  key=len, reverse=True)
    return re.compile(
        r"(?<![A-Za-z0-9_.])(" + "|".join(re.escape(k) for k in kept) +
        r")\.([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)


def _preflight_strip(sources, rules):
    """A bare Kept.Object is weak evidence on its own.

    "SCHEMA.Lookup" is what a legitimate two-part name looks like as well as
    what a stripped three-part name looks like, and nothing in the text tells
    them apart. It becomes strong evidence only when the SAME object name is
    still written in full somewhere else in the tree, because that pairing is
    the thing a half-finished run leaves behind.
    """
    bare_re = _bare_pattern(rules.strip_prefixes)
    full_objects = set()
    for text in sources.values():
        for m in rules.strip_re.finditer(text):
            # Group 2 always opens with a dot and a letter, so its first
            # segment is never empty.
            obj = m.group(2).lstrip(".").split(".", 1)[0].split("\\", 1)[0]
            full_objects.add(obj.lower())

    hits = []
    for name in sorted(sources):
        text = sources[name]
        if rules.strip_re.search(text):
            continue  # still carries full qualifiers, so plainly not done yet
        try:
            strings, _comments = literal_spans(text)
        except ScanError:
            continue  # the scan reports this file as an error in its own right
        offsets = line_offsets(text)
        for m in bare_re.finditer(text):
            if span_at(strings, m.start()) is None:
                continue
            lineno = line_for_offset(offsets, m.start())
            hits.append(PreflightHit(
                name, lineno, context_for(text, offsets, lineno),
                m.group(0), m.group(2).lower() in full_objects))
    return hits


def _preflight_rename(sources, old, new, pattern):
    """A file holding the new value and not the old one has probably been done."""
    anywhere_old = any(pattern.search(text) for text in sources.values())
    new_re = re.compile(re.escape(new), re.IGNORECASE)
    hits = []
    for name in sorted(sources):
        text = sources[name]
        if pattern.search(text):
            continue
        m = new_re.search(text)
        if m is None:
            continue
        offsets = line_offsets(text)
        lineno = line_for_offset(offsets, m.start())
        hits.append(PreflightHit(
            name, lineno, context_for(text, offsets, lineno),
            m.group(0), not anywhere_old))
    return hits


# ------------------------------------------------------------------- the file

class FileResult(object):
    def __init__(self, path):
        self.path = path
        self.text = None
        self.bom = b""
        self.newline = None
        self.findings = []
        self.error = ""
        self.backup_path = None
        self.applied = False

    @property
    def replace_count(self):
        return sum(1 for f in self.findings if f.kind == REPLACE)

    @property
    def review_count(self):
        return sum(1 for f in self.findings if f.kind == REVIEW)


def read_file(path):
    """Read one file byte-faithfully into a FileResult.

    Line endings are preserved because the file is decoded with newline
    translation off and written back the same way. A UTF-8 BOM is kept aside and
    put back on write: Windows editors write them, and silently dropping one
    changes the file for no reason the operator asked for.
    """
    result = FileResult(path)
    try:
        data = open(path, "rb").read()
    except (IOError, OSError) as exc:
        result.error = "read failed: %s" % exc
        return result
    if data.startswith(b"\xef\xbb\xbf"):
        result.bom = b"\xef\xbb\xbf"
        data = data[3:]
    try:
        result.text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        # Not decoded with errors="replace". A lossy decode would let the tool
        # write back a file with the undecodable bytes destroyed.
        result.error = "not valid UTF-8, so it was skipped and not modified: %s" % exc
    return result


def scan_result(result, rules):
    """Fill one FileResult's findings, or its error. Never raises."""
    if result.error or result.text is None:
        return result
    try:
        result.findings = scan_source(result.text, rules)
    except ScanError as exc:
        result.error = str(exc)
        result.findings = []
    return result


def backup_path_for(path):
    """A backup name that never overwrites a backup already there.

    The plain <file>.bak when it is free, otherwise a timestamped one. A tool
    that clobbers its own backup destroys the only copy of the previous state
    on the second run, which is exactly when somebody needs it.
    """
    plain = path + ".bak"
    if not os.path.exists(plain):
        return plain
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = "%s.%s.bak" % (path, stamp)
    counter = 1
    while os.path.exists(candidate):
        candidate = "%s.%s-%d.bak" % (path, stamp, counter)
        counter += 1
    return candidate


def write_atomic(path, data):
    """Replace `path` with `data` in one step.

    A temporary file in the same directory, then os.replace. A crash or a full
    disk leaves the old file or the new one, never half of either.
    """
    directory = os.path.dirname(os.path.abspath(path))
    handle, temp = tempfile.mkstemp(dir=directory,
                                    prefix=os.path.basename(path) + ".",
                                    suffix=".litswap")
    try:
        os.write(handle, data)
        os.close(handle)
        handle = None
        shutil.copymode(path, temp)
        os.replace(temp, path)
    except Exception:
        if handle is not None:
            os.close(handle)
        if os.path.exists(temp):
            os.remove(temp)
        raise


def apply_result(result):
    """Write one file's replacements, backup first. Mutates and returns result."""
    if result.error or not result.replace_count:
        return result
    try:
        new_text = apply_findings(result.text, result.findings)
    except ScanError as exc:
        result.error = str(exc)
        return result
    # The scan happened a moment ago, not a run ago, but a scheduled job or a
    # colleague's editor can still land in between. Comparing against what was
    # scanned costs one read, and it is the difference between refusing and
    # quietly overwriting somebody else's edit.
    current = read_file(result.path)
    if (current.error or current.text != result.text
            or current.bom != result.bom):
        result.error = ("changed on disk since it was scanned, so nothing was "
                        "written. Re-run the scan.")
        return result

    # Asked before anything is created, so a file nobody can write produces one
    # clear error and no stray backup beside it.
    if not os.access(result.path, os.W_OK):
        result.error = "not writable, so nothing was changed"
        return result

    backup = backup_path_for(result.path)
    try:
        open(backup, "wb").write(result.bom + result.text.encode("utf-8"))
        result.backup_path = backup
        write_atomic(result.path, result.bom + new_text.encode("utf-8"))
    except (IOError, OSError) as exc:
        if result.backup_path and os.path.exists(result.backup_path):
            os.remove(result.backup_path)  # leave no orphan behind
        result.backup_path = None
        result.error = "write failed: %s" % exc
        return result
    result.text = new_text
    result.applied = True
    return result


# ------------------------------------------------------------------ discovery

def _posix(path):
    return path.replace("\\", "/")


def excluded(rel, patterns):
    """fnmatch against the POSIX spelling of a relative path, ignoring case.

    Both sides are put into one spelling here rather than left to fnmatch.
    fnmatch runs os.path.normcase, which lower-cases on Windows and does
    nothing on Linux, so the same --exclude answers differently per platform.
    Lowering both sides and calling fnmatchcase gives one answer everywhere.
    The pattern is put through _posix as well, so an --exclude typed with
    backslashes matches a path this tool spells with slashes.

    Measured, because the obvious worry turns out not to be the real one:
    normcase rewrites slashes into backslashes in the path AND in the pattern,
    so "vendor/*" does keep matching under plain fnmatch. Case is the
    difference that bites.
    """
    low = _posix(rel).lower()
    base = low.rsplit("/", 1)[-1]
    for pattern in patterns:
        low_pattern = _posix(pattern).lower()
        if (fnmatch.fnmatchcase(low, low_pattern)
                or fnmatch.fnmatchcase(base, low_pattern)):
            return True
    return False


def discover(paths, extensions, excludes):
    """Every file under `paths` with a wanted extension and no exclude match."""
    wanted = tuple((e if e.startswith(".") else "." + e).lower()
                   for e in extensions)
    found = []
    for path in paths:
        if os.path.isfile(path):
            # A file named on the command line is still subject to --exclude, so
            # pointing straight at an excluded file cannot get it rewritten.
            if os.path.splitext(path)[1].lower() in wanted and not excluded(
                    os.path.basename(path), excludes):
                found.append(path)
            continue
        for dirpath, dirnames, filenames in os.walk(path):
            dirnames[:] = [d for d in dirnames
                           if d not in SKIP_DIRS and not d.startswith(".")]
            for name in filenames:
                if os.path.splitext(name)[1].lower() not in wanted:
                    continue
                full = os.path.join(dirpath, name)
                if excluded(os.path.relpath(full, path), excludes):
                    continue
                found.append(full)
    return sorted(set(found))


# ------------------------------------------------------------------ reporting

def describe(results, apply_mode):
    """The report, as a list of lines."""
    lines = []
    replaces = sum(r.replace_count for r in results)
    reviews = sum(r.review_count for r in results)
    errors = [r for r in results if r.error]

    for result in results:
        if not result.findings and not result.error:
            continue
        lines.append("")
        lines.append(result.path)
        if result.error:
            lines.append("  ERROR    %s" % result.error)
        if result.applied:
            lines.append("  written, backup -> %s" % result.backup_path)
        for f in result.findings:
            where = "line %d, col %d" % (f.lineno, f.col)
            if f.kind == REPLACE:
                lines.append("  REPLACE  %-22s %r -> %r"
                             % (where, f.before, f.after))
            else:
                lines.append("  REVIEW   %-22s %r" % (where, f.before))
                lines.append("           %s" % f.note)
            lines.append("           %s" % f.context)

    lines.append("")
    lines.append("%d replacement(s) %s, %d for review, %d file(s) errored"
                 % (replaces, "written" if apply_mode else "pending",
                    reviews, len(errors)))
    if replaces and not apply_mode:
        lines.append("Nothing was written. Re-run with --apply.")
    if reviews:
        lines.append("Every REVIEW item is a case the rules could not decide. "
                     "Read each one; none of them was changed.")
    return lines


def describe_preflight(hits, force):
    lines = ["", "PRE-FLIGHT: this rename may already have run."]
    strong = [h for h in hits if h.strong]
    weak = [h for h in hits if not h.strong]
    lines.append("  %d strong sign(s), %d weak hint(s)" % (len(strong), len(weak)))
    for hit in (strong + weak)[:20]:
        lines.append("  %-6s %s:%d  %s"
                     % ("STRONG" if hit.strong else "weak", hit.name,
                        hit.lineno, hit.context))
    if len(hits) > 20:
        lines.append("  ... and %d more" % (len(hits) - 20))
    if strong and force:
        lines.append("--force given, so the pre-flight is overridden.")
    elif strong:
        lines.append("Refusing to --apply. Confirm the ordering, then re-run "
                     "with --force.")
    else:
        lines.append("Weak hints only, which a legitimate two-part name also "
                     "produces. Continuing with a warning.")
    return lines


# ------------------------------------------------------------------ self-test

def self_test():
    """Assertions over the decision core, then over the file layer.

    No network, no database, no arcpy, no third-party package. The file half
    writes to a temporary directory and removes it. Every assertion runs on
    Windows and on Linux, so the count the README prints is the count on both.
    """
    passed = [0]
    failed = []

    def check(cond, label):
        if cond:
            passed[0] += 1
            print("PASS  %s" % label)
        else:
            failed.append(label)
            print("FAIL  %s" % label)

    def raises(fn, label):
        try:
            fn()
        except ValueError:
            check(True, label)
        except Exception as exc:
            check(False, "%s (wrong exception %r)" % (label, exc))
        else:
            check(False, "%s (no error raised)" % label)

    def kinds(findings):
        return [f.kind for f in findings]

    def replaces(findings):
        return [f for f in findings if f.kind == REPLACE]

    def reviews(findings):
        return [f for f in findings if f.kind == REVIEW]

    def capture(fn):
        # stderr as well as stdout: a usage message written straight to the
        # terminal lands in the middle of a PASS line and makes the run look
        # broken when it is not.
        held_out, held_err = sys.stdout, sys.stderr
        buffer = io.StringIO()
        sys.stdout = sys.stderr = buffer
        try:
            code = fn()
            return code, buffer.getvalue()
        finally:
            sys.stdout, sys.stderr = held_out, held_err

    strip = Rules(strip_prefixes=["Db.SCHEMA", "Db.SDE"])
    host = Rules(renames=[("OLDHOST", "newhost.example.org")],
                 review_suffixes=[".sde"])

    print("litswap self-test: no network, no database, no arcpy")
    print("-" * 68)

    # ---- the tokenizer decides what is a literal
    src = 'x = "a"  # b = "c"\n'
    strings, comments = literal_spans(src)
    check(len(strings) == 1 and len(comments) == 1,
          "a quote inside a comment is comment, not a second literal")
    check(span_at(strings, src.index('"a"')) is not None,
          "the literal itself is inside the literal span")
    check(span_at(strings, src.index("# b")) is None,
          "the comment is not")
    src = 'x = "one" "two"\n'
    check(len(literal_spans(src)[0]) == 2,
          "implicitly concatenated literals are two spans")
    src = 'x = r"C:\\path\\dir"  # still a comment\n'
    check(len(literal_spans(src)[1]) == 1,
          "a raw string's backslashes do not escape its closing quote")
    src = 'x = "C:\\\\path\\\\"  # still a comment\n'
    check(len(literal_spans(src)[1]) == 1,
          "and neither does an escaped backslash at the end of a plain string")
    src = 'sql = """\nline two\nline three\n"""\n'
    check(span_at(literal_spans(src)[0], src.index("line three")) is not None,
          "a triple-quoted literal covers a line three lines down")
    src = 'x = f"pre{value}post"\n'
    check(len(literal_spans(src)[0]) == 1,
          "an f-string is ONE span on every Python version  <-- pinned defect")
    check(span_at(literal_spans(src)[0], src.index("post")) is not None,
          "including the part after the replacement field")
    src = 'x = "#not a comment"\n'
    check(literal_spans(src)[1] == [],
          "a hash inside a literal starts no comment")
    src = 'x = f"a{f\'b{c}\'}d"\n'
    check(len(literal_spans(src)[0]) == 1,
          "an f-string nested in an f-string is still ONE span  "
          "<-- pinned defect")
    check(span_at(literal_spans(src)[0], src.index("}d")) is not None,
          "and the outer literal owns the text after the inner one closes")
    check(literal_spans(src)[0][0][0] == src.index('f"a{'),
          "and that span STARTS at the outer f-string, not at the inner one  "
          "<-- pinned defect")
    src = 'x = f"a{\'plain\'}b"\n'
    check(len(literal_spans(src)[0]) == 1,
          "a plain literal inside a replacement field is not a second span  "
          "<-- pinned defect")
    check(len(scan_source('fc = "Db.SCHEMA.Parcels"', strip)) == 1,
          "a file with no newline on its last line still scans  "
          "<-- pinned defect")
    check(_left_boundary("Db.SCHEMA.X", 0),
          "a hit at offset zero has a clean left edge, and is not compared "
          "against the last byte of the file  <-- pinned defect")
    check(_token_boundaries("OLDHOST", 0, 7),
          "and the same at both edges for a whole-value rename")
    # Both rule kinds in one run, and deliberately in the order that catches a
    # missing sort: the strip pass runs first, so its hit is FOUND first while
    # the rename hit belongs earlier in the file.
    both = Rules(renames=[("OLDHOST", "newhost.example.org")],
                 strip_prefixes=["Db.SCHEMA"])
    f = scan_source('a = "OLDHOST"\nb = "Db.SCHEMA.X" + "Db.SCHEMA.Y"\n', both)
    check([(x.lineno, x.col) for x in f] == [(1, 6), (2, 6), (2, 22)],
          "findings come out in line then column order whichever rule found "
          "them, which is the order the report prints and --apply walks  "
          "<-- pinned defect")
    check([x.after for x in f]
          == ["newhost.example.org", "SCHEMA.X", "SCHEMA.Y"],
          "and a --rename and a --strip-prefix in one run both do their work")
    f = scan_source('if 1:\n    x = "Db.SCHEMA.A"\n', strip)
    check(f[0].context == 'x = "Db.SCHEMA.A"',
          "the context beside a finding is stripped of its indentation")

    # ---- a source the tokenizer refuses is an error, never a partial answer
    raises(lambda: literal_spans('sql = """unterminated\n'),
           "an unterminated triple quote raises  <-- pinned defect")
    raises(lambda: literal_spans('x = (1,\n'),
           "an unclosed bracket raises")
    raises(lambda: literal_spans("def f():\n    a = 1\n  b = 2\n"),
           "a bad dedent raises")
    raises(lambda: scan_source('sql = """unterminated\n', strip),
           "scan_source raises rather than scanning a partial mask  <-- pinned defect")

    # ---- strip rules: the qualifier cases
    f = scan_source('fc = "Db.SCHEMA.Parcels"\n', strip)
    check(kinds(f) == [REPLACE] and f[0].after == "SCHEMA.Parcels",
          "a qualified name in a literal is a replacement")
    f = scan_source('fc = "Db.Schema.Parcels"\n', strip)
    check(replaces(f)[0].after == "Schema.Parcels",
          "the casing the script wrote is kept, not the casing on the command "
          "line  <-- pinned defect")
    line = 'p = r"C:\\gis\\Db.SCHEMA.Area\\Db.SCHEMA.Roads"\n'
    f = scan_source(line, strip)
    check(sorted(x.after for x in replaces(f))
          == ["SCHEMA.Area", "SCHEMA.Roads"],
          "a backslash-separated double qualifier gives TWO replacements  "
          "<-- pinned defect")
    f = scan_source('fc = "Db.SCHEMA.Area.Db.SDE.Lookup"\n', strip)
    check(len(replaces(f)) == 1
          and replaces(f)[0].after == "SCHEMA.Area.SDE.Lookup",
          "a dot-joined double qualifier is fully reduced, not left half done  "
          "<-- pinned defect")
    f = scan_source("# Db.SCHEMA.Parcels is the old name\n", strip)
    check(f == [],
          "a qualified name in a comment is neither replaced nor reviewed  "
          "<-- pinned defect")
    f = scan_source("Db_SCHEMA = Db.SCHEMA.Parcels\n", strip)
    check(kinds(f) == [REVIEW] and "outside a string" in f[0].note,
          "a qualified name in code is reviewed, never rewritten")
    f = scan_source('fc = "xDb.SCHEMA.Parcels"\n', strip)
    check(kinds(f) == [REVIEW] and "left edge" in f[0].note,
          "a qualifier running on from the word before it is reviewed")

    # ---- the SQL demotion, which is the reasoned decision, not an oversight
    sql = ('sql = """\n'
           'SELECT * FROM Db.SCHEMA.Parcels\n'
           'JOIN Db.SDE.Lookup ON 1=1\n'
           '"""\n')
    f = scan_source(sql, strip)
    check(replaces(f) == [] and len(reviews(f)) == 2,
          "a qualifier inside embedded SQL is reviewed, never stripped  "
          "<-- pinned defect")
    check("still valid" in reviews(f)[0].note,
          "and the report says why: the qualifier still resolves in SQL")
    prose = 'sql = """\nDb.SCHEMA.Parcels\nis the layer\n"""\n'
    check(len(replaces(scan_source(prose, strip))) == 1,
          "the same triple-quoted literal with no SQL keyword IS replaced, so "
          "the demotion is about the SQL and not about the quoting")
    low = 'sql = """\nselect * from Db.SCHEMA.Parcels\n"""\n'
    check(kinds(scan_source(low, strip)) == [REVIEW],
          "lower-case SQL demotes the same way, because nobody writes the "
          "keywords in capitals twice  <-- pinned defect")
    f = scan_source('fc = "Db.SCHEMA.UPDATED_ROWS"\n', strip)
    check(kinds(f) == [REPLACE],
          "UPDATED_ROWS is not the UPDATE keyword, so the SQL demotion needs a "
          "whole word and not a substring  <-- pinned defect")

    # ---- a computed object name is surfaced, never guessed at
    f = scan_source('fc = f"Db.SDE.{layer}"\n', strip)
    check(kinds(f) == [REVIEW] and f[0].before == "Db.SDE.",
          "an f-string qualifier with a computed object is reviewed")
    check("'SDE.'" in f[0].note,
          "and the note suggests the qualifier to write instead")
    f = scan_source('fc = "Db.SDE." + name\n', strip)
    check(kinds(f) == [REVIEW],
          "a concatenated object name is reviewed")
    f = scan_source('fc = "Db.SDE.%s" % name\n', strip)
    check(kinds(f) == [REVIEW],
          "a percent-formatted object name is reviewed")
    f = scan_source('fc = "Db.SDE.Lookup"\n', strip)
    check(kinds(f) == [REPLACE],
          "a literal object name is a replacement ONLY, never also a computed "
          "review  <-- pinned defect")
    f = scan_source('fc = "Db.SDE." + name  # Db.SDE.Lookup was the old name\n',
                    strip)
    check(len(f) == 1,
          "the comment on the same line as a computed qualifier stays out of it")
    check(scan_source("# Db.SDE. was the prefix\n", strip) == [],
          "a computed qualifier written in a comment is left alone")
    check(scan_source('fc = "xDb.SDE." + name\n', strip) == [],
          "and one running on from the word before it is not reported either")

    # ---- rename rules: the whole-value cases
    f = scan_source('srv = "OLDHOST"\n', host)
    check(kinds(f) == [REPLACE] and f[0].after == "newhost.example.org",
          "a bare old value in a literal is a replacement")
    f = scan_source('srv = "oldhost"\n', host)
    check(replaces(f)[0].after == "newhost.example.org",
          "the match ignores case and the replacement uses the value given")
    f = scan_source('srv = "XOLDHOSTX"\n', host)
    check(kinds(f) == [REVIEW] and "longer token" in f[0].note,
          "the old value inside a longer token is reviewed, not rewritten")
    f = scan_source('srv = "srvOLDHOST"\n', host)
    check(kinds(f) == [REVIEW] and "longer token" in f[0].note,
          "the old value welded to the word BEFORE it is reviewed too, so the "
          "left edge is really tested and not assumed  <-- pinned defect")
    f = scan_source('srv = "a.sde""OLDHOST"\n', host)
    check(kinds(f) == [REPLACE],
          "a hit starting exactly where the previous literal ended belongs to "
          "the NEW literal, so the .sde beside it does not claim it  "
          "<-- pinned defect")
    f = scan_source('srv = "OLDHOST.example.org"\n', host)
    check(kinds(f) == [REVIEW],
          "an already-qualified host is reviewed, never doubled into "
          "newhost.example.org.example.org  <-- pinned defect")
    f = scan_source('p = r"C:\\conn\\SQLServer-OLDHOST-GIS.sde"\n', host)
    check(kinds(f) == [REVIEW] and ".sde" in f[0].note,
          "the old value inside a connection-file literal is reviewed  "
          "<-- pinned defect")
    check("longer token" not in f[0].note,
          "and the connection-file reason wins over the token reason, because "
          "it is the more useful one  <-- pinned defect")
    f = scan_source('OLDHOST_PATH = "value"\n', host)
    check(kinds(f) == [REVIEW] and "identifier" in f[0].note,
          "a variable named after the old value is reviewed, never renamed")
    f = scan_source("# OLDHOST was the old server\n", host)
    check(f == [], "the old value in a comment is left entirely alone")
    plain = Rules(renames=[("OLDHOST", "newhost.example.org")])
    f = scan_source('p = r"C:\\conn\\SQLServer-OLDHOST-GIS.sde"\n', plain)
    check("longer token" in f[0].note,
          "without --review-suffix the same literal falls through to the token "
          "rule, so the suffix flag is doing the work")

    # ---- rules validate their own inputs
    raises(lambda: Rules(), "no rules at all is refused")
    raises(lambda: Rules(renames=[("", "x")]), "an empty old value is refused")
    raises(lambda: Rules(renames=[("a", "a")]),
           "renaming a value to itself is refused")
    raises(lambda: Rules(strip_prefixes=["SCHEMA"]),
           "a strip prefix with nothing to strip is refused")
    raises(lambda: _rename_pair("OLDHOST"),
           "a --rename without an equals sign is refused")
    raises(lambda: _rename_pair("=newhost"),
           "a --rename with an empty old value is refused")
    check(_rename_pair("a=b=c") == ("a", "b=c"),
          "a --rename splits on the FIRST equals sign, so the new value may "
          "hold one")

    # ---- applying findings
    src = 'a = "Db.SCHEMA.One"  # Db.SCHEMA.One is the old name\n'
    out = apply_findings(src, scan_source(src, strip))
    check(out == 'a = "SCHEMA.One"  # Db.SCHEMA.One is the old name\n',
          "the comment beside a rewritten literal survives the edit  "
          "<-- pinned defect")
    src = 'a = "Db.SCHEMA.One" + "Db.SDE.Two"\n'
    out = apply_findings(src, scan_source(src, strip))
    check(out == 'a = "SCHEMA.One" + "SDE.Two"\n',
          "two hits on one line both land, because the line is edited right to "
          "left  <-- pinned defect")
    check(apply_findings(out, scan_source(out, strip)) == out,
          "a second pass over the result changes nothing")
    src = 'OLDHOST_PATH = "OLDHOST"\n'
    out = apply_findings(src, scan_source(src, host))
    check(out == 'OLDHOST_PATH = "newhost.example.org"\n',
          "the identifier is untouched while the literal beside it is rewritten "
          " <-- pinned defect")
    check(apply_findings(sql, scan_source(sql, strip)) == sql,
          "a source whose only findings are REVIEW is returned unchanged")
    stale = scan_source('a = "Db.SCHEMA.One"\n', strip)
    raises(lambda: apply_findings('a = "something else entirely"\n', stale),
           "applying a stale finding to moved source raises  <-- pinned defect")
    stale2 = scan_source('a = 1\nb = "Db.SCHEMA.One"\n', strip)
    raises(lambda: apply_findings("a = 1\n", stale2),
           "a finding past the end of the source raises  <-- pinned defect")
    # A CR-only file is the one place where the scanner's idea of a line and
    # str.splitlines' idea of a line disagree. The column check catches it, so
    # it refuses instead of writing the replacement into the wrong place.
    cr = 'a = 1\rb = "Db.SCHEMA.One"\r'
    raises(lambda: apply_findings(cr, scan_source(cr, strip)),
           "an old-Mac CR-only file is refused, not written wrong  "
           "<-- pinned defect")

    # ---- the pre-flight
    done = {"a.py": 'fc = "SCHEMA.Parcels"\n'}
    hits = preflight(done, strip)
    check(len(hits) == 1 and not hits[0].strong,
          "a bare two-part name on its own is only a weak hint")
    mixed = {"a.py": 'fc = "SCHEMA.Parcels"\n',
             "b.py": 'fc = "Db.SCHEMA.Parcels"\n'}
    hits = [h for h in preflight(mixed, strip) if h.strong]
    check(len(hits) == 1 and hits[0].name == "a.py",
          "the same object still written in full elsewhere makes it strong  "
          "<-- pinned defect")
    check(preflight({"b.py": 'fc = "Db.SCHEMA.Parcels"\n'}, strip) == [],
          "a tree that still carries full qualifiers raises no pre-flight hit")
    check(preflight({"a.py": 'x = "Db.SCHEMA.Parcels"\ny = "SCHEMA.Lookup"\n'},
                    strip) == [],
          "and a file holding a full qualifier raises none for the bare name "
          "sitting beside it  <-- pinned defect")
    check(preflight({"a.py": "# SCHEMA.Parcels\n"}, strip) == [],
          "a bare name in a comment is not pre-flight evidence")
    check(preflight({"a.py": 'x = """bad\n'}, strip) == [],
          "a file the tokenizer refuses is skipped by the pre-flight, not "
          "guessed at")
    hits = preflight({"a.py": 'srv = "newhost.example.org"\n'}, host)
    check(len(hits) == 1 and hits[0].strong,
          "a renamed file with the old value nowhere in the tree is strong")
    hits = preflight({"a.py": 'srv = "newhost.example.org"\n',
                      "b.py": 'srv = "OLDHOST"\n'}, host)
    check(len(hits) == 1 and not hits[0].strong,
          "with the old value still in the tree the same file is only a hint")
    check(preflight({"a.py": 'srv = "other"\n'}, host) == [],
          "a file mentioning neither value raises nothing")
    many = dict(("f%02d.py" % i, 'srv = "newhost.example.org"\n')
                for i in range(21))
    report = describe_preflight(preflight(many, host), False)
    text = "\n".join(report)
    check("... and 1 more" in text,
          "a pre-flight over 21 files lists 20 and counts the rest")
    listed = [l for l in report
              if l.startswith("  STRONG") or l.startswith("  weak  ")]
    check(len(listed) == 20,
          "and really does list 20 of them, so the count and the list agree  "
          "<-- pinned defect")

    # ---- backup naming
    check(excluded("a/b/legacy_old.py", ["*legacy*"]),
          "an exclude glob matches a path")
    check(excluded("legacy_old.py", ["*legacy*"]),
          "and matches a bare file name")
    check(not excluded("a/b/current.py", ["*legacy*"]),
          "and leaves everything else alone")
    check(excluded("A/B/LEGACY.py", ["*legacy*"]),
          "exclude globs ignore case, so one pattern works on both platforms")
    check(excluded("a/b/legacy.py", ["legacy.py"]),
          "a bare file name excludes that file anywhere in the walk, not only "
          "at the top of it  <-- pinned defect")
    check(excluded("vendor/legacy.py", ["vendor\\legacy.py"]),
          "a pattern typed with backslashes matches a path spelled with "
          "slashes  <-- pinned defect")

    # ---- argument parsing, before anything can be written
    args = _parse(["scripts"])
    check(args.apply is False, "--apply is OFF unless asked for  <-- pinned defect")
    check(args.force is False, "--force is OFF unless asked for  <-- pinned defect")
    check(args.self_test is False, "--self-test is OFF unless asked for")
    check(args.rename == [] and args.strip_prefix == [],
          "no rename rule is assumed")
    check(args.review_suffix == [], "no review suffix is assumed")
    check(tuple(args.ext) == DEFAULT_EXTENSIONS,
          "the default extensions are the Python ones")
    args = _parse(["a", "b", "--apply", "--force", "--rename", "X=Y",
                   "--rename", "P=Q", "--strip-prefix", "Db.S",
                   "--review-suffix", ".sde", "--ext", ".pyt",
                   "--exclude", "vendor/*"])
    check(args.paths == ["a", "b"], "paths are collected")
    check(args.apply is True and args.force is True,
          "both switches are read from the command line")
    check(args.rename == [("X", "Y"), ("P", "Q")], "--rename repeats")
    check(args.strip_prefix == ["Db.S"], "--strip-prefix is read")
    check(args.review_suffix == [".sde"], "--review-suffix is read")
    check(args.ext == [".pyt"], "--ext replaces the default rather than adding")
    check(args.exclude == ["vendor/*"], "--exclude is read")
    # argparse turns the ValueError from a type= callable into its own exit,
    # and that exit code is the 2 the README documents for a rejected flag.
    exits = []
    try:
        capture(lambda: _parse(["x", "--rename", "NOEQUALS"]))
    except SystemExit as exc:
        exits.append(exc.code)
    check(exits == [2],
          "a --rename with no equals sign exits 2, the code the README "
          "documents for a rejected flag value  <-- pinned defect")

    # ---- the file layer
    tmp = tempfile.mkdtemp(prefix="litswap-selftest-")
    try:
        def write(name, data):
            path = os.path.join(tmp, name)
            parent = os.path.dirname(path)
            if not os.path.isdir(parent):
                os.makedirs(parent)
            handle = open(path, "wb")
            handle.write(data)
            handle.close()
            return path

        good = write("good.py", b'srv = "OLDHOST"\n')
        crlf = write("crlf.py", b'srv = "OLDHOST"\r\nx = 1\r\n')
        bom = write("bom.py", b'\xef\xbb\xbfsrv = "OLDHOST"\n')
        bad = write("bad.py", b'srv = "OLDHOST \xff\xfe"\n')
        broken = write("broken.py", b'sql = """unterminated\n')
        skipped = write("vendor/legacy.py", b'srv = "OLDHOST"\n')
        notes = write("notes.txt", b'srv = "OLDHOST"\n')
        write("__pycache__/cached.py", b'srv = "OLDHOST"\n')
        write(".hidden/secret.py", b'srv = "OLDHOST"\n')

        files = discover([tmp], DEFAULT_EXTENSIONS, [])
        check(len(files) == 6,
              "the walk finds six .py files and skips the .txt")
        check(not any("__pycache__" in _posix(p) or "/.hidden/" in _posix(p)
                      for p in files),
              "__pycache__ and a dotted directory are never descended into  "
              "<-- pinned defect")
        check(discover([good, good], DEFAULT_EXTENSIONS, []) == [good],
              "the same file named twice is scanned once")
        check(discover([tmp], ["txt"], []) == [notes],
              "--ext takes an extension with or without its leading dot  "
              "<-- pinned defect")
        kept = discover([tmp], DEFAULT_EXTENSIONS, ["vendor/*"])
        check(len(kept) == 5
              and not any("/vendor/" in _posix(p) for p in kept),
              "an excluded directory is left out of the walk")
        check(discover([skipped], DEFAULT_EXTENSIONS, ["legacy.py"]) == [],
              "and pointing --exclude's own target straight at litswap still "
              "excludes it  <-- pinned defect")
        check(discover([skipped], DEFAULT_EXTENSIONS, []) == [skipped],
              "while the same file with no exclude is scanned")

        result = read_file(tmp)
        check("read failed" in result.error,
              "a directory handed in where a file was expected is an error, "
              "not a traceback")

        result = scan_result(read_file(bad), host)
        check("not valid UTF-8" in result.error and result.findings == [],
              "a file that is not UTF-8 is an error with no findings  "
              "<-- pinned defect")
        result = scan_result(read_file(broken), host)
        check("cannot tokenize" in result.error and result.findings == [],
              "a file the tokenizer refuses is an error with no findings  "
              "<-- pinned defect")

        before = open(good, "rb").read()
        code, out = capture(lambda: main([tmp, "--rename",
                                          "OLDHOST=newhost.example.org"]))
        check(code == 1, "a dry run over findings exits 1")
        check(open(good, "rb").read() == before,
              "and writes nothing at all  <-- pinned defect")
        check("Nothing was written" in out, "and says so")
        check("pending" in out, "and calls the replacements pending")
        check(out.count("ERROR") == 2,
              "both unreadable files are reported by name, not skipped in "
              "silence  <-- pinned defect")

        code, out = capture(lambda: main([tmp, "--rename",
                                          "OLDHOST=newhost.example.org",
                                          "--apply"]))
        check(code == 1, "an apply that leaves errors behind still exits 1")
        check(open(good, "rb").read() == b'srv = "newhost.example.org"\n',
              "the replacement really is written")
        check(open(good + ".bak", "rb").read() == before,
              "and the backup holds the original bytes")
        check(open(crlf, "rb").read()
              == b'srv = "newhost.example.org"\r\nx = 1\r\n',
              "CRLF line endings survive the rewrite  <-- pinned defect")
        check(open(bom, "rb").read()
              == b'\xef\xbb\xbfsrv = "newhost.example.org"\n',
              "a UTF-8 BOM survives the rewrite  <-- pinned defect")
        check(open(bad, "rb").read() == b'srv = "OLDHOST \xff\xfe"\n',
              "the file that could not be decoded was not touched")
        check(open(broken, "rb").read() == b'sql = """unterminated\n',
              "and neither was the one that could not be tokenized")
        check(not os.path.exists(bad + ".bak"),
              "a file that is never written gets no backup")

        # second run over the same tree: the .bak is already there
        write(good, b'srv = "OLDHOST"\n')
        result = apply_result(scan_result(read_file(good), host))
        check(result.applied and result.backup_path != good + ".bak",
              "an existing backup is never clobbered  <-- pinned defect")
        check(open(good + ".bak", "rb").read() == before,
              "the first backup still holds what it held")
        check(open(result.backup_path, "rb").read() == b'srv = "OLDHOST"\n',
              "and the second backup holds the second original")

        # two backups inside the same second, so the timestamp alone collides
        stamped = write("stamped.py", b'srv = "OLDHOST"\n')
        open(stamped + ".bak", "wb").close()
        nxt = ""
        for _ in range(3):
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            open("%s.%s.bak" % (stamped, stamp), "wb").close()
            nxt = backup_path_for(stamped)
            if nxt.startswith("%s.%s-" % (stamped, stamp)):
                break
        check(nxt == "%s.%s-1.bak" % (stamped, stamp),
              "a third backup inside the same second gets a counter rather "
              "than overwriting the second  <-- pinned defect")

        # the backup goes first, so a backup that cannot be written stops the
        # rewrite instead of following it
        # The backup is aimed at a directory that does not exist, so creating it
        # is the step that fails. Nothing else in the run is touched.
        first = write("first.py", b'srv = "OLDHOST"\n')
        held_backup = globals()["backup_path_for"]
        globals()["backup_path_for"] = (
            lambda path: os.path.join(path + ".nodir", "x.bak"))
        try:
            result = apply_result(scan_result(read_file(first), host))
        finally:
            globals()["backup_path_for"] = held_backup
        check("write failed" in result.error and not result.applied,
              "a backup that cannot be written is one clear error  "
              "<-- pinned defect")
        check(open(first, "rb").read() == b'srv = "OLDHOST"\n',
              "and the file it was for still holds the original, because the "
              "backup is written before it  <-- pinned defect")

        # a write that fails AFTER the backup exists takes the backup with it
        orphan = write("orphan.py", b'srv = "OLDHOST"\n')

        def _boom(_path, _data):
            raise IOError("no space left on device")

        held_write = globals()["write_atomic"]
        globals()["write_atomic"] = _boom
        try:
            result = apply_result(scan_result(read_file(orphan), host))
        finally:
            globals()["write_atomic"] = held_write
        check("no space left" in result.error and not result.applied,
              "a write that fails after the backup exists is reported, not "
              "swallowed")
        check(result.backup_path is None
              and not os.path.exists(orphan + ".bak"),
              "and the backup it had already made is removed, so no orphan "
              ".bak is left behind  <-- pinned defect")
        check(open(orphan, "rb").read() == b'srv = "OLDHOST"\n',
              "with the file itself untouched")

        # a file nobody can write
        locked = write("locked.py", b'srv = "OLDHOST"\n')
        os.chmod(locked, 0o444)
        result = apply_result(scan_result(read_file(locked), host))
        check("not writable" in result.error and not result.applied,
              "an unwritable file is one clear error  <-- pinned defect")
        check(open(locked, "rb").read() == b'srv = "OLDHOST"\n',
              "with the file unchanged")
        check(not os.path.exists(locked + ".bak"),
              "and no orphan backup left beside it  <-- pinned defect")
        os.chmod(locked, 0o644)

        # a finding that no longer describes its own source
        drifted = scan_result(read_file(locked), host)
        drifted.findings = [Finding(REPLACE, 1, 1, "OLDHOST",
                                    "newhost.example.org", "")]
        apply_result(drifted)
        check("apply refused" in drifted.error and not drifted.applied,
              "a finding that no longer matches the text aborts the write  "
              "<-- pinned defect")

        # the file moving under the scan
        moved = scan_result(read_file(locked), host)
        handle = open(locked, "wb")
        handle.write(b'srv = "OLDHOST"  # edited by somebody else\n')
        handle.close()
        apply_result(moved)
        check("changed on disk" in moved.error and not moved.applied,
              "a file edited between the scan and the write is refused  "
              "<-- pinned defect")
        check(open(locked, "rb").read()
              == b'srv = "OLDHOST"  # edited by somebody else\n',
              "and the other edit survives")

        # the atomic write, when the replace itself cannot happen
        scratch = os.path.join(tmp, "scratch")
        os.makedirs(scratch)
        before_names = sorted(os.listdir(tmp))
        raised = []
        try:
            write_atomic(scratch, b"data")
        except OSError:
            raised.append(True)
        check(raised == [True],
              "the atomic write raises when the replace cannot be done")

        # a failure EARLIER, while the handle is still open. On Windows the
        # temporary file cannot be removed unless that handle was closed first,
        # so the assertion below fails if the cleanup skips the close.
        raised = []
        try:
            write_atomic(good, u"not bytes")
        except TypeError:
            raised.append(True)
        check(raised == [True],
              "and raises when the write itself cannot be done")
        check(sorted(os.listdir(tmp)) == before_names,
              "and takes its temporary file with it  <-- pinned defect")

        # the pre-flight over a tree that has already been converted
        done_dir = os.path.join(tmp, "done")
        write("done/a.py", b'srv = "newhost.example.org"\n')
        code, out = capture(lambda: main([done_dir, "--rename",
                                          "OLDHOST=newhost.example.org",
                                          "--apply"]))
        check(code == 1 and "Refusing to --apply" in out,
              "strong pre-flight evidence stops an apply  <-- pinned defect")
        check(open(os.path.join(done_dir, "a.py"), "rb").read()
              == b'srv = "newhost.example.org"\n',
              "and the tree is untouched")
        code, out = capture(lambda: main([done_dir, "--rename",
                                          "OLDHOST=newhost.example.org",
                                          "--apply", "--force"]))
        check("--force given" in out and "Refusing" not in out,
              "--force is read and overrides it")
        write("done/b.py", b'srv = "OLDHOST"\n')
        code, out = capture(lambda: main([done_dir, "--rename",
                                          "OLDHOST=newhost.example.org"]))
        check("weak hint" in out and "Refusing" not in out,
              "a weak hint warns and carries on, so nobody reaches for --force "
              "by habit  <-- pinned defect")

        # --force over a tree that still has real work in it, so the switch is
        # measured by what reaches the disk and not only by what it prints
        force_dir = os.path.join(tmp, "force")
        write("force/a.py", b'fc = "SCHEMA.Parcels"\n')
        forced = write("force/b.py", b'fc = "Db.SCHEMA.Parcels"\n')
        code, out = capture(lambda: main([force_dir, "--strip-prefix",
                                          "Db.SCHEMA", "--apply"]))
        check(code == 1 and open(forced, "rb").read()
              == b'fc = "Db.SCHEMA.Parcels"\n',
              "a refused pre-flight leaves the file it would have rewritten "
              "exactly as it was  <-- pinned defect")
        code, out = capture(lambda: main([force_dir, "--strip-prefix",
                                          "Db.SCHEMA", "--apply", "--force"]))
        check(open(forced, "rb").read() == b'fc = "SCHEMA.Parcels"\n',
              "and --force really does let that write through  "
              "<-- pinned defect")

        # --ext end to end, not only as a parsed flag
        ext_dir = os.path.join(tmp, "extonly")
        ext_file = write("extonly/a.txt", b'srv = "OLDHOST"\n')
        code, out = capture(lambda: main([ext_dir, "--rename",
                                          "OLDHOST=newhost.example.org"]))
        check(code == 0 and "no files" in out,
              "a .txt is not scanned by default")
        code, out = capture(lambda: main([ext_dir, "--rename",
                                          "OLDHOST=newhost.example.org",
                                          "--ext", "txt", "--apply"]))
        check(open(ext_file, "rb").read() == b'srv = "newhost.example.org"\n',
              "and --ext txt scans and rewrites the same file  "
              "<-- pinned defect")

        # a clean tree
        clean = os.path.join(tmp, "clean")
        write("clean/a.py", b'srv = "something else"\n')
        code, out = capture(lambda: main([clean, "--rename",
                                          "OLDHOST=newhost.example.org"]))
        check(code == 0 and "0 replacement" in out,
              "a tree with nothing to rename exits 0")

        # a tree with replacements and nothing else
        plain_dir = os.path.join(tmp, "plain")
        plain_file = write("plain/a.py", b'srv = "OLDHOST"\n')
        code, out = capture(lambda: main([plain_dir, "--rename",
                                          "OLDHOST=newhost.example.org"]))
        check(code == 1, "pending replacements alone exit 1, so a dry run gates "
                         "a build  <-- pinned defect")
        code, out = capture(lambda: main([plain_dir, "--rename",
                                          "OLDHOST=newhost.example.org",
                                          "--apply"]))
        check(code == 0, "and an apply that leaves nothing outstanding exits 0")
        check(open(plain_file, "rb").read()
              == b'srv = "newhost.example.org"\n',
              "with the work really done")

        # the strip rule end to end
        strip_dir = os.path.join(tmp, "strip")
        target = write("strip/a.py",
                       b'fc = "Db.SCHEMA.Parcels"  # Db.SCHEMA.Parcels was here\n'
                       b'sql = "SELECT 1 FROM Db.SCHEMA.Parcels"\n')
        code, out = capture(lambda: main([strip_dir, "--strip-prefix",
                                          "Db.SCHEMA", "--apply"]))
        check(code == 1, "the run exits 1 because a review item is outstanding")
        check(open(target, "rb").read()
              == b'fc = "SCHEMA.Parcels"  # Db.SCHEMA.Parcels was here\n'
                 b'sql = "SELECT 1 FROM Db.SCHEMA.Parcels"\n',
              "--strip-prefix rewrites the path, keeps the comment and keeps "
              "the SQL  <-- pinned defect")
        check("still valid" in out, "and the report explains the SQL one")

        # usage errors
        code, out = capture(lambda: main([]))
        check(code == 64, "no paths is a usage error")
        check("pass files or directories" in out,
              "and says which usage error it was, rather than falling through "
              "to the no-rules message  <-- pinned defect")
        code, out = capture(lambda: main([tmp]))
        check(code == 64, "no rules is a usage error")
        code, out = capture(lambda: main([os.path.join(tmp, "nope"),
                                          "--rename", "A=B"]))
        check(code == 64, "a path that does not exist is a usage error, not a "
                          "clean pass  <-- pinned defect")
        code, out = capture(lambda: main([tmp, "--strip-prefix", "SCHEMA"]))
        check(code == 64, "an unusable rule is a usage error")
        empty = os.path.join(tmp, "empty")
        os.makedirs(empty)
        code, out = capture(lambda: main([empty, "--rename", "A=B"]))
        check(code == 0 and "no files" in out,
              "a directory with no Python in it exits 0 and says so")

        # the harness itself
        mark = len(failed)
        check(False, "a deliberately failing assertion")
        probe = failed[mark:]
        del failed[mark:]
        check(probe == ["a deliberately failing assertion"],
              "check() really does record a failure  <-- pinned defect")
        mark = len(failed)
        raises(lambda: None, "a call that does not raise")
        probe = failed[mark:]
        del failed[mark:]
        check(probe == ["a call that does not raise (no error raised)"],
              "raises() really does record a call that did not raise  "
              "<-- pinned defect")
        mark = len(failed)
        raises(lambda: 1 // 0, "a call that raises the wrong thing")
        probe = failed[mark:]
        del failed[mark:]
        check(len(probe) == 1 and "wrong exception" in probe[0],
              "and reports an exception that is not the one it asked for  "
              "<-- pinned defect")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    check(not os.path.isdir(tmp),
          "the self-test leaves no temporary directory behind  <-- pinned defect")

    print("-" * 68)
    total = passed[0] + len(failed)
    if failed:
        print("%d assertions, %d failed" % (total, len(failed)))
        for label in failed:
            print("  FAILED: %s" % label)
        return 1
    print("%d assertions, 0 failed" % total)
    return 0


# ------------------------------------------------------------------------ cli

def _rename_pair(text):
    """Parse OLD=NEW. Raises ValueError, which argparse turns into a usage error."""
    old, sep, new = text.partition("=")
    if not sep or not old:
        raise ValueError("expected OLD=NEW, got %r" % text)
    return (old, new)


def _parse(argv):
    ap = argparse.ArgumentParser(
        prog="litswap.py",
        description="Rename a string constant across a tree of Python "
                    "scripts, and report every case the rules cannot decide.",
        epilog="Nothing is written without --apply, and every file written is "
               "backed up first.",
    )
    ap.add_argument("paths", nargs="*",
                    help="files or directories to scan")
    ap.add_argument("--rename", action="append", default=[], type=_rename_pair,
                    metavar="OLD=NEW",
                    help="replace OLD with NEW inside string literals. "
                         "Repeatable.")
    ap.add_argument("--strip-prefix", dest="strip_prefix", action="append",
                    default=[], metavar="QUALIFIER.KEPT",
                    help="drop the leading qualifier from QUALIFIER.KEPT.OBJECT "
                         "so KEPT.OBJECT remains. Repeatable.")
    ap.add_argument("--review-suffix", dest="review_suffix", action="append",
                    default=[], metavar="SUFFIX",
                    help="never rewrite inside a literal ending in SUFFIX; "
                         "report it instead. Repeatable. Use .sde for ArcGIS "
                         "connection files.")
    ap.add_argument("--ext", action="append", default=[], metavar="EXT",
                    help="file extension to scan, replacing the default "
                         "%s. Repeatable." % ", ".join(DEFAULT_EXTENSIONS))
    ap.add_argument("--exclude", action="append", default=[], metavar="GLOB",
                    help="skip paths matching this glob. Repeatable.")
    ap.add_argument("--force", action="store_true",
                    help="apply even when the pre-flight finds strong evidence "
                         "the rename already ran")
    ap.add_argument("--apply", action="store_true",
                    help="write the replacements. Without this nothing is "
                         "written.")
    ap.add_argument("--self-test", dest="self_test", action="store_true",
                    help="run the assertions and exit. Needs no data.")
    args = ap.parse_args(argv)
    if not args.ext:
        args.ext = list(DEFAULT_EXTENSIONS)
    return args


def main(argv=None):
    args = _parse(sys.argv[1:] if argv is None else argv)

    if args.self_test:
        return self_test()

    if not args.paths:
        print("error: pass files or directories to scan. Use --self-test to "
              "check the tool without any.", file=sys.stderr)
        return 64
    missing = [p for p in args.paths if not os.path.exists(p)]
    if missing:
        print("error: no such file or directory: %s" % ", ".join(missing),
              file=sys.stderr)
        return 64
    try:
        rules = Rules(args.rename, args.strip_prefix, args.review_suffix)
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 64

    files = discover(args.paths, args.ext, args.exclude)
    if not files:
        print("litswap: no files to scan under %s" % ", ".join(args.paths))
        return 0

    results = [read_file(path) for path in files]
    readable = dict((r.path, r.text) for r in results if r.text is not None)

    hits = preflight(readable, rules)
    if hits:
        for line in describe_preflight(hits, args.force):
            print(line)
        if any(h.strong for h in hits) and args.apply and not args.force:
            print("Nothing was written.")
            return 1

    for result in results:
        scan_result(result, rules)
        if args.apply:
            apply_result(result)

    print("")
    print("litswap: %d file(s) scanned" % len(results))
    for line in describe(results, args.apply):
        print(line)

    # 1 means work is outstanding, so a dry run with replacements pending gates
    # a build the same way a review item does. A completed --apply that left no
    # review item behind has nothing outstanding, so it is 0.
    if any(r.error for r in results):
        return 1
    if any(r.review_count for r in results):
        return 1
    if not args.apply and any(r.replace_count for r in results):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
