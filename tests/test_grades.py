"""Weighted-grade math, checked against values worked out by hand."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from blackboard_web.grades import (  # noqa: E402
    build_breakdown, current_grade, letter_for, needed_on, projected_grade, summarise,
)

FAILS: list[str] = []


def check(label: str, got, want, tol: float = 0.05) -> None:
    if isinstance(want, float) and isinstance(got, (int, float)):
        ok = got is not None and abs(got - want) <= tol
    else:
        ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok else f"  — got {got!r}, want {want!r}"))
    if not ok:
        FAILS.append(label)


CATS = [{"id": "q", "title": "Quiz"}, {"id": "e", "title": "Exam"}]
COLS = [
    {"id": "Q1", "name": "Quiz 1", "gradebookCategoryId": "q", "score": {"possible": 20}},
    {"id": "Q2", "name": "Quiz 2", "gradebookCategoryId": "q", "score": {"possible": 20}},
    {"id": "Q3", "name": "Quiz 3", "gradebookCategoryId": "q", "score": {"possible": 20}},
    {"id": "E1", "name": "Exam 1", "gradebookCategoryId": "e", "score": {"possible": 100}},
]
GRADES = {
    "Q1": {"displayGrade": {"score": 18.0}, "status": "Graded"},
    "Q2": {"displayGrade": {"score": 16.0}, "status": "Graded"},
}
W = {"q": 0.4, "e": 0.6}

print("\n[breakdown]")
bd = build_breakdown(CATS, COLS, GRADES, W)
quiz = next(b for b in bd if b["category_id"] == "q")
exam = next(b for b in bd if b["category_id"] == "e")
check("quiz earned", quiz["earned"], 34.0)
check("quiz graded_possible", quiz["graded_possible"], 40.0)
check("quiz total_possible", quiz["total_possible"], 60.0)
check("quiz remaining", quiz["remaining_possible"], 20.0)
check("quiz pct so far", quiz["pct_graded"], 85.0)
check("exam has nothing graded", exam["graded_possible"], 0.0)
check("exam pct is unknown, not zero", exam["pct_graded"], None)

print("\n[current standing] graded work only, weights renormalised")
# Only quizzes are graded, so the quiz percentage IS the current grade.
check("current grade = 34/40", current_grade(bd), 85.0)

print("\n[projection]")
# quiz (34+20)/60 = 90%, exam 100/100 = 100%  ->  0.4*90 + 0.6*100
check("projected at 100%", projected_grade(bd, 1.0), 96.0)
# quiz 34/60 = 56.67%, exam 0  ->  0.4*56.67
check("projected at 0%", projected_grade(bd, 0.0), 22.67)
check("projected at 80%", projected_grade(bd, 0.8),
      0.4 * ((34 + 16) / 60 * 100) + 0.6 * 80.0)

print("\n[what do I need]")
# Exam is 60% and the only exam; to finish at 90 with quizzes maxed:
# 0.4*90 + 0.6*x = 90  ->  x = 90
n = needed_on(bd, "E1", target=90.0, rate=1.0)
check("need 90/100 on the exam", n["needed_points"], 90.0)
check("expressed as a percentage", n["needed_pct"], 90.0)
check("and it is achievable", n["achievable"], True)
check("not already secured", n["already_secured"], False)

# Same target, but assuming the last quiz is skipped entirely.
n0 = needed_on(bd, "E1", target=90.0, rate=0.0)
check("needing >100 is flagged unachievable", n0["achievable"], False)
check("needed points exceed the exam", n0["needed_points"] > 100.0, True)

# Quiz 3 with the exam assumed perfect: 0.4*q + 0.6*100 = 90 -> q = 75%
# quiz final (34+x)/60 = 0.75 -> x = 11
nq = needed_on(bd, "Q3", target=90.0, rate=1.0)
check("need 11/20 on quiz 3", nq["needed_points"], 11.0)
check("= 55%", nq["needed_pct"], 55.0)

# A target already locked in should read as secured, not as a negative score.
low = needed_on(bd, "E1", target=20.0, rate=1.0)
check("unreachable-to-fail target is secured", low["already_secured"], True)

graded = needed_on(bd, "Q1", target=90.0)
check("asking about a graded item reports the grade", graded["already_graded"], True)
check("  and its score", graded["score"], 18.0)
check("unknown column returns None", needed_on(bd, "NOPE", 90.0), None)

print("\n[verify the solver inverts the projection]")
# Feed the needed score back in and confirm the target actually lands.
target = 87.0
n2 = needed_on(bd, "E1", target=target, rate=1.0)
g2 = dict(GRADES)
g2["Q3"] = {"displayGrade": {"score": 20.0}}
g2["E1"] = {"displayGrade": {"score": n2["needed_points"]}}
bd2 = build_breakdown(CATS, COLS, g2, W)
check("scoring exactly what was asked hits the target", current_grade(bd2), target)

print("\n[points mode] no syllabus weights available")
bp = build_breakdown(CATS, COLS, GRADES, {})
# Weighted by points: quiz 60/160, exam 100/160. Only quizzes graded -> 85%.
check("current grade still computable", current_grade(bp), 85.0)
# quiz (34+20)/60=90%, exam 100% -> (60/160)*90 + (100/160)*100
check("projection weights by points", projected_grade(bp, 1.0),
      (60 / 160) * 90 + (100 / 160) * 100)
check("summarise reports the fallback", summarise(bp)["weighted_by"], "points")
check("summarise reports syllabus weighting",
      summarise(bd)["weighted_by"], "syllabus")

print("\n[edge cases]")
check("zero-point columns are ignored",
      build_breakdown(CATS, [{"id": "Z", "gradebookCategoryId": "q",
                              "score": {"possible": 0}}], {}, W), [])
check("no graded work anywhere -> no current grade",
      current_grade(build_breakdown(CATS, COLS, {}, W)), None)
ex = build_breakdown(CATS, COLS, {"Q1": {"displayGrade": {"score": 18.0},
                                         "exempt": True}}, W)
check("exempt work is excluded from the average",
      next(b for b in ex if b["category_id"] == "q")["graded_possible"], 0.0)
uncat = build_breakdown([], [{"id": "X", "name": "Loose", "score": {"possible": 10}}],
                        {"X": {"displayGrade": {"score": 9.0}}}, {})
check("columns with no category still counted", uncat[0]["earned"], 9.0)
check("  and labelled", uncat[0]["title"], "Uncategorised")

print("\n[letters]")
check("90 is an A", letter_for(90.0), "A")
check("89.9 is a B", letter_for(89.9), "B")
check("below the scale is an F", letter_for(12.0), "F")
check("None stays None", letter_for(None), None)
check("custom scale honoured", letter_for(88.0, {"A": 87.0, "B": 77.0}), "A")

print("\n" + "=" * 60)
print("ALL CHECKS PASSED" if not FAILS else f"FAILED ({len(FAILS)}): " + ", ".join(FAILS))
print("=" * 60)
sys.exit(1 if FAILS else 0)
