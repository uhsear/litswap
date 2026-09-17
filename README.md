# litswap

Rename a string constant across a tree of Python scripts, and report every case the rules cannot decide.

A database moves to a new server. Three hundred production scripts carry the old name as a string
literal, so somebody runs the obvious command:

```
grep -rl OLDSQL . | xargs sed -i 's/OLDSQL/newsql.example.org/g'
```

That command is wrong three ways in the same pass. It rewrites the comment that explained what
`OLDSQL` was, so the only record of the old name is gone. It rewrites `OLDSQL_FALLBACK`, a variable
named after the server, and now that name means nothing. It rewrites `OLDSQL.example.org`, which was
already correct, into `newsql.example.org.example.org`. In a fourth file it reaches inside a
triple-quoted `SELECT` where the old qualifier still resolves, and breaks a query that worked.

Nothing fails that afternoon. Every script still parses and still imports. They fail at 2am, one at
a time, on a schedule, and the person on call has no idea a rename happened.

litswap edits inside string literals only, and it asks Python's own tokenizer where those are. When a
hit is ambiguous it reports the hit and changes nothing. Nothing is written without `--apply`.

```
$ python litswap.py --self-test
litswap self-test: no network, no database, no arcpy
--------------------------------------------------------------------
PASS  a quote inside a comment is comment, not a second literal
PASS  an f-string is ONE span on every Python version  <-- pinned defect
PASS  and that span STARTS at the outer f-string, not at the inner one  <-- pinned defect
PASS  findings come out in line then column order whichever rule found them, which is the order the report prints and --apply walks  <-- pinned defect
PASS  an unterminated triple quote raises  <-- pinned defect
PASS  scan_source raises rather than scanning a partial mask  <-- pinned defect
PASS  the casing the script wrote is kept, not the casing on the command line  <-- pinned defect
PASS  a backslash-separated double qualifier gives TWO replacements  <-- pinned defect
PASS  a dot-joined double qualifier is fully reduced, not left half done  <-- pinned defect
PASS  a qualified name in a comment is neither replaced nor reviewed  <-- pinned defect
PASS  a qualifier inside embedded SQL is reviewed, never stripped  <-- pinned defect
PASS  lower-case SQL demotes the same way, because nobody writes the keywords in capitals twice  <-- pinned defect
PASS  UPDATED_ROWS is not the UPDATE keyword, so the SQL demotion needs a whole word and not a substring  <-- pinned defect
...
PASS  a literal object name is a replacement ONLY, never also a computed review  <-- pinned defect
PASS  the old value welded to the word BEFORE it is reviewed too, so the left edge is really tested and not assumed  <-- pinned defect
PASS  a hit starting exactly where the previous literal ended belongs to the NEW literal, so the .sde beside it does not claim it  <-- pinned defect
PASS  an already-qualified host is reviewed, never doubled into newhost.example.org.example.org  <-- pinned defect
PASS  and the connection-file reason wins over the token reason, because it is the more useful one  <-- pinned defect
PASS  the comment beside a rewritten literal survives the edit  <-- pinned defect
PASS  two hits on one line both land, because the line is edited right to left  <-- pinned defect
PASS  the identifier is untouched while the literal beside it is rewritten  <-- pinned defect
PASS  an old-Mac CR-only file is refused, not written wrong  <-- pinned defect
PASS  the same object still written in full elsewhere makes it strong  <-- pinned defect
PASS  a bare file name excludes that file anywhere in the walk, not only at the top of it  <-- pinned defect
PASS  --apply is OFF unless asked for  <-- pinned defect
PASS  a --rename with no equals sign exits 2, the code the README documents for a rejected flag value  <-- pinned defect
...
PASS  __pycache__ and a dotted directory are never descended into  <-- pinned defect
PASS  --ext takes an extension with or without its leading dot  <-- pinned defect
PASS  a file that is not UTF-8 is an error with no findings  <-- pinned defect
PASS  both unreadable files are reported by name, not skipped in silence  <-- pinned defect
PASS  CRLF line endings survive the rewrite  <-- pinned defect
PASS  a UTF-8 BOM survives the rewrite  <-- pinned defect
PASS  an existing backup is never clobbered  <-- pinned defect
PASS  a third backup inside the same second gets a counter rather than overwriting the second  <-- pinned defect
PASS  and the file it was for still holds the original, because the backup is written before it  <-- pinned defect
PASS  and the backup it had already made is removed, so no orphan .bak is left behind  <-- pinned defect
PASS  a finding that no longer matches the text aborts the write  <-- pinned defect
PASS  a file edited between the scan and the write is refused  <-- pinned defect
PASS  strong pre-flight evidence stops an apply  <-- pinned defect
PASS  a weak hint warns and carries on, so nobody reaches for --force by habit  <-- pinned defect
PASS  and --force really does let that write through  <-- pinned defect
PASS  and --ext txt scans and rewrites the same file  <-- pinned defect
...
PASS  the self-test leaves no temporary directory behind  <-- pinned defect
--------------------------------------------------------------------
167 assertions, 0 failed
```

