"""Weighted grade math.

Blackboard supplies the structure — categories, columns, points, and your
scores — but not how the categories are weighted; that lives in the syllabus.
These functions take both and answer the two questions worth asking: where do I
stand, and what do I need on this next thing.

Everything here is pure: plain dicts in, plain dicts out, no I/O.
"""

from __future__ import annotations

from typing import Any, Iterable

DEFAULT_SCALE = {"A": 90.0, "B": 80.0, "C": 70.0, "D": 60.0}


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def build_breakdown(
    categories: Iterable[dict[str, Any]],
    columns: Iterable[dict[str, Any]],
    grades: dict[str, dict[str, Any]],
    weights: dict[str, float] | None = None,
    assign: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Group columns into categories and total up the points.

    `weights` maps a category id to its share of the final grade (0..1). When it
    is empty every category is weighted by its points, which reproduces a plain
    points-based course.

    `assign` maps a column id to the category it really belongs in, overriding the
    `gradebookCategoryId` the instructor set. Columns it does not mention keep
    Blackboard's own answer.
    """
    weights = weights or {}
    assign = assign or {}
    by_id: dict[str, dict[str, Any]] = {}
    for cat in categories:
        cid = cat.get("id")
        if cid:
            by_id[cid] = {
                "category_id": cid,
                "title": cat.get("title") or "Uncategorised",
                "weight": weights.get(cid),
                "earned": 0.0,
                "graded_possible": 0.0,
                "total_possible": 0.0,
                "columns": [],
            }
    # Columns with no category still have to land somewhere.
    other = {
        "category_id": None, "title": "Uncategorised", "weight": weights.get(""),
        "earned": 0.0, "graded_possible": 0.0, "total_possible": 0.0, "columns": [],
    }

    for col in columns:
        possible = _num((col.get("score") or {}).get("possible"))
        if not possible or possible <= 0:
            continue  # ungraded placeholders and 0-point surveys carry no weight
        placed = assign.get(col.get("id") or "") or col.get("gradebookCategoryId")
        bucket = by_id.get(placed or "", other)
        row = grades.get(col.get("id") or "") or {}
        score = _num((row.get("displayGrade") or {}).get("score"))
        if score is None:
            score = _num(row.get("score"))
        graded = score is not None and not row.get("exempt")

        bucket["total_possible"] += possible
        if graded:
            bucket["earned"] += score
            bucket["graded_possible"] += possible
        bucket["columns"].append({
            "column_id": col.get("id"),
            "name": col.get("name"),
            "possible": possible,
            "score": score if graded else None,
            "graded": graded,
            "due": (col.get("grading") or {}).get("due"),
        })

    out = [b for b in by_id.values() if b["columns"]]
    if other["columns"]:
        out.append(other)
    for b in out:
        b["remaining_possible"] = b["total_possible"] - b["graded_possible"]
        b["pct_graded"] = (
            100.0 * b["earned"] / b["graded_possible"] if b["graded_possible"] else None
        )
    return out


def _effective_weights(breakdown: list[dict[str, Any]]) -> dict[str | None, float]:
    """Each category's weight, falling back to points when none was supplied.

    A course with no syllabus weighting still needs an answer, and weighting by
    points is what Blackboard itself does in that situation.
    """
    declared = {b["category_id"]: b["weight"] for b in breakdown
                if _num(b["weight"]) is not None}
    if declared:
        return {k: float(v) for k, v in declared.items()}
    total = sum(b["total_possible"] for b in breakdown) or 1.0
    return {b["category_id"]: b["total_possible"] / total for b in breakdown}


def current_grade(breakdown: list[dict[str, Any]]) -> float | None:
    """Your standing across graded work only, ignoring what hasn't happened yet.

    Categories with nothing graded are excluded and the remaining weights are
    renormalised, which is what "your grade so far" means to a student.
    """
    weights = _effective_weights(breakdown)
    active = [b for b in breakdown
              if b["graded_possible"] > 0 and weights.get(b["category_id"])]
    total_w = sum(weights[b["category_id"]] for b in active)
    if not active or total_w <= 0:
        return None
    return sum(weights[b["category_id"]] * b["pct_graded"] for b in active) / total_w


def projected_grade(breakdown: list[dict[str, Any]], rate: float) -> float | None:
    """Final grade if every remaining point is earned at `rate` (0..1)."""
    weights = _effective_weights(breakdown)
    active = [b for b in breakdown
              if b["total_possible"] > 0 and weights.get(b["category_id"])]
    total_w = sum(weights[b["category_id"]] for b in active)
    if not active or total_w <= 0:
        return None
    total = 0.0
    for b in active:
        final = (b["earned"] + b["remaining_possible"] * rate) / b["total_possible"]
        total += weights[b["category_id"]] * final * 100.0
    return total / total_w


def needed_on(
    breakdown: list[dict[str, Any]],
    column_id: str,
    target: float,
    rate: float = 1.0,
) -> dict[str, Any] | None:
    """What you must score on one assignment to finish the course at `target`.

    Everything else still outstanding is assumed to come in at `rate`, so the
    answer moves depending on whether you expect to ace the rest or coast.
    """
    weights = _effective_weights(breakdown)
    home = None
    column = None
    for b in breakdown:
        for col in b["columns"]:
            if col["column_id"] == column_id:
                home, column = b, col
                break
    if home is None or column is None:
        return None
    if column["graded"]:
        return {"column_id": column_id, "name": column["name"],
                "already_graded": True, "score": column["score"],
                "possible": column["possible"]}

    active = [b for b in breakdown
              if b["total_possible"] > 0 and weights.get(b["category_id"])]
    total_w = sum(weights[b["category_id"]] for b in active)
    w_home = weights.get(home["category_id"], 0.0)
    if total_w <= 0 or w_home <= 0 or home["total_possible"] <= 0:
        return None

    # Contribution from every other category, at the assumed rate.
    others = 0.0
    for b in active:
        if b["category_id"] == home["category_id"]:
            continue
        final = (b["earned"] + b["remaining_possible"] * rate) / b["total_possible"]
        others += weights[b["category_id"]] * final * 100.0

    # Remaining points in this category that are not the assignment in question.
    rest_here = max(home["remaining_possible"] - column["possible"], 0.0)
    # Solve target*total_w = others + w_home * (earned + rest*rate + x)/total * 100
    x = ((target * total_w - others) * home["total_possible"] / (w_home * 100.0)
         - home["earned"] - rest_here * rate)

    pct = 100.0 * x / column["possible"] if column["possible"] else None
    return {
        "column_id": column_id,
        "name": column["name"],
        "possible": column["possible"],
        "target": target,
        "assumed_rate": rate,
        "needed_points": round(x, 2),
        "needed_pct": round(pct, 1) if pct is not None else None,
        # Below zero means the target is already locked in; above 100 means it
        # cannot be reached on this assignment alone.
        "already_secured": x <= 0,
        "achievable": pct is not None and pct <= 100.0,
    }


def letter_for(pct: float | None, scale: dict[str, float] | None = None) -> str | None:
    if pct is None:
        return None
    scale = scale or DEFAULT_SCALE
    for name, floor in sorted(scale.items(), key=lambda kv: -kv[1]):
        if pct >= floor:
            return name
    return "F"


def summarise(
    breakdown: list[dict[str, Any]],
    scale: dict[str, float] | None = None,
    rate: float = 1.0,
) -> dict[str, Any]:
    cur = current_grade(breakdown)
    proj = projected_grade(breakdown, rate)
    weights = _effective_weights(breakdown)
    return {
        "current_pct": round(cur, 2) if cur is not None else None,
        "current_letter": letter_for(cur, scale),
        "projected_pct": round(proj, 2) if proj is not None else None,
        "projected_letter": letter_for(proj, scale),
        "assumed_rate": rate,
        "weighted_by": "syllabus" if any(
            _num(b["weight"]) is not None for b in breakdown) else "points",
        "categories": [
            {
                "title": b["title"],
                "category_id": b["category_id"],
                "weight": b["weight"],
                "effective_weight": round(weights.get(b["category_id"], 0.0), 4),
                "earned": round(b["earned"], 2),
                "graded_possible": round(b["graded_possible"], 2),
                "total_possible": round(b["total_possible"], 2),
                "pct": round(b["pct_graded"], 1) if b["pct_graded"] is not None else None,
                "columns": b["columns"],
            }
            for b in breakdown
        ],
    }
