"""Run pass2pass / fail2pass buckets inside the built task image and grade them.

A bucket is a directory of tests, bind-mounted read-only into the image and run with
pytest; per-test outcomes are read from JUnit XML so an assertion failure is told apart
from a collection/import error. Shared by `validate` (baseline guardrail) and, later,
`run` (post-solver scoring). Python/pytest for now; the parse + judge layer is reusable.
"""
from __future__ import annotations

# ----------------------------- Imports -----------------------------
import secrets
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from . import containers as C

# ----------------------------- Constants -----------------------------
PASSED, FAILED, ERROR, SKIPPED = "passed", "failed", "error", "skipped"

_BUCKET_MOUNT = "/taskbundle/bucket"      # tests mounted read-only here
_PATCH_MOUNT = "/taskbundle/patch.diff"
_XML_PATH = "/tmp/taskbundle-junit.xml"
_REPO_WORKDIR = "/workspace/repo"
_XML_DELIM = f"===TASKBUNDLE-JUNIT-XML-{secrets.token_hex(6)}==="  # random per run so test output can't forge it


# ----------------------------- Results -----------------------------
@dataclass
class TestResult:
    name: str
    status: str   # passed | failed | error | skipped


@dataclass
class BucketResult:
    name: str                 # "pass2pass" | "fail2pass"
    results: List[TestResult]
    exit_code: int
    log: str = ""             # human-readable runner output (debugging / ledger)
    expected: bool = False    # were tests supposed to run? (the bucket had test files)

    def _names(self, status: str) -> List[str]:
        return [r.name for r in self.results if r.status == status]

    @property
    def passed(self) -> List[str]:  return self._names(PASSED)
    @property
    def failed(self) -> List[str]:  return self._names(FAILED)
    @property
    def errored(self) -> List[str]: return self._names(ERROR)
    @property
    def skipped(self) -> List[str]: return self._names(SKIPPED)

    @property
    def produced(self) -> bool:        # the runner actually reported tests
        return bool(self.results)

    @property
    def unproduced(self) -> bool:      # expected to run, but reported nothing
        return self.expected and not self.produced

    @property
    def any_passed(self) -> bool:
        return any(r.status == PASSED for r in self.results)

    @property
    def any_failed(self) -> bool:      # a genuine failure or error (not a skip or an absence)
        return any(r.status in (FAILED, ERROR) for r in self.results)

    @property
    def clean(self) -> bool:
        # no failures/errors — but tests that were EXPECTED must have actually run:
        # an empty result set when the bucket has tests can't confirm "no regression".
        if self.unproduced:
            return False
        return all(r.status in (PASSED, SKIPPED) for r in self.results)

    @property
    def all_passed(self) -> bool:
        return self.produced and all(r.status == PASSED for r in self.results)

    def counts(self) -> dict:
        return {"passed": self.passed, "failed": self.failed,
                "error": self.errored, "skipped": self.skipped}


# ----------------------------- JUnit parsing -----------------------------
def parse_junit(xml_text: str) -> List[TestResult]:
    """Map a JUnit XML report to per-test results. Empty / malformed → [].

    Per testcase the WORST child status wins (error > failure > skipped > passed), so a
    <failure> is never masked by a trailing <skipped>; tags match by LOCAL name, so
    namespaced JUnit (<ns:testcase>) is found too.
    """
    text = xml_text.strip()
    if not text:
        return []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    out: List[TestResult] = []
    for el in root.iter():
        if el.tag.split("}")[-1] != "testcase":   # local name → tolerate namespaced tags
            continue
        tags = {child.tag.split("}")[-1] for child in el}
        # Yatharth Note : worst status wins here - a trailing <skipped> was quietly masking a real <failure> for me until I ordered it error > failure > skipped > passed.
        if "error" in tags:     status = ERROR
        elif "failure" in tags: status = FAILED
        elif "skipped" in tags: status = SKIPPED
        else:                   status = PASSED
        out.append(TestResult(el.get("name") or "?", status))
    return out


# ----------------------------- Run a bucket -----------------------------
def run_bucket(
    runtime: str,
    tag: str,
    bucket_dir: str | Path,
    *,
    name: str,
    patch: Optional[str | Path] = None,
    network: Optional[str] = None,
    timeout: Optional[int] = None,
    test_cmd: Optional[str] = None,
    memory: Optional[str] = None,
    cpus: Optional[str] = None,
) -> BucketResult:
    """Run one bucket in the image; optionally `git apply` patch.diff first.

    The bucket is mounted read-only — the container never mutates the author's files. The runner
    defaults to pytest, but a bundle's `test_cmd` overrides it to use ANY framework: it runs with
    `$TASKBUNDLE_BUCKET` (the mounted tests) and `$TASKBUNDLE_JUNIT` (where to write JUnit XML)
    exported, and results are read from that XML — so anything that emits JUnit works (e.g. `go test`
    via gotestsum, jest via jest-junit). The XML is cat'd back after a delimiter so we recover both
    the machine-readable results and the human log from one stream.
    """
    bucket_dir = Path(bucket_dir).resolve()
    expected = any(p.is_file() for p in bucket_dir.rglob("*"))   # a non-empty bucket is expected to run (language-agnostic)
    volumes = [(str(bucket_dir), _BUCKET_MOUNT, "ro")]
    pre = ""
    if patch is not None:
        patch = Path(patch).resolve()
        volumes.append((str(patch), _PATCH_MOUNT, "ro"))
        pre = f"git apply -p1 {_PATCH_MOUNT} && "
    # default runner is pytest; a bundle's test_cmd (any JUnit-emitting framework) overrides it
    runner = test_cmd or f"python -m pytest {_BUCKET_MOUNT} -p no:cacheprovider --junit-xml={_XML_PATH} -q"
    command = (
        f"export TASKBUNDLE_BUCKET={_BUCKET_MOUNT}; export TASKBUNDLE_JUNIT={_XML_PATH}; "
        f"{pre}{runner}; rc=$?; "
        f"echo '{_XML_DELIM}'; cat {_XML_PATH} 2>/dev/null; exit $rc"
    )
    res = C.run_in_image(
        runtime, tag, command,
        network=network, workdir=_REPO_WORKDIR, volumes=volumes, timeout=timeout,
        memory=memory, cpus=cpus,
    )
    human, _, xml = res.output.partition(_XML_DELIM)
    return BucketResult(name, parse_junit(xml), res.exit_code, human.strip(), expected=expected)


# ----------------------------- Guardrail judgment -----------------------------
def judge_baseline(p2p: BucketResult, f2p: BucketResult) -> bool:
    """Baseline invariant: no pass2pass regressions, and fail2pass GENUINELY fails — at least one
    fail/error and nothing passing. An all-skipped or empty fail2pass does NOT count (a skip is
    "did not pass", not "failed"), so an inert task can't certify as valid."""
    return p2p.clean and f2p.any_failed and not f2p.any_passed


def judge_patched(p2p: BucketResult, f2p: BucketResult) -> bool:
    """After the golden patch: pass2pass still clean, and every fail2pass now passes."""
    return p2p.clean and f2p.all_passed