## Requirements

Python 3.9 or newer. Nothing to install, no `arcpy`, no third-party package. It runs on ArcGIS Pro's
Python and on a plain `python3` equally. The same 167 assertions pass on Windows and on Linux.

```
git clone https://github.com/uhsear/litswap.git
```

## Quick start

```
python litswap.py --self-test
```

## Usage

Scan first. Nothing is written without `--apply`.

```
python litswap.py scripts/ --rename "OLDSQL=newsql.example.org"
python litswap.py scripts/ --rename "OLDSQL=newsql.example.org" --apply
python litswap.py scripts/ --strip-prefix OldDb.SDE --review-suffix .sde
```

| Flag | Default | What it does |
|---|---|---|
| `--rename OLD=NEW` | none | Replace OLD with NEW inside string literals. Repeatable. |
| `--strip-prefix QUALIFIER.KEPT` | none | Drop the leading qualifier, so `QUALIFIER.KEPT.OBJECT` becomes `KEPT.OBJECT`. Repeatable. |
| `--review-suffix SUFFIX` | none | Never rewrite inside a literal ending in SUFFIX. Report it instead. Repeatable. |
| `--ext EXT` | `.py`, `.pyt` | Extension to scan, with or without its leading dot. Passing any replaces both defaults. Repeatable. |
| `--exclude GLOB` | none | Skip paths matching this glob. Repeatable. |
| `--force` | off | Apply although the pre-flight found strong evidence the rename already ran. |
| `--apply` | off | Write the replacements. Without it nothing is written. |
| `--self-test` | off | Run the assertions and exit. Needs no data. |

You must pass at least one `--rename` or `--strip-prefix`. The tool ships with no rule of its own, no
hostname and no database name.

## What it checks

This ten-line file carries one case of every rule. The report is the real output, not a sketch.

```python
# OLDSQL was the reporting server until the move.
OLDSQL_FALLBACK = "unused"
SERVER = "OLDSQL"
FQDN = "OLDSQL.example.org"
CONN = r"C:\conn\SQLServer-OLDSQL-Gis.sde"
QUERY = """
SELECT OBJECTID FROM OldDb.SDE.Parcels
"""
PATH = r"C:\gis\OldDb.SDE.Area\OldDb.Sde.Roads"
LAYER = f"OldDb.SDE.{name}"
```

