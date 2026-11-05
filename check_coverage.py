"""
MutaMine - check if the papers we downloaded contain the gold sentences.

For every gold record in the corpus, look for its sentence (the 'text'
column) inside papers/<PMID>.txt. This tells us whether abstracts are
enough, or whether we really need the full PDFs.

How to run (after fetch_papers.py):
  python3 check_coverage.py --corpus enzyminer-corpus-demo.csv
"""

import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path


def norm(s):
    """Lower case, keep only letters and digits, so small differences in
    spaces, quotes, arrows or hyphens do not matter."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def found(sentence, paper):
    """True if the gold sentence (or a long piece of it) is in the paper."""
    s = norm(sentence)
    if not s:
        return False
    if s in paper:
        return True
    # gold sentences are sometimes cut or slightly edited: try the first
    # and the last 40 characters as well
    return len(s) > 60 and (s[:40] in paper or s[-40:] in paper)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="enzyminer-corpus-demo.csv")
    ap.add_argument("--papers", default="papers")
    args = ap.parse_args()

    source = {}
    if Path("fetch_report.csv").exists():
        with open("fetch_report.csv", newline="") as f:
            source = {r["pmid"]: r["source"] for r in csv.DictReader(f)}

    gold = defaultdict(set)   # pmid -> set of unique gold sentences
    with open(args.corpus, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            if row.get("pmid", "").isdigit() and row.get("text"):
                gold[row["pmid"]].add(row["text"].strip())

    rows, tot_hit, tot_all = [], 0, 0
    for pmid in sorted(gold, key=int):
        path = Path(args.papers) / f"{pmid}.txt"
        paper = norm(path.read_text(encoding="utf-8")) if path.exists() else ""
        hit = sum(found(s, paper) for s in gold[pmid])
        n = len(gold[pmid])
        tot_hit += hit
        tot_all += n
        rows.append({"pmid": pmid, "source": source.get(pmid, "?"),
                     "gold_sentences": n, "found": hit,
                     "missing": n - hit})
        flag = "" if hit == n else "   <-- missing some"
        print(f"{pmid} [{source.get(pmid, '?'):15}] {hit}/{n} found{flag}")

    with open("coverage_report.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    print(f"\nOverall: {tot_hit}/{tot_all} gold sentences found "
          f"({100 * tot_hit / tot_all:.0f}%)")
    by = defaultdict(lambda: [0, 0])
    for r in rows:
        by[r["source"]][0] += r["found"]
        by[r["source"]][1] += r["gold_sentences"]
    for src, (h, n) in by.items():
        print(f"  {src:15}: {h}/{n} found")
    need = [r["pmid"] for r in rows if r["missing"] > 0]
    print(f"\nPapers that are missing gold sentences ({len(need)}):")
    print("  " + (", ".join(need) if need else "none - abstracts are enough!"))


if __name__ == "__main__":
    main()
