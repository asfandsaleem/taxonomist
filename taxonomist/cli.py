"""taxonomist — CLI surface.

  taxonomist ingest docs.jsonl --db taxonomy.db
  taxonomist tree --taxonomy taxonomy.json
  taxonomist coverage
  taxonomist tag --level l3 --batch 1000 [--no-suggest]
  taxonomist audit -n 200
  taxonomist observations [--min-docs 3]
  taxonomist proposals list|accept <id>|apply <id>
  taxonomist restore <snapshot_id>

Data model: docs carry meta.l1/meta.l2/meta.l3 tags (levels). The store
defaults to ./taxonomy.db (sqlite); implement DocStore for your own schema.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .store import Doc, SqliteStore
from .taxonomy import Taxonomy


def _load(args) -> Taxonomy:
    return Taxonomy.load(args.taxonomy)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="taxonomist")
    p.add_argument("--db", default="taxonomy.db")
    p.add_argument("--taxonomy", default="taxonomy.json")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ingest").add_argument("file")
    sub.add_parser("tree")
    sub.add_parser("coverage")

    tg = sub.add_parser("tag")
    tg.add_argument("--level", default="l3")
    tg.add_argument("--batch", type=int, default=1000)
    tg.add_argument("--max-batches", type=int)
    tg.add_argument("--concurrency", type=int, default=32)
    tg.add_argument("--no-suggest", action="store_true")

    au = sub.add_parser("audit")
    au.add_argument("-n", type=int, default=200)

    ob = sub.add_parser("observations")
    ob.add_argument("--min-docs", type=int, default=3)
    ob.add_argument("--propose", action="store_true")

    pr = sub.add_parser("proposals")
    pr.add_argument("action", choices=["list", "accept", "apply"])
    pr.add_argument("id", type=int, nargs="?")

    ag = sub.add_parser("agent")
    ag.add_argument("--turns", type=int, default=30)
    ag.add_argument("--run-dir", default="tmp/agent_runs")

    rs = sub.add_parser("restore")
    rs.add_argument("snapshot", type=int)

    args = p.parse_args(argv)
    store = SqliteStore(args.db)

    if args.cmd == "ingest":
        docs = [Doc(str(d.get("id")), d.get("title", ""), d.get("text", ""),
                    d.get("meta", {}))
                for d in map(json.loads, open(args.file))]
        print("ingested:", store.add_docs(docs))
    elif args.cmd == "tree":
        t = _load(args)
        for l1, lab in t.l1.items():
            print(f"{l1}  {lab}")
            for l2 in t.l2s(l1):
                print(f"  {l2}  {t.l2.get(l2, '')}  ({len(t.l3s(l2))} leaves)")
        probs = t.lint()
        if probs:
            print("lint:", *probs, sep="\n  ")
    elif args.cmd == "coverage":
        c = store.coverage()
        for lv in ("l1", "l2", "l3"):
            pct = 100 * c[lv] / max(1, c["total"])
            print(f"{lv}: {c[lv]}/{c['total']}  ({pct:.1f}%)")
    elif args.cmd == "tag":
        from .classify import classify
        stats = asyncio.run(classify(
            store, _load(args), level=args.level, batch=args.batch,
            max_batches=args.max_batches, concurrency=args.concurrency,
            suggest=not args.no_suggest))
        print("done:", stats)
    elif args.cmd == "audit":
        from .verify import audit, summarize
        rows = asyncio.run(audit(store, _load(args), n=args.n))
        s = summarize(rows)
        print("verdicts:", s)
        for r in rows:
            if r["verdict"] not in ("ok",):
                print(" ", r["id"], r["verdict"], r.get("suggested") or "")
    elif args.cmd == "observations":
        from .evolve import cluster, propose
        for g in cluster(store, min_docs=args.min_docs):
            print(f"l3  {g['suggested_id']:35} {g['parent']:20} "
                  f"{g['docs']} docs")
        if args.propose:
            print("proposals:", propose(store, min_docs=args.min_docs))
    elif args.cmd == "proposals":
        if args.action == "list":
            for pr_ in store.proposals("pending"):
                print(f"#{pr_['id']} {pr_['node_id']} -> {pr_['parent_path']}"
                      f"  {pr_['rationale'][:60]}")
        elif args.action == "accept":
            store.set_proposal(args.id, "accepted")
            print("accepted", args.id)
        else:
            from .evolve import apply
            print(apply(store, _load(args), args.taxonomy, args.id))
    elif args.cmd == "agent":
        from .agent import agent
        print(asyncio.run(agent(store, _load(args), turns=args.turns,
                                run_dir=args.run_dir)))
    elif args.cmd == "restore":
        print("restored rows:", store.restore(args.snapshot))
    return 0


if __name__ == "__main__":
    sys.exit(main())
