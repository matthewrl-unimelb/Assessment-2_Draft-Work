"""
library.py - the app's "closed world" regulation library.

Two jobs:
  1. BUILD (run once, on your own computer):  python library.py build <folder_of_pdfs>
     Reads each regulation PDF, splits it into chunks (one chunk per section where
     possible, e.g. "12 CFR 217.10"), and saves them to library/chunks.json.
     You commit chunks.json to GitHub so the deployed app can read it.

  2. SEARCH (used by the app every time it needs to look something up):
     search("supplementary leverage ratio category III") -> the most relevant chunks.

Search uses BM25, a classic keyword-ranking method. It runs entirely inside the app:
no internet, no embeddings API, no cost. The AI never sees anything except the
chunks this search hands it - that is what makes the app "closed world".
"""

import json
import math
import os
import re
import sys
from collections import Counter

LIBRARY_PATH = os.path.join(os.path.dirname(__file__), "library", "chunks.json")

# --------------------------------------------------------------------------------------
# The list of source documents. "match" is a piece of text found in the PDF's file name.
# "status" is shown to the user so they know whether a rule is in force or only proposed.
# --------------------------------------------------------------------------------------
SOURCES = [
    {"match": "Part_217", "name": "12 CFR Part 217 (Regulation Q - capital)", "cite": "12 CFR", "kind": "cfr", "status": "In force", "as_of": "2026-10-27"},
    {"match": "Part_252", "name": "12 CFR Part 252 (Regulation YY - enhanced prudential standards)", "cite": "12 CFR", "kind": "cfr", "status": "In force", "as_of": "2026-10-27"},
    {"match": "Part_249", "name": "12 CFR Part 249 (Regulation WW - LCR / NSFR)", "cite": "12 CFR", "kind": "cfr", "status": "In force", "as_of": "2026-10-27"},
    {"match": "Part_225", "name": "12 CFR Part 225 (Regulation Y - bank holding companies)", "cite": "12 CFR", "kind": "cfr", "status": "In force", "as_of": "2026-10-27"},
    {"match": "Part_251", "name": "12 CFR Part 251 (Regulation XX - concentration limit)", "cite": "12 CFR", "kind": "cfr", "status": "In force", "as_of": "2026-10-27"},
    {"match": "1828", "name": "12 U.S.C. 1828 (incl. Bank Merger Act, s.1828(c))", "cite": "12 U.S.C. 1828", "kind": "page", "status": "In force", "as_of": "2026-10-29"},
    {"match": "1842", "name": "12 U.S.C. 1842 (BHC Act s.3 - acquisitions)", "cite": "12 U.S.C. 1842", "kind": "page", "status": "In force", "as_of": "2026-10-29"},
    {"match": "1852", "name": "12 U.S.C. 1852 (concentration limits)", "cite": "12 U.S.C. 1852", "kind": "page", "status": "In force", "as_of": "2026-10-29"},
    {"match": "car-nfp-ch1", "name": "OSFI CAR Guideline (2027) - Chapter 1", "cite": "OSFI CAR (2027) Ch.1", "kind": "page", "status": "In force from 1 Nov 2026", "as_of": "2026-09"},
    {"match": "car-nfp-ch2", "name": "OSFI CAR Guideline (2027) - Chapter 2", "cite": "OSFI CAR (2027) Ch.2", "kind": "page", "status": "In force from 1 Nov 2026", "as_of": "2026-09"},
    {"match": "Leverage_Requirements", "name": "OSFI Leverage Requirements Guideline (2023)", "cite": "OSFI LR Guideline", "kind": "page", "status": "In force", "as_of": "2023"},
    {"match": "B-1.01", "name": "Bank Act (Canada) - Parts IX-X (pp. 397-429)", "cite": "Bank Act (Can.)", "kind": "page", "status": "In force", "as_of": "2026-09-30", "pages": (396, 429)},
    {"match": "board-memo-basel", "name": "Fed Board memo on the March 2026 capital proposals", "cite": "Fed Board memo (19 Mar 2026)", "kind": "page", "status": "PROPOSED rules - not in force", "as_of": "2026-03-19"},
]

# Lines that are page furniture, not law (headers, footers, website clutter).
NOISE = re.compile(
    r"^(12 CFR Part \d+ \(up to date as of .*\)|.*Legal Information Institute.*|https?://\S+.*|"
    r"\d+/\d+/\d{4}, \d+:\d+.*|Page \d+ of \d+|.*\(enhanced display\) page \d+ of \d+|.*Protected B - External.*|.*Unclassified / Non classifi.*)$"
)

SECTION_HEADING = re.compile(r"^§\s?(\d{3})\.\s?([0-9a-z\-]+)\s+(.{3,160})$")
APPENDIX_HEADING = re.compile(r"^(Appendix [A-Z]+ to Part (\d{3}))\b\s*(.{0,160})$")
MAX_CHARS = 3500  # long sections are split into parts of about this size


def _clean(text):
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln and not NOISE.match(ln))


def _split_long(text, max_chars=MAX_CHARS):
    """Split long text on paragraph-ish boundaries so each piece stays readable."""
    if len(text) <= max_chars:
        return [text]
    parts, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) > max_chars and current:
            parts.append(current)
            current = ""
        current += line + "\n"
    if current.strip():
        parts.append(current)
    return parts


