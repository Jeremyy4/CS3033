"""
MutaMine - Step 1: Fetch papers and turn them into plain text.

What it does
  1. Reads the 38 PMIDs from the EnzyMiner corpus file.
  2. For each PMID, tries to get the FULL TEXT from the NCBI BioC API
     (only works for open access papers in PubMed Central).
  3. If there is no full text, it saves the ABSTRACT from PubMed instead
     and marks the paper as "needs PDF".
  4. Any PDFs you download by hand and put in pdfs/<PMID>.pdf are turned
     into text as well (and replace the abstract-only version).
  5. Writes one text file per paper (one sentence per line) to papers/,
     plus a report (fetch_report.csv) saying where each paper came from.

How to run
  pip install requests pymupdf
  python fetch_papers.py --corpus enzyminer-corpus-demo.csv
"""

import argparse
import csv
import json
import re
import time
from pathlib import Path

import requests

BIOC_URL = ("https://www.ncbi.nlm.nih.gov/research/bionlp/RESTful/"
            "pmcoa.cgi/BioC_json/{pmid}/unicode")
EFETCH_URL = ("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
              "?db=pubmed&id={pmid}&rettype=abstract&retmode=text")
PAUSE = 0.4  # NCBI allows about 3 requests per second without an API key

# Sections we do not want (they have no mutation impact sentences)
SKIP_SECTIONS = {"REF", "ACK_FUND", "COMP_INT", "AUTH_CONT", "SUPPL"}


# ---------------------------------------------------------------- corpus
def read_pmids(corpus_path):
    """Return the sorted list of unique PMIDs in the corpus.
    Note: the corpus 'CSV' is really tab-separated."""
    pmids = set()
    with open(corpus_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            pmid = (row.get("pmid") or "").strip()
            if pmid.isdigit():
                pmids.add(pmid)
    return sorted(pmids, key=int)


# ---------------------------------------------------------------- fetching
def bioc_passages(data):
    """Pull (section, text) pairs out of a BioC JSON reply.
    The API sometimes returns a list of collections, sometimes one."""
    collections = data if isinstance(data, list) else [data]
    out = []
    for col in collections:
        for doc in col.get("documents", []):
            for p in doc.get("passages", []):
                text = (p.get("text") or "").strip()
                section = p.get("infons", {}).get("section_type", "")
                if text and section not in SKIP_SECTIONS:
                    out.append((section, text))
    return out


def fetch_full_text(pmid):
    """Full text from BioC, or None if the paper is not open access."""
    r = requests.get(BIOC_URL.format(pmid=pmid), timeout=30)
    if r.status_code != 200:
        return None
    try:
        data = r.json()
    except json.JSONDecodeError:  # non-OA papers return a plain error message
        return None
    passages = bioc_passages(data)
    return "\n".join(t for _, t in passages) if passages else None


def fetch_abstract(pmid):
    """Title + abstract as plain text from PubMed."""
    r = requests.get(EFETCH_URL.format(pmid=pmid), timeout=30)
    return r.text.strip() if r.status_code == 200 and r.text.strip() else None


# ---------------------------------------------------------------- PDFs
def pdf_to_text(pdf_path):
    """Extract text from a PDF with PyMuPDF.
    'blocks' mode keeps each column's paragraphs together, which helps
    with two-column papers."""
    try:
        import pymupdf
    except ImportError:          # older versions only have the 'fitz' name
        import fitz as pymupdf
    parts = []
    with pymupdf.open(pdf_path) as doc:
        for page in doc:
            mid = page.rect.width / 2
            blocks = page.get_text("blocks")
            # left column first (block starts left of the middle), then right
            # column; inside each column, top to bottom
            blocks.sort(key=lambda b: (0 if b[0] < mid else 1, b[1]))
            parts.extend(b[4] for b in blocks if b[4].strip())
    text = "\n".join(parts)
    # cut everything after the reference list
    m = re.search(r"\n\s*(References|REFERENCES|Literature Cited)\s*\n", text)
    return text[:m.start()] if m else text


# ---------------------------------------------------------------- cleaning
def clean_text(text):
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)   # join words split by hyphen
    text = text.replace("→", "->")             # arrow, e.g. Glu-280→Ser
    text = re.sub(r"[ \t]+", " ", text)
    return text


def split_sentences(text):
    """Simple sentence splitter that does not break on 'e.g.', 'Fig.',
    'et al.' or decimal numbers."""
    text = re.sub(r"\s*\n\s*", " ", text)
    protect = ["e.g.", "i.e.", "et al.", "Fig.", "Figs.", "approx.", "vs.", "ca.", "no."]
    for i, p in enumerate(protect):
        text = text.replace(p, f"<P{i}>")
    sents = re.split(r"(?<=[.!?])\s+(?=[A-Z(\[])", text)
    out = []
    for s in sents:
        for i, p in enumerate(protect):
            s = s.replace(f"<P{i}>", p)
        s = s.strip()
        if len(s) > 3:
            out.append(s)
    return out


def save_sentences(text, out_path):
    sents = split_sentences(clean_text(text))
    out_path.write_text("\n".join(sents) + "\n", encoding="utf-8")
    return len(sents)


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="enzyminer-corpus-demo.csv")
    ap.add_argument("--out", default="papers")
    ap.add_argument("--pdfs", default="pdfs")
    ap.add_argument("--offline", action="store_true",
                    help="skip downloading, only convert PDFs in pdfs/")
    args = ap.parse_args()

    out_dir, pdf_dir = Path(args.out), Path(args.pdfs)
    out_dir.mkdir(exist_ok=True)
    pdf_dir.mkdir(exist_ok=True)

    pmids = read_pmids(args.corpus)
    print(f"{len(pmids)} PMIDs found in the corpus")

    report = []
    for i, pmid in enumerate(pmids, 1):
        out_path = out_dir / f"{pmid}.txt"
        pdf_path = pdf_dir / f"{pmid}.pdf"
        source, n = "missing", 0

        if pdf_path.exists():                       # a PDF always wins
            n = save_sentences(pdf_to_text(pdf_path), out_path)
            source = "pdf"
        elif not args.offline:
            try:
                text = fetch_full_text(pmid)
                time.sleep(PAUSE)
                if text:
                    source = "full_text_bioc"
                else:
                    text = fetch_abstract(pmid)
                    time.sleep(PAUSE)
                    source = "abstract_only" if text else "missing"
                if text:
                    n = save_sentences(text, out_path)
            except requests.RequestException as e:
                source = f"error: {e.__class__.__name__}"

        report.append({"pmid": pmid, "source": source, "sentences": n,
                       "needs_pdf": source in ("abstract_only", "missing")
                                    or source.startswith("error")})
        print(f"[{i:2}/{len(pmids)}] {pmid}: {source} ({n} sentences)")

    with open("fetch_report.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["pmid", "source", "sentences", "needs_pdf"])
        w.writeheader()
        w.writerows(report)

    need = [r["pmid"] for r in report if r["needs_pdf"]]
    print(f"\nDone. {len(pmids) - len(need)} papers have full text.")
    if need:
        print(f"{len(need)} papers need a PDF from the library "
              f"(save as {pdf_dir}/<PMID>.pdf and run again):")
        print("  " + ", ".join(need))


if __name__ == "__main__":
    main()
