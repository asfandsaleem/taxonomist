"""The taxonomy is DATA — a JSON file the host project versions.
L1 → L2 → L3 with labels; validates its own integrity."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Taxonomy:
    l1: dict[str, str]                          # id -> label
    l1_to_l2: dict[str, list[str]]              # l1 -> [l2 ids]
    l2: dict[str, str]                          # id -> label
    l3: dict[str, dict[str, str]] = field(default_factory=dict)  # l2 -> {leaf: label}

    def l2s(self, l1: str) -> list[str]:
        return self.l1_to_l2.get(l1, [])

    def l3s(self, l2: str) -> dict[str, str]:
        return self.l3.get(l2, {})

    def parent_l1(self, l2: str) -> str | None:
        return next((a for a, bs in self.l1_to_l2.items() if l2 in bs), None)

    def valid_l2(self, l2: str) -> bool:
        return l2 in self.l2 and self.parent_l1(l2) is not None

    def valid_l3(self, l2: str, leaf: str) -> bool:
        return leaf in self.l3.get(l2, {})

    def add_leaf(self, l2: str, leaf: str, label: str) -> None:
        if l2 not in self.l2:
            raise ValueError(f"unknown L2 {l2!r}")
        self.l3.setdefault(l2, {})[leaf] = label

    def add_l2(self, l1: str, l2: str, label: str) -> None:
        if l1 not in self.l1:
            raise ValueError(f"unknown L1 {l1!r}")
        self.l2[l2] = label
        self.l1_to_l2.setdefault(l1, []).append(l2)

    def lint(self) -> list[str]:
        problems = []
        for l1, l2s in self.l1_to_l2.items():
            if l1 not in self.l1:
                problems.append(f"l1_to_l2 references unknown L1 {l1!r}")
            for x in l2s:
                if x not in self.l2:
                    problems.append(f"{l1!r} references unknown L2 {x!r}")
        for l2 in self.l2:
            if self.parent_l1(l2) is None:
                problems.append(f"L2 {l2!r} has no parent L1")
        for l2, leaves in self.l3.items():
            if l2 not in self.l2:
                problems.append(f"L3 vocab under unknown L2 {l2!r}")
            if not leaves:
                problems.append(f"L2 {l2!r} has empty L3 vocab")
        return problems

    def to_dict(self) -> dict[str, Any]:
        return {"l1": self.l1, "l1_to_l2": self.l1_to_l2,
                "l2": self.l2, "l3": self.l3}

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2,
                                         ensure_ascii=False))

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Taxonomy":
        t = cls(l1=dict(d["l1"]), l1_to_l2={k: list(v) for k, v in
                                            d["l1_to_l2"].items()},
                l2=dict(d["l2"]),
                l3={k: dict(v) for k, v in d.get("l3", {}).items()})
        problems = t.lint()
        if problems:
            raise ValueError("taxonomy fails lint: " + "; ".join(problems[:10]))
        return t

    @classmethod
    def load(cls, path: str | Path) -> "Taxonomy":
        return cls.from_dict(json.loads(Path(path).read_text()))