def _chunks_from_cfr(pages, src):
    """CFR text: one chunk per § section. The table of contents produces tiny
    'sections' with no body; those are dropped because they are under 300 characters."""
    text = _clean("\n".join(pages))
    chunks, heading, body = [], None, []

    def flush():
        if heading and len("\n".join(body)) > 300:
            sec_id, title = heading
            for i, part in enumerate(_split_long("\n".join(body))):
                label = (f"12 CFR {sec_id}" if not sec_id.startswith("Part") else f"12 CFR {sec_id}") + (f" (part {i + 1})" if i else "")
                chunks.append({"cite": label, "title": title, "text": part.strip()})

    for line in text.split("\n"):
        m = SECTION_HEADING.match(line)
        a = APPENDIX_HEADING.match(line)
        if m:
            flush()
            heading = (f"{m.group(1)}.{m.group(2)}", m.group(3).strip())
            body = []
        elif a:
            flush()
            heading = (f"Part {a.group(2)}, {a.group(1)}", a.group(3).strip() or a.group(1))
            body = []
        elif heading:
            body.append(line)
    flush()
    return chunks


def _chunks_from_pages(pages, src, first_page=1):
    """Statutes and guidelines: chunk by page groups, cited by page number."""
    chunks = []
    for i in range(0, len(pages), 2):
        text = _clean("\n".join(pages[i:i + 2]))
        if len(text) < 200:
            continue
        p = first_page + i
        for part in _split_long(text):
            chunks.append({"cite": f"{src['cite']}, p.{p}", "title": src["name"], "text": part.strip()})
    return chunks


def build(pdf_folder):
    from pypdf import PdfReader

    all_chunks = []
    files = sorted(os.listdir(pdf_folder))
    for src in SOURCES:
        match = [f for f in files if src["match"] in f and f.lower().endswith(".pdf")]
        if not match:
            print(f"  MISSING: {src['name']}")
            continue
        reader = PdfReader(os.path.join(pdf_folder, match[0]))
        start, end = src.get("pages", (0, len(reader.pages)))
        pages = [(reader.pages[i].extract_text() or "") for i in range(start, end)]
        if src["kind"] == "cfr":
            chunks = _chunks_from_cfr(pages, src)
        else:
            chunks = _chunks_from_pages(pages, src, first_page=start + 1)
        for c in chunks:
            c.update({"source": src["name"], "status": src["status"], "as_of": src["as_of"]})
        print(f"  {src['name']}: {len(chunks)} chunks")
        all_chunks.extend(chunks)

    for n, c in enumerate(all_chunks):
        c["id"] = f"C{n}"
    os.makedirs(os.path.dirname(LIBRARY_PATH), exist_ok=True)
    with open(LIBRARY_PATH, "w", encoding="utf-8") as fh:
        json.dump(all_chunks, fh, ensure_ascii=False)
    print(f"Saved {len(all_chunks)} chunks to {LIBRARY_PATH}")


# --------------------------------------------------------------------------------------
# SEARCH (BM25)
# --------------------------------------------------------------------------------------
TOKEN = re.compile(r"[a-z0-9]+(?:\.[0-9a-z]+)?")
STOP = set("the a an of to and or in for on by with as is are be this that which any such its it at from under shall may".split())


def _tokens(text):
    return [t for t in TOKEN.findall(text.lower()) if t not in STOP]


class Library:
    def __init__(self, path=LIBRARY_PATH):
        with open(path, encoding="utf-8") as fh:
            self.chunks = json.load(fh)
        self.by_id = {c["id"]: c for c in self.chunks}
        # Section number and title are counted twice so a query naming "217.10" finds it.
        self.docs = [_tokens(c["cite"] + " " + c["title"] + " " + c["cite"] + " " + c["title"] + " " + c["text"]) for c in self.chunks]
        self.lengths = [len(d) for d in self.docs]
        self.avg_len = sum(self.lengths) / max(len(self.lengths), 1)
        self.tf = [Counter(d) for d in self.docs]
        df = Counter()
        for d in self.docs:
            df.update(set(d))
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        # Word pairs in each chunk ("leverage ratio", "stress capital") - used to reward exact phrases.
        self.pairs = [Counter(zip(d, d[1:])) for d in self.docs]

    def sources(self):
        seen = {}
        for c in self.chunks:
            seen.setdefault(c["source"], {"source": c["source"], "status": c["status"], "as_of": c["as_of"], "chunks": 0})
            seen[c["source"]]["chunks"] += 1
        return list(seen.values())

    def search(self, query, k=6, k1=1.5, b=0.75):
        """Return the k best-matching chunks, each with a 'score'.
        Score = BM25 keyword score + a bonus when the query's word pairs appear together in the chunk
        (so 'supplementary leverage ratio' beats a chunk that merely mentions each word somewhere)."""
        q = _tokens(query)
        q_pairs = list(zip(q, q[1:]))
        scores = []
        for i, tf in enumerate(self.tf):
            s = 0.0
            for t in q:
                if t in tf:
                    f = tf[t]
                    s += self.idf[t] * f * (k1 + 1) / (f + k1 * (1 - b + b * self.lengths[i] / self.avg_len))
            if s > 0:
                for pair in q_pairs:
                    hits = self.pairs[i].get(pair, 0)
                    if hits:
                        s += min(hits, 3) * 0.5 * (self.idf.get(pair[0], 0) + self.idf.get(pair[1], 0))
                scores.append((s, i))
        scores.sort(reverse=True)
        return [dict(self.chunks[i], score=round(s, 2)) for s, i in scores[:k]]


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "build":
        build(sys.argv[2])
    elif len(sys.argv) >= 3 and sys.argv[1] == "search":
        lib = Library()
        for c in lib.search(" ".join(sys.argv[2:])):
            print(c["score"], c["id"], c["cite"], "-", c["title"][:70])
    else:
        print("Usage:  python library.py build <pdf_folder>   |   python library.py search <words>")
