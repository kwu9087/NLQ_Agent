"""Run the accuracy test set against the live model and report the results.

    python -m evals.run                  # every case
    python -m evals.run brief_top_categories weekday   # selected cases

Needs ANTHROPIC_API_KEY. Each case makes one or two model calls (about 20 cases, a
few dollars at most on Opus 5.5). Writes a full report, with every answer and SQL query,
to evals/last_run.md, and exits with status 1 if any case fails.
"""

import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .cases import CASES, TODAY, Case, asserts, says_zero, self_corrects

os.environ.setdefault("NLQ_TODAY", TODAY)     # before the agent reads its config

from nlq_agent import config                  # noqa: E402
from nlq_agent.agent import Agent, AgentResult  # noqa: E402

REPORT_PATH = Path(__file__).resolve().parent / "last_run.md"
TOLERANCE = 0.01


@dataclass
class Outcome:
    case: Case
    result: AgentResult
    seconds: float
    problems: list[str]

    @property
    def passed(self) -> bool:
        return not self.problems


def _same(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= TOLERANCE
    return a == b


def compare(reference: list[tuple], columns: list[str], rows: list[tuple],
            ordered: bool) -> list[str]:
    """Problems found when matching a reference result against the agent's rows.

    Every reference column must equal some agent column. With ordered=True the
    reference rows must be the agent's first rows; otherwise the same rows in any order.
    """
    if not reference:
        return ["reference query returned no rows"]
    if ordered:
        if len(rows) < len(reference):
            return [f"expected at least {len(reference)} rows, got {len(rows)}"]
        rows = rows[:len(reference)]
    elif len(rows) != len(reference):
        return [f"expected {len(reference)} rows, got {len(rows)}"]

    def column(data, i):
        values = [r[i] for r in data]
        return values if ordered else sorted(values, key=lambda v: (v is None, str(type(v)), v))

    problems = []
    for i in range(len(reference[0])):
        want = column(reference, i)
        if not any(all(_same(w, g) for w, g in zip(want, column(rows, j)))
                   for j in range(len(columns))):
            problems.append(f"no column matches expected values {want[:5]}")
    return problems


def check(case: Case, result: AgentResult, raw: sqlite3.Connection) -> list[str]:
    problems = []
    if result.status not in case.expect:
        detail = result.error_detail or result.error or result.reason or result.clarifying_question
        problems.append(f"status {result.status!r}, expected {sorted(case.expect)}"
                        + (f" ({detail})" if detail else ""))
    if case.reference_sql and result.status == "answered":
        reference = raw.execute(case.reference_sql).fetchall()
        problems += compare(reference, result.columns, result.rows, case.ordered)
    if case.forbid_columns:
        bad = [c for c in result.columns if re.search(case.forbid_columns, c, re.I)]
        if bad:
            problems.append(f"forbidden columns returned: {bad}")
    if self_corrects(result.answer):
        problems.append("answer corrects itself mid-text")
    if case.no_zero_answer and says_zero(result.answer):
        problems.append("answer presents missing data as zero")
    if case.expect_truncated and result.status == "answered" and not result.truncated:
        problems.append(f"result not marked as cut off ({len(result.rows)} rows returned)")
    if case.forbid_answer and asserts(case.forbid_answer, result.answer):
        problems.append(f"answer matches forbidden wording: {case.forbid_answer}")
    if case.require_assumption and not any(
            re.search(case.require_assumption, a, re.I) for a in result.assumptions):
        problems.append(f"no assumption matches: {case.require_assumption}")
    return problems


def run(cases: list[Case]) -> list[Outcome]:
    agent = Agent.create()
    raw = sqlite3.connect(f"file:{config.DB_PATH.as_posix()}?mode=ro", uri=True)
    outcomes = []
    for n, case in enumerate(cases, 1):
        start = time.monotonic()
        result = agent.ask(case.question)
        outcome = Outcome(case, result, time.monotonic() - start, check(case, result, raw))
        outcomes.append(outcome)
        mark = "PASS" if outcome.passed else "FAIL"
        print(f"[{n:2}/{len(cases)}] {mark}  {case.id:<26} {result.status:<14} "
              f"{outcome.seconds:5.1f}s")
        for p in outcome.problems:
            print(f"         - {p}")
    return outcomes


def _block(text: str) -> str:
    return "\n".join("> " + line for line in text.splitlines()) or ">"


def write_report(outcomes: list[Outcome]) -> None:
    passed = sum(o.passed for o in outcomes)
    lines = [
        "# Accuracy test results",
        "",
        f"Model `{config.MODEL}`, SQL effort `{config.SQL_EFFORT}`, answer effort "
        f"`{config.ANSWER_EFFORT}`. Today pinned to {TODAY}. "
        f"**{passed} of {len(outcomes)} passed.** Generated by `python -m evals.run`.",
        "",
        "| Case | Status | Result | Time |",
        "|---|---|---|---:|",
    ]
    for o in outcomes:
        lines.append(f"| `{o.case.id}` | {o.result.status} | "
                     f"{'pass' if o.passed else 'FAIL'} | {o.seconds:.1f} s |")
    for o in outcomes:
        r = o.result
        lines += ["", f"## `{o.case.id}`: {'pass' if o.passed else 'FAIL'}", "",
                  f"**Q:** {o.case.question}", ""]
        if o.case.note:
            lines += [f"*Tests:* {o.case.note}", ""]
        for label, text in [("Answer", r.answer), ("Clarifying question", r.clarifying_question),
                            ("Can't answer", r.reason), ("Error", r.error)]:
            if text:
                lines += [f"**{label}:**", "", _block(text), ""]
        if r.assumptions:
            lines += ["**Assumptions:**", ""] + [f"- {a}" for a in r.assumptions] + [""]
        if r.sql:
            lines += ["```sql", r.sql.strip(), "```", ""]
        if o.problems:
            lines += ["**Problems:**", ""] + [f"- {p}" for p in o.problems] + [""]
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    wanted = set(argv)
    unknown = wanted - {c.id for c in CASES}
    if unknown:
        print(f"Unknown case id(s): {', '.join(sorted(unknown))}")
        return 2
    cases = [c for c in CASES if not wanted or c.id in wanted]
    outcomes = run(cases)
    write_report(outcomes)
    passed = sum(o.passed for o in outcomes)
    print(f"\n{passed} of {len(outcomes)} passed. Full report: {REPORT_PATH}")
    return 0 if passed == len(outcomes) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
