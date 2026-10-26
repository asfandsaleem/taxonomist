"""DocStore — the only integration surface a host implements. Docs out
{id, title, text, meta}, tags in; snapshots make every write reversible.

Ships SqliteStore so `taxonomist` works out of the box on a JSONL ingest.
Hosts with their own DB implement the same five methods."""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Protocol


@dataclass
class Doc:
    id: str
    title: str = ""
    text: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class TagWrite:
    doc_id: str
    level: str               # 'l1' | 'l2' | 'l3'
    value: str
    confidence: float = 1.0


class DocStore(Protocol):
    def docs(self, *, missing_level: str | None = None,
             limit: int | None = None) -> Iterator[Doc]: ...
    def tags(self, doc_id: str) -> dict[str, str]: ...
    def write_tags(self, items: list[TagWrite],
                   provenance: str) -> int: ...   # returns snapshot id
    def observe(self, doc_id: str, level: str, parent: str,
                suggested_id: str, rationale: str) -> None: ...
    def observations(self) -> list[dict[str, Any]]: ...


class SqliteStore:
    """Self-contained store: taxonomy.db in the project directory."""

    def __init__(self, path: str | Path = "taxonomy.db"):
        self.conn = sqlite3.connect(str(path))
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS docs(
                id TEXT PRIMARY KEY, title TEXT, text TEXT, meta TEXT);
            CREATE TABLE IF NOT EXISTS tags(
                doc_id TEXT, level TEXT, value TEXT, confidence REAL,
                source TEXT, PRIMARY KEY(doc_id, level));
            CREATE TABLE IF NOT EXISTS snapshots(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at REAL, provenance TEXT, rows_json TEXT);
            CREATE TABLE IF NOT EXISTS observations(
                doc_id TEXT, level TEXT, parent TEXT, suggested_id TEXT,
                rationale TEXT, created_at REAL);
            CREATE TABLE IF NOT EXISTS proposals(
                id INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT,
                parent_path TEXT, node_id TEXT, label TEXT, rationale TEXT,
                sample_docs TEXT, created_at REAL);
        """)

    # -- ingest / read -----------------------------------------------------
    def add_docs(self, docs: list[Doc]) -> int:
        self.conn.executemany(
            "INSERT OR REPLACE INTO docs VALUES (?,?,?,?)",
            [(d.id, d.title, d.text, json.dumps(d.meta)) for d in docs])
        self.conn.commit()
        return len(docs)

    def docs(self, *, missing_level: str | None = None,
             limit: int | None = None) -> Iterator[Doc]:
        q = "SELECT id, title, text, meta FROM docs"
        args: list[Any] = []
        if missing_level:
            q += " WHERE id NOT IN (SELECT doc_id FROM tags WHERE level=?)"
            args.append(missing_level)
        if limit:
            q += " LIMIT ?"
            args.append(limit)
        for r in self.conn.execute(q, args):
            yield Doc(r[0], r[1] or "", r[2] or "", json.loads(r[3] or "{}"))

    def tags(self, doc_id: str) -> dict[str, str]:
        return {lv: v for lv, v in self.conn.execute(
            "SELECT level, value FROM tags WHERE doc_id=?", (doc_id,))}

    def doc_ids_missing(self, level: str) -> list[str]:
        return [r[0] for r in self.conn.execute(
            "SELECT id FROM docs WHERE id NOT IN "
            "(SELECT doc_id FROM tags WHERE level=?)", (level,))]

    def coverage(self) -> dict[str, int]:
        total = self.conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
        out = {"total": total}
        for lv in ("l1", "l2", "l3"):
            out[lv] = self.conn.execute(
                "SELECT COUNT(DISTINCT doc_id) FROM tags WHERE level=?",
                (lv,)).fetchone()[0]
        return out

    # -- writes ------------------------------------------------------------
    def write_tags(self, items: list[TagWrite], provenance: str) -> int:
        """Delete-then-insert per (doc,level); snapshot old rows first."""
        cur = self.conn.execute(
            "INSERT INTO snapshots(created_at, provenance, rows_json) "
            "VALUES (?,?,'')", (time.time(), provenance))
        snap_id = cur.lastrowid
        keys = {(i.doc_id, i.level) for i in items}
        old = [dict(zip(("doc_id", "level", "value", "confidence", "source"), r))
               for did, lv in keys
               for r in self.conn.execute(
                   "SELECT doc_id,level,value,confidence,source FROM tags "
                   "WHERE doc_id=? AND level=?", (did, lv))]
        self.conn.execute("UPDATE snapshots SET rows_json=? WHERE id=?",
                          (json.dumps(old), snap_id))
        with self.conn:
            for did, lv in keys:
                self.conn.execute(
                    "DELETE FROM tags WHERE doc_id=? AND level=?", (did, lv))
            self.conn.executemany(
                "INSERT INTO tags VALUES (?,?,?,?,?)",
                [(i.doc_id, i.level, i.value, i.confidence, provenance)
                 for i in items])
        return snap_id

    def restore(self, snapshot_id: int) -> int:
        row = self.conn.execute(
            "SELECT rows_json FROM snapshots WHERE id=?",
            (snapshot_id,)).fetchone()
        rows = json.loads(row[0]) if row else []
        with self.conn:
            for r in rows:
                self.conn.execute(
                    "INSERT OR REPLACE INTO tags VALUES (?,?,?,?,?)",
                    (r["doc_id"], r["level"], r["value"],
                     r["confidence"], r["source"]))
        return len(rows)

    # -- evolution ---------------------------------------------------------
    def observe(self, doc_id, level, parent, suggested_id, rationale) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO observations VALUES (?,?,?,?,?,?)",
                (doc_id, level, parent, suggested_id, rationale, time.time()))

    def observations(self) -> list[dict[str, Any]]:
        return [dict(zip(("doc_id", "level", "parent", "suggested_id",
                          "rationale", "created_at"), r))
                for r in self.conn.execute(
                    "SELECT * FROM observations ORDER BY created_at")]

    def add_proposal(self, parent_path, node_id, label, rationale,
                     sample_docs) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO proposals(status,parent_path,node_id,label,"
                "rationale,sample_docs,created_at) VALUES('pending',?,?,?,?,?,?)",
                (parent_path, node_id, label, rationale,
                 json.dumps(sample_docs), time.time()))
            return cur.lastrowid

    def proposals(self, status="pending") -> list[dict[str, Any]]:
        return [dict(zip(("id", "status", "parent_path", "node_id", "label",
                          "rationale", "sample_docs", "created_at"), r))
                for r in self.conn.execute(
                    "SELECT * FROM proposals WHERE status=? ORDER BY id",
                    (status,))]

    def set_proposal(self, pid: int, status: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE proposals SET status=? WHERE id=?",
                              (status, pid))
