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
...
PASS  an f-string is ONE span on every Python version  <-- pinned defect
...
PASS  and that span STARTS at the outer f-string, not at the inner one  <-- pinned defect
...
PASS  a Python 2 print statement does not stop the scan, because the front end tokenizes and never parses  <-- pinned defect
...
PASS  a drive-letter and a UNC literal are decided with no file-system call, so the scan never calls a path DEAD  <-- pinned defect
...
PASS  scan_source raises rather than scanning a partial mask  <-- pinned defect
...
PASS  the casing the script wrote is kept, not the casing on the command line  <-- pinned defect
PASS  a backslash-separated double qualifier gives TWO replacements  <-- pinned defect
PASS  a dot-joined double qualifier is fully reduced, not left half done  <-- pinned defect
PASS  a qualified name in a comment is neither replaced nor reviewed  <-- pinned defect
...
PASS  a qualifier inside embedded SQL is reviewed, never stripped  <-- pinned defect
...
PASS  UPDATED_ROWS is not the UPDATE keyword, so the SQL demotion needs a whole word and not a substring  <-- pinned defect
...
PASS  an already-qualified host is reviewed, never doubled into newhost.example.org.example.org  <-- pinned defect
...
PASS  and the connection-file reason wins over the token reason, because it is the more useful one  <-- pinned defect
...
PASS  the comment beside a rewritten literal survives the edit  <-- pinned defect
PASS  two hits on one line both land, because the line is edited right to left  <-- pinned defect
...
PASS  the identifier is untouched while the literal beside it is rewritten  <-- pinned defect
...
PASS  an old-Mac CR-only file is refused, not written wrong  <-- pinned defect
PASS  a hit in column 1 of line 2 is reported on line 2, not as the end of line 1  <-- pinned defect
PASS  doubled braces and a named escape are data, and a nested field, a raw-literal field and a field after an escaped backslash are code  <-- pinned defect
...
PASS  the name inside the braces is left to run, and the text beside it is renamed  <-- pinned defect
...
PASS  a new value holding a backslash, either quote, a line break or a brace lands as exactly that value in every kind of literal  <-- pinned defect
PASS  and where a literal cannot hold it (a quote or line break in a raw literal, a trailing backslash in a raw literal, a non-ASCII character in a bytes literal) the hit is reviewed, not written  <-- pinned defect
PASS  a PWD value on the line of a hit is masked in the report  <-- pinned defect
...
PASS  the pre-flight echo is masked the same way  <-- pinned defect
...
PASS  a form feed, a vertical tab or U+2028 near a hit no longer makes the file unappliable  <-- pinned defect
PASS  a CR-only file is an ERROR in the scan, so a dry run shows the refusal and not a pile of drifted hits  <-- pinned defect
...
PASS  a file that still holds the old value beside the new one is not evidence that the rename ran
...
PASS  a bare file name excludes that file anywhere in the walk, not only at the top of it  <-- pinned defect
PASS  a pattern typed with backslashes matches a path spelled with slashes  <-- pinned defect
PASS  a path typed with its directory is excluded by a pattern for the tail of it  <-- pinned defect
...
PASS  --apply is OFF unless asked for  <-- pinned defect
...
PASS  a unique prefix of the write flag (--ap for --apply) is refused by the parser, not taken as --apply  <-- pinned defect
...
PASS  a file that is not UTF-8 is an error with no findings  <-- pinned defect
PASS  a file the tokenizer refuses is an error with no findings  <-- pinned defect
...
PASS  and writes nothing at all  <-- pinned defect
...
PASS  CRLF line endings survive the rewrite  <-- pinned defect
PASS  a UTF-8 BOM survives the rewrite  <-- pinned defect
...
PASS  an existing backup is never clobbered  <-- pinned defect
...
PASS  a third backup inside the same second gets a counter rather than overwriting the second  <-- pinned defect
...
PASS  a backup carries exactly the mode of the file it protects, so a script kept private does not get a more readable copy  <-- pinned defect
...
PASS  and the file it was for still holds the original, because the backup is written before it  <-- pinned defect
...
PASS  and the backup it had already made is removed, so no orphan .bak is left behind  <-- pinned defect
...
PASS  an unwritable file is one clear error  <-- pinned defect
...
PASS  a finding that no longer matches the text aborts the write  <-- pinned defect
PASS  a file edited between the scan and the write is refused  <-- pinned defect
...
PASS  a byte-order mark added after the scan counts as an edit, so the write is refused  <-- pinned defect
...
PASS  and takes its temporary file with it  <-- pinned defect
...
PASS  strong pre-flight evidence stops an apply  <-- pinned defect
...
PASS  a weak hint warns and carries on, so nobody reaches for --force by habit  <-- pinned defect
PASS  and with --apply the weak hint still lets the write through  <-- pinned defect
...
PASS  a directory holding only a .txt scans nothing, and that is a usage error and not a clean pass  <-- pinned defect
...
PASS  the report on a drive-letter literal gives a rename decision and no path verdict at all  <-- pinned defect
PASS  a file that could not be written is not counted as written  <-- pinned defect
PASS  --apply over a file that holds only review items writes no backup and no file  <-- pinned defect
PASS  a character the console cannot encode prints as an escape and does not end the report after the files were written  <-- pinned defect
PASS  a named file with an unscanned extension exits 64 and says how to scan it  <-- pinned defect
PASS  a directory the walk could not read is an ERROR line and exit 1, not a silent skip  <-- pinned defect
...
PASS  the same file named by two spellings is scanned once  <-- pinned defect
PASS  an --exclude written for the tail of a path stops a file named straight on the command line  <-- pinned defect
...
PASS  a path that does not exist is a usage error, not a clean pass  <-- pinned defect
...
PASS  a directory with no Python in it exits 64 and says so, so a gate pointed at the wrong place does not stay green  <-- pinned defect
...
PASS  and reports an exception that is not the one it asked for  <-- pinned defect
PASS  the self-test leaves no temporary directory behind  <-- pinned defect
PASS  a green run ends with the count and exit code 0
PASS  and a red run names each failed label and exits 1, so the red path is exercised and not only described  <-- pinned defect
PASS  importing the module runs nothing and prints nothing  <-- pinned defect
--------------------------------------------------------------------
218 assertions, 0 failed
```

Lines marked `...` are left out of this quote. The real run prints every assertion, and a green run
prints no `FAIL` line at all. The harness checks itself by running `check()` and `raises()` on
failures built on purpose, with their output captured, so a CI step that greps for `FAIL` sees
nothing on a green run.

## Requirements

Python 3.9 or newer. Nothing to install, no `arcpy`, no third-party package. It runs on ArcGIS Pro's
Python and on a plain `python3` equally. The same 218 assertions pass on Windows and on Linux.

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
| `--rename OLD=NEW` | none | Replace OLD with NEW inside string literals. Repeatable. NEW must not be empty. |
| `--strip-prefix QUALIFIER.KEPT` | none | Drop the leading qualifier, so `QUALIFIER.KEPT.OBJECT` becomes `KEPT.OBJECT`. Repeatable. |
| `--review-suffix SUFFIX` | none | Never rewrite inside a literal ending in SUFFIX. Report it instead. Repeatable. |
| `--ext EXT` | `.py`, `.pyt` | Extension to scan, with or without its leading dot. Passing any replaces both defaults. Repeatable. |
| `--exclude GLOB` | none | Skip paths matching this glob. Repeatable. |
| `--force` | off | Apply although the pre-flight found strong evidence the rename already ran. |
| `--apply` | off | Write the replacements. Without it nothing is written. |
| `--self-test` | off | Run the assertions and exit. Needs no data. |

You must pass at least one `--rename` or `--strip-prefix`. The tool ships with no rule of its own, no
hostname and no database name. The parser does not accept abbreviated flags: `--ap` is an error, not
`--apply`, and the self-test pins that.

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

An f-string gets one more rule. Text inside `{...}` is code, so a hit there is REVIEW and never
replaced: in `f"SERVER={OLDSQL};x=OLDSQL"` the name in braces keeps running and only the text beside
it is renamed. A doubled brace (`{{`) and a `\N{NAME}` escape are data, and a hit beside them is
replaced as usual.

The new value is written into the file as Python source text, so litswap escapes it for the literal
it lands in. A backslash is doubled, the literal's own quote character and a line break are escaped,
and a brace in an f-string is doubled. `--rename "OLDSQL=HOST\NAMED"` therefore leaves the value
`HOST\NAMED` in the script, and the file still compiles. A raw literal cannot hold a quote, a line
break or a trailing backslash, and a bytes literal cannot hold a non-ASCII character. For those the
hit is REVIEW and nothing is written. In the report, the text after `->` is the source text that
goes into the file, so a doubled backslash shows doubled.

Before an `--apply`, litswap also runs a pre-flight over the whole tree for signs that the rename
already happened. One converted file on its own is a weak hint, because a legitimate two-part name
looks exactly the same, and litswap warns and carries on. It refuses only on strong evidence. For
`--strip-prefix`, that is the same object name still written in full somewhere else in the tree. For
`--rename`, that is a file holding the new value when the old value appears nowhere in the tree.
`--force` overrides either. The default is a warning on purpose, so nobody gets in the habit of
passing `--force` every time.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Nothing is outstanding. Either there was nothing to do, or `--apply` finished it. |
| 1 | Work is outstanding: replacements pending, a review item, a file that errored, or a directory that could not be read. |
| 2 | A flag value was rejected, or an abbreviated flag was used. |
| 64 | Usage error: no paths, no rules, an unusable rule, a path that does not exist, a named file whose extension is not scanned, or no files found to scan. |

A dry run over a tree that needs work exits 1, so a scheduled check can gate on it. A run that
scanned nothing exits 64, so a gate pointed at the wrong place, an unmounted share or a mistyped
`--ext` does not pass in silence. To scan a file such as `run.pyw`, add `--ext pyw`.

## How it decides what is a literal

It calls `tokenize` from the standard library, the same tokenizer the interpreter agrees with. That
is not a detail. A hand-written scanner has to get raw prefixes, byte prefixes, nested quotes, a `#`
inside a literal and a quote inside a comment all correct, and it will be wrong about one of them.

An f-string is reported as one span. Python 3.12 split f-strings into three token types where 3.9
through 3.11 emit one, so without that merge the tool would answer differently on ArcGIS Pro's
Python and on a current `python3`. The self-test ran at the same assertion count on 3.9, 3.10, 3.11,
3.12 and 3.13.

A file the tokenizer refuses is an ERROR with no findings, never a partial answer. A partial answer
is worse than none: it names a few hits, implies the rest of the file is clean, and the operator
moves on. A file with a bare CR line ending (old Mac) is refused the same way, because its line
numbers would drift.

Tokenizing rather than parsing is also why this works on an old tree. A Python 2 script is scanned
like any other, because the tokenizer never asks whether the file would run. The boundary is the
damage and the encoding: an unbalanced bracket, a bad dedent, an unterminated triple quote or a file
that is not UTF-8 is an ERROR. The Python version matters here too. Python 3.9 through 3.11 accept
an unterminated single-quote string, mixed tabs and spaces, and a NUL byte, and scan the file. Python
3.12 and later refuse all three. The same tree can therefore give different scanned and refused
counts on different interpreters, so use the same one every time.

litswap makes a text decision about a literal. It does not decide whether a path is stale, and it
never asks whether a path literal exists. (It does check that the paths you pass on the command line
exist, and exits 64 for one that does not.) A path in a script that runs elsewhere can only be
judged on that machine, which is the next section.

### A path that does not resolve here is UNVERIFIABLE, not DEAD

When you check path literals yourself before a rename, a path that does not resolve on your
workstation is UNVERIFIABLE, not DEAD. In the author's own scripts, 351 of 352 drive-letter and UNC
literals did not resolve from the workstation that ran the check.

Microsoft documents that drive letters are not global to the system: each logon session gets its own
set from A to Z, and a service cannot use a drive letter made in another logon session
([Services and Redirected Drives](https://learn.microsoft.com/en-us/windows/win32/services/services-and-redirected-drives)).
That page covers logon sessions on one machine. The step to "another workstation" is reasoning, not
something the page says. A mapped letter such as `Q:` or a local disk letter such as `D:` exists on
the machine, and for the account, that runs the job. Your desk may have neither. So
`Q:\gis\parcels.gdb` can be missing at your desk and present for the job. Only a check made on that
machine, as that account, can call the path DEAD. The same page tells a service to use the UNC name
instead of a mapped drive letter.

litswap gives no path a liveness verdict, so it cannot make this mistake. The self-test pins that.
It wraps nine file-system lookups: `os.stat`, `os.lstat`, `os.listdir`, `os.scandir`, `os.access`,
and `exists`, `lexists`, `isdir` and `isfile` from `os.path`. It proves the wrapper records a real
lookup. Then it scans a drive-letter literal and a UNC literal: the scan makes none of those nine
lookups and replaces both. A full run over a drive-letter literal prints a rename decision, and its
report holds none of the words DEAD, MISSING, UNVERIFIABLE or NOT FOUND. The wrapper does not cover
`os.path.realpath`, `os.path.samefile`, `pathlib`, `os.walk` or `open()`, so the claim covers the
nine named functions and no others.

## Writing

- The dry run writes nothing. `--apply` is the only thing that writes, and no environment variable
  turns it on.
- Every file written is backed up first. The plain `<file>.bak` when it is free, otherwise a
  timestamped `<file>.<stamp>.bak`. An existing backup is never overwritten, because a tool that
  clobbers its own backup destroys the previous state on the second run.
- The backup gets the permissions of the file it protects, and it is created owner-only before any
  text goes in. A script that is mode 0600 does not get a world-readable copy.
- The file is read again immediately before the write and compared against what was scanned. A file
  somebody else edited in between is refused, not overwritten.
- The write is atomic. A temporary file in the same directory, then `os.replace`. A crash or a full
  disk leaves the old file or the new one, never half of either.
- A file nobody can write is one clear error, checked before any backup is made, so no orphan `.bak`
  is left beside it. A backup that fails part-way is removed.
- A file with only REVIEW items is neither backed up nor rewritten.
- The summary counts what reached the disk. If a file errored, the report says how many replacements
  were NOT written and points at the ERROR lines.
- CRLF line endings and a UTF-8 BOM both survive. A file that is not valid UTF-8 is an error and is
  never decoded with replacement characters, because a lossy decode would write the damage back.
- Output never ends a run early. A character the console cannot encode, such as an arrow in a
  comment on a hit line under a Windows code page, prints as a backslash escape, so the report is
  complete after the files were written.

## When to run it

Rewriting early and rewriting late both break production, and there is usually no moment when both
spellings work. A script holding the old name resolves against the old system and not the new one;
the new name is the reverse. No single string satisfies both. So the scripts have to change at the
same moment the thing they point at changes, and any scheduled job that touches them should be off
for that window. Run the dry run as early and as often as you like. It only reads.

## Limits

- It knows Python. The tokenizer is Python's, so this reads `.py` and `.pyt` files and nothing else.
  A hostname in a `.sql`, a `.bat`, a `.json` or a `.lyrx` is out of scope.
- `--exclude` is `fnmatch`, not `.gitignore` syntax. `vendor/*` and `*_legacy.py` work. litswap tries
  a pattern against the path as you typed it and against every tail of it, so `vendor/*` excludes
  `scripts/vendor/v.py` whether a directory walk or the command line reached it. A pattern that starts
  at a drive or a root is tried against the absolute path. A pattern typed with backslashes matches a
  path spelled with slashes. Negation does not work, and `*` crosses a `/`. It ignores case on every
  platform, because `fnmatch` alone folds case on Windows and not on Linux.
- It renames text, not meaning. A literal that happens to contain the old value for an unrelated
  reason is a hit like any other, and you get a REPLACE you did not want. Read the dry run.
- A docstring is a string literal, so a hit inside one is replaced like any other literal. Only a
  comment is left alone.
- A rename matches the old value in any letter case and writes the new value exactly as typed.
- Any literal that contains a SQL keyword such as `FROM` or `UPDATE` as a whole word is demoted to
  REVIEW for `--strip-prefix`. That includes ordinary prose.
- `--strip-prefix` keeps the capitalisation found in the source and needs a dot to split on.
  `Db.SCHEMA` is a prefix. `SCHEMA` is refused.
- The token rule treats `-` and `.` as part of a name, which suits hostnames and paths. Renaming
  something whose neighbours are hyphens or dots for a different reason produces REVIEW items rather
  than replacements.
- The f-string field rule counts braces and does not read the quotes inside a field. A field that
  holds a quoted brace is cut short, which can make a later hit in that literal look like data.
- The report prints the source line of every hit, so a password or key on that line shows in the
  output. litswap masks the value after `PWD`, `PASSWORD`, `PASSWD`, `PASS`, `SECRET`, `TOKEN`,
  `APIKEY`, `API_KEY` or `KEY` when it follows `=` or `:`, and the password in a `user:password@host`
  URL. Any other shape is not masked. Review a report, or redirect it to a file only you can read,
  before you share it. The mask is for the echo only: the file on disk keeps its text.
- A backup holds the same text as the script, so the `.bak` of a script that holds a password is
  itself a secret file. Delete the backups once the change is confirmed. This repository's
  `.gitignore` ignores `*.bak`; a typical one does not, so check yours before you commit.
- There is no undo command. The backups are the undo, and they are ordinary files beside the
  originals.
- Exit code 1 means "work outstanding", and it covers both a tree with findings and a run where a
  file could not be read or tokenized. A gate cannot tell a broken run from a run with work. Read the
  ERROR lines to tell them apart.
- Two rules that hit the same span, such as the same `--rename` twice, or a `--rename` and a
  `--strip-prefix` on one name, make that file fail with "apply refused". Nothing is written for it.
  Keep the rules from overlapping.
- A symbolic link given as a file is replaced by a regular file holding the new text, and the target
  of the link keeps the old text. The backup is made beside the link. Pass the real path.
- The pre-flight searches the whole text of each file for the new value, comments included. A short
  new value such as `gis` produces many weak hints. This is noise and not a refusal.
- Every file is read from disk once for the scan. Under `--apply` each file written is read a second
  time, to compare it with what was scanned. The pre-flight also walks all of the text once more
  before the scan proper. On a few thousand files that is seconds, not minutes, but it is not free.
- It does not rename identifiers, imports, attributes or anything else a refactoring tool renames.
  That work belongs to `rope` or `libcst`, which do it properly.
- Branch coverage is 100% (`coverage run --branch litswap.py --self-test`, coverage.py 7.16, on
  Windows). Two shortcuts keep a regression from being reported by name. A UnicodeEncodeError or a
  ScanError raised inside the self-test ends the run with a traceback instead of a FAIL line, because
  those two checks have no `try` block of their own. The self-test also loads the module a second
  time with `runpy` to prove an import runs nothing.

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