```
$ python litswap.py nightly.py --rename "OLDSQL=newsql.example.org" \
      --strip-prefix OldDb.SDE --review-suffix .sde
litswap: 1 file(s) scanned

nightly.py
  REVIEW   line 2, col 1          'OLDSQL'
           outside a string literal, so it is an identifier or a comment and not this tool's to edit
           OLDSQL_FALLBACK = "unused"
  REPLACE  line 3, col 11         'OLDSQL' -> 'newsql.example.org'
           SERVER = "OLDSQL"
  REVIEW   line 4, col 9          'OLDSQL'
           part of a longer token, so rewriting it would corrupt the rest of that token
           FQDN = "OLDSQL.example.org"
  REVIEW   line 5, col 28         'OLDSQL'
           inside a literal ending in .sde, which is a connection file and is not edited as text
           CONN = r"C:\conn\SQLServer-OLDSQL-Gis.sde"
  REVIEW   line 7, col 22         'OldDb.SDE.Parcels'
           inside an embedded SQL statement, where the qualifier is still valid; stripping it here would break the statement rather than fix it
           SELECT OBJECTID FROM OldDb.SDE.Parcels
  REPLACE  line 9, col 17         'OldDb.SDE.Area' -> 'SDE.Area'
           PATH = r"C:\gis\OldDb.SDE.Area\OldDb.Sde.Roads"
  REPLACE  line 9, col 32         'OldDb.Sde.Roads' -> 'Sde.Roads'
           PATH = r"C:\gis\OldDb.SDE.Area\OldDb.Sde.Roads"
  REVIEW   line 10, col 11        'OldDb.SDE.'
           qualifier with a computed object name (suggest 'OldDb.SDE.' -> 'SDE.'); the object is built at run time, so only the qualifier can be fixed, and it must be fixed by hand
           LAYER = f"OldDb.SDE.{name}"

3 replacement(s) pending, 5 for review, 0 file(s) errored
Nothing was written. Re-run with --apply.
Every REVIEW item is a case the rules could not decide. Read each one; none of them was changed.
```

Reading down that report:

- **The comment on line 1 is not in the report at all.** It is the record of why the rename happened.
- **The identifier on line 2 is REVIEW.** The tool does not rename symbols, and says so.
- **Line 3 is the only whole-value replacement.**
- **Line 4 is REVIEW.** `OLDSQL.example.org` is already qualified. A dot and a hyphen count as part of
  the same token, so the tool sees a longer name and stops. `sed` produces
  `newsql.example.org.example.org` here.
- **Line 5 is REVIEW.** A connection file is a binary or an encoded credential store. Editing the
  hostname inside its filename is not the way to repoint it.
- **Line 7 is REVIEW, and this one is deliberate.** A qualifier inside a `SELECT` usually still
  resolves after the object it names is re-cataloged. Stripping it there breaks a query that works.
  `SELECT`, `FROM`, `JOIN`, `OPENQUERY`, `WHERE`, `EXEC`, `INSERT`, `UPDATE`, `DELETE` and `MERGE`
  all trigger the demotion. The same triple-quoted literal with no SQL keyword in it is replaced
  normally, so the rule is about the SQL and not about the quoting.
- **Line 9 gives two replacements, not one.** A backslash-joined double qualifier is two separate
  names. Each keeps the capitalisation the script wrote, so `SDE` stays `SDE` and `Sde` stays `Sde`.
  A dot-joined double qualifier such as `OldDb.SDE.Area.OldDb.Sde.Roads` is also fully reduced,
  because the replacement is re-scanned until no qualifier is left.
- **Line 10 is REVIEW.** The object name is built at run time, so no regular expression can rewrite
  the whole path. The tool still names the stale qualifier and suggests what to put there. Missing
  this silently is the failure this tool exists to prevent.

Before an `--apply`, litswap also runs a pre-flight over the whole tree for signs that the rename
already happened. One converted file on its own is a weak hint, because a legitimate two-part name
looks exactly the same, and litswap warns and carries on. It refuses only on strong evidence: the
same object name still written in full somewhere else in the tree. `--force` overrides either. The
default is a warning on purpose, so nobody gets in the habit of passing `--force` every time.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Nothing is outstanding. Either there was nothing to do, or `--apply` finished it. |
| 1 | Work is outstanding: replacements pending, a review item, or a file that errored. |
| 2 | A flag value was rejected. |
| 64 | Usage error: no paths, no rules, an unusable rule, or a path that does not exist. |

A dry run over a tree that needs work exits 1, so a scheduled check can gate on it.

## How it decides what is a literal

It calls `tokenize` from the standard library, the same tokenizer the interpreter agrees with. That
is not a detail. A hand-written scanner has to get raw prefixes, byte prefixes, nested quotes, a `#`
inside a literal and a quote inside a comment all correct, and it will be wrong about one of them.

An f-string is reported as one span on every version. Python 3.12 split f-strings into three token
types where 3.9 through 3.11 emit one, so without that merge the tool would answer differently on
ArcGIS Pro's Python and on a current `python3`.

