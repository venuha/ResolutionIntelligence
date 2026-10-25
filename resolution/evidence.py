"""Case-scoped lexical RAG over the Week 2 synthetic source export."""
import json
import re
from pathlib import Path
from rank_bm25 import BM25Okapi
from .store import fingerprint

DATA = Path(__file__).resolve().parents[1] / "data"


def tokens(text):
    return re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", text.lower())


class Corpus:
    def __init__(self):
        self.cases = {c["id"]: c for c in json.loads((DATA / "cases.json").read_text())}
        self.documents = json.loads((DATA / "documents.json").read_text())

    def docs(self, case_id):
        if case_id not in self.cases:
            raise KeyError("Case not found")
        return [d for d in self.documents if d.get("case_id") == case_id]

    def search(self, case_id, query, limit=8):
        # Filter BEFORE ranking. No shared private-upload indexes or cross-case fallback.
        docs = self.docs(case_id)
        chunks = []
        for d in docs:
            for i, paragraph in enumerate(re.split(r"\n\n+", d["text"])):
                if paragraph.strip():
                    chunks.append({"source_id": d["id"], "title": d["title"],
                                   "chunk_id": f"{d['id']}:{i}", "text": paragraph,
                                   "date": d["date"], "synthetic": True})
        index = BM25Okapi([tokens(x["text"]) for x in chunks])
        scores = index.get_scores(tokens(query))
        ranked = sorted(zip(scores, chunks), key=lambda p: p[0], reverse=True)
        return [{**c, "score": round(float(s), 3)} for s, c in ranked[:limit]]

    def version(self, case_id):
        return fingerprint(self.docs(case_id))


POLICY = {
    "id": "DEMO-POLICY-2026-10-v1", "title": "Fictional telecom refund operating procedure",
    "version": "1", "synthetic": True,
    "rules": [
        "Investigate warehouse acceptance or device mismatches before proposing a refund.",
        "A refund proposal requires a recorded approval, matched inspection and a current processor lookup.",
        "Only approved amounts not already submitted may be proposed; provisional credit is not a merchant refund.",
        "An existing submitted refund requires tracing, not a duplicate refund.",
        "A supervisor must approve the exact action and customer update before execution.",
        "Processor success is not proof of customer bank posting. Close only on verified matching bank evidence.",
        "Contradictory financial records or unavailable required tools require human investigation.",
    ],
}
