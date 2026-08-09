"""Topologically order skills from their reads/writes dependency graph.

A skill that *writes* a resource must run before any skill that *reads* it
(``docs/architecture.md``: the reads/writes graph "lets a planner topologically
order skills and detect missing preconditions"). This is what lets a preset be a
declarative *set* of skills rather than a hand-maintained ordered list — the order
falls out of the dependencies.

The sort is **stable**: among skills with no ordering constraint between them, the
input order is preserved (via a min-heap on original index), so a preset's listed
order is the deterministic tiebreak and output stays reproducible.
"""

from __future__ import annotations

import heapq
from collections.abc import Sequence

from .base import Skill


def topological_order(skills: Sequence[Skill]) -> list[Skill]:
    """Order ``skills`` so every writer precedes readers of the same resource.

    Raises ``ValueError`` on a cyclic dependency (no valid order exists).
    """
    items = list(skills)
    n = len(items)
    reads = [set(s.reads) for s in items]
    writes = [set(s.writes) for s in items]

    # Edge i -> j when i writes something j reads (i must precede j); skip self.
    adj: list[set[int]] = [set() for _ in range(n)]
    indeg = [0] * n
    for i in range(n):
        for j in range(n):
            if i != j and writes[i] & reads[j]:
                adj[i].add(j)
    for i in range(n):
        for j in adj[i]:
            indeg[j] += 1

    ready = [i for i in range(n) if indeg[i] == 0]
    heapq.heapify(ready)  # min-heap on original index keeps the sort stable
    order: list[int] = []
    while ready:
        i = heapq.heappop(ready)
        order.append(i)
        for j in sorted(adj[i]):
            indeg[j] -= 1
            if indeg[j] == 0:
                heapq.heappush(ready, j)

    if len(order) != n:
        raise ValueError("cyclic skill dependency graph: no valid execution order")
    return [items[i] for i in order]