A file the tokenizer refuses is an ERROR with no findings, never a partial answer. A partial answer
is worse than none: it names a few hits, implies the rest of the file is clean, and the operator
moves on.

## Writing

- The dry run writes nothing. `--apply` is the only thing that writes, and no environment variable
  turns it on.
- Every file written is backed up first. The plain `<file>.bak` when it is free, otherwise a
  timestamped `<file>.<stamp>.bak`. An existing backup is never overwritten, because a tool that
  clobbers its own backup destroys the previous state on the second run.
- The file is read again immediately before the write and compared against what was scanned. A file
  somebody else edited in between is refused, not overwritten.
- The write is atomic. A temporary file in the same directory, then `os.replace`. A crash or a full
  disk leaves the old file or the new one, never half of either.
- A file nobody can write is one clear error, checked before any backup is made, so no orphan `.bak`
  is left beside it.
- CRLF line endings and a UTF-8 BOM both survive. A file that is not valid UTF-8 is an error and is
  never decoded with replacement characters, because a lossy decode would write the damage back.

## When to run it

Rewriting early and rewriting late both break production, and there is usually no moment when both
spellings work. A script holding the old name resolves against the old system and not the new one;
the new name is the reverse. No single string satisfies both. So the scripts have to change at the
same moment the thing they point at changes, and any scheduled job that touches them should be off
for that window. Run the dry run as early and as often as you like. It only reads.

## Limits

- It knows Python. The tokenizer is Python's, so this reads `.py` and `.pyt` files and nothing else.
  A hostname in a `.sql`, a `.bat`, a `.json` or a `.lyrx` is out of scope.
- `--exclude` is `fnmatch`, not `.gitignore` syntax. `vendor/*` and `*_legacy.py` work. A bare file
  name excludes that file anywhere in the tree. A pattern typed with backslashes matches a path
  spelled with slashes. Negation does not work, and `*` crosses a `/`. It ignores case on every
  platform, because `fnmatch` alone folds case on Windows and not on Linux.
- It renames text, not meaning. A literal that happens to contain the old value for an unrelated
  reason is a hit like any other, and you get a REPLACE you did not want. Read the dry run.
- `--strip-prefix` keeps the capitalisation found in the source and needs a dot to split on.
  `Db.SCHEMA` is a prefix. `SCHEMA` is refused.
- The token rule treats `-` and `.` as part of a name, which suits hostnames and paths. Renaming
  something whose neighbours are hyphens or dots for a different reason produces REVIEW items rather
  than replacements.
- Every file is read from disk once, but the pre-flight then walks all of that text a second time
  before the scan proper. On a few thousand files that is seconds, not minutes, but it is not free.
- There is no undo command. The backups are the undo, and they are ordinary files beside the
  originals. Remove them yourself when the change is confirmed.
- It does not rename identifiers, imports, attributes or anything else a refactoring tool renames.
  That work belongs to `rope` or `libcst`, which do it properly.

## Why not sed, or a refactoring tool

`sed` and an editor's Replace in Files do line-oriented substitution well, and both are far faster
than this. Neither knows what a Python string literal is, so neither can tell a hit in a comment from
a hit in a path, and neither can see that a hit sits inside embedded SQL.

`rope` and `libcst` do parse Python properly, and they rename identifiers correctly. That is a
different job. The thing being renamed here is data inside a literal, not a symbol, and no symbol
table has an entry for it. `libcst` will hand you the parsed tree and let you write the codemod. This
is that codemod, with the decision rules already attached and with the cases it refuses to decide
written down.

## Contributing

Open an issue or pull request on GitHub.

## Author

Built by [Asir Khan](https://www.linkedin.com/in/asir-khan-310317264/).

## License

MIT.

## Related

Other single-file tools in this portfolio that pair with this one:

- [gdbfence](https://github.com/uhsear/gdbfence) - refuses the commit that puts a geodatabase or a
  hard-coded drive letter into git, which is where these literals come from
- [agol-relink](https://github.com/uhsear/agol-relink) - the same rename on the other surface: the
  service URLs held inside ArcGIS Online and Portal items, which no file scan reaches
