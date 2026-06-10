
## The decisions (and what each one cost me)

### Keeping "which code" and "the environment" separate
task.json only carries which code (repo, commit, id) plus optional command overrides. The Dockerfile
owns the environment - the FROM, the system deps, where the install runs - and I never split or mix the two. I did
this on purpose, because a half-specified base image is exactly the thing that makes a benchmark work on
my machine and nowhere else. The price of being strict is the remote-repo case: auto-detect can only
read the bundle's local files, so a repo that hasn't been cloned yet always reads as "generic" and just
gets a commented starter Dockerfile to finish. I'd rather show that gap honestly than make a confident
wrong guess about the stack.

### The solver never sees the answers
run is two separate containers, and the asymmetry is deliberate. The solve box has network on (real LLM
agents need it) and sees the repo + description.md, but never the hidden buckets. The score box is
hermetic (--network none), and only there do the buckets come in, applied on top of a clean baseline.
Resolved means every fail2pass passes and nothing in pass2pass breaks. This "solver can't see the tests"
property is the single most important thing in the tool, so I didn't want to just trust myself to keep
it true - there's a runtime-free test that checks exactly what gets mounted into the solve box and goes
red if anyone ever mounts a bucket, or even the bundle root, into it. The cost is that scoring can't
rely on anything the solver installed, but that's actually the whole point.

### Capturing the patch with git diff
I capture whatever the solver changed with git diff instead of asking it to hand me a diff. That way any
file-editing command works the same way - a one-line sed, a script, or a full LLM agent are all the same
interface to me. A few things bit me here and each one needed handling: new files (so I git add -A
first), the solver making its own commits (so I diff against the base commit's SHA, not HEAD), and build
artifacts sneaking into the patch (so I take the base snapshot after the build, not before), and binary
edits (so I diff with --binary, or git just emits a "Binary files differ" stub that won't apply).

### One dependency, and reading JUnit

The philosophy I follow here is: 
a - less complexity
b - more reliability
c - more robustness

I believe that by keeping things simple at the start, it allows me to make more changes and feature additions in the future. As part of this I have tried to keep the code/dependency base really easy to understand and follow.

So I was initially working with argparse for the CLI, but later shifted to Typer (honestly Typer earned the place, and I was already familiar with it from my work with the moss CLI). Typer is the one real dependency I take on; I also pin click, but only because I import it directly in main()'s exit-code shim - and Typer already requires it, so it isn't an extra install.

The library stays on the standard library (sqlite3, subprocess, urllib, xml) so the whole surface is
small and easy to audit. I did try rich for the output and ended up cutting it - it grew the CLI by about 50 lines just for nicer borders which wasn't worth it right now, maybe in the future when the CLI grows more complex. For grading I read JUnit XML so a real failure is told apart from
an import error. The bucket runner isn't locked to pytest either: a bundle's `test_cmd` can be any
framework that writes JUnit to `$TASKBUNDLE_JUNIT` (the tests sit at `$TASKBUNDLE_BUCKET`), so a Go or
Node task runs the same way a Python one does. The ledger stores each record's details as a JSON blob, so I never migrate that part of the schema.

## Bugs I hit (each one became a fix, most also a test)

- An empty pass2pass was letting a do-nothing solver score RESOLVED. Now run re-checks the baseline
  itself, and the evaluator refuses a "clean" verdict when a bucket that should have tests reports nothing.
- An all-skipped fail2pass was certifying the baseline as valid. A skip is "did not pass", not "failed",
  so the baseline now needs a real failure with nothing passing.
- Cloning a local repo left a nested .git, which made git add record it as a gitlink (mode 160000) and
  silently dropped all the repo files. Learnt that one the hard way - now I drop .git after pinning the commit.
- A trailing <skipped> in the JUnit was hiding a real <failure>, so I made the worst status win.
- A solver that crashed looked exactly like one that did nothing. Now a non-zero exit with no patch is
  solver_error, kept separate from no_edits.
- Usage errors were crashing with a traceback, because Typer ships its own vendored click and my except
  was watching the wrong one. Now I catch both.
- Exit code 5 was meaning two different things (no smoke_cmd vs the smoke check actually failing), so I
  split it into 11 and 5.
- My unit tests never actually touched Docker, so I added one Docker-gated end-to-end test that runs the
  real init -> validate -> run loop.
- Two runs at the same time tripped "database is locked", so the ledger uses WAL + a busy timeout now.

## What I know is still rough

- A fork-bomb pids-limit is always on, and run takes --memory / --cpus to cap the solve + score boxes,
  but I left those opt-in (a default memory cap that OOM-kills a heavy test suite would mislabel it
  unresolved, which is worse than no cap). The solver still runs as root - --user / --cap-drop is the next
  bit of containment, and --user is a real change not a flag, since the image has to own /workspace/repo
  as a non-root user first.
- The patch capture (`git add -A` then diff) respects the repo's .gitignore/.gitattributes on purpose -
  I want to capture the solver's source edits, not drag build artifacts or node_modules into the patch. The
  flip side is a solver that has to create a file the repo ignores (e.g. into build/) won't have it captured.
  For SWE-bench-style fixes (which edit tracked source) that's the right default; forcing it (`add -f`) would
  do more harm than good.
- The image tag is keyed on id + commit, not on the environment, so after editing a Dockerfile you have to
  re-run `init` (which always rebuilds) or pass `--rebuild` - otherwise `validate`/`run` reuse the cached
  image. Folding a hash of the Dockerfile into the tag would make that automatic.
- I also want to work on CLI sessions, where I'd create a session per bundle - after entering it, you could run validate and run without passing the bundle path each time.
