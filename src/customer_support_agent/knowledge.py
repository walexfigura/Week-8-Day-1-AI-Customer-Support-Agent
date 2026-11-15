"""Local Markdown knowledge base with lightweight relevance scoring.

No vector database: documents are split into sections at Markdown headings,
tokenized, and ranked with BM25 (a classic keyword-relevance formula used by
search engines). It is transparent, deterministic, and good enough to show how
an agent *retrieves* knowledge through a tool instead of having the whole
knowledge base pasted into every prompt.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "by",
    "can",
    "do",
    "does",
    "for",
    "from",
    "has",
    "have",
    "how",
    "i",
    "if",
    "in",
    "is",
    "it",
    "its",
    "my",
    "of",
    "on",
    "or",
    "our",
    "so",
    "that",
    "the",
    "their",
    "this",
    "to",
    "was",
    "we",
    "what",
    "when",
    "where",
    "which",
    "will",
    "with",
    "you",
    "your",
    "me",
    "there",
    "they",
    "about",
}
# Small synonym map so customer wording ("money back") finds policy wording ("refund").
_SYNONYMS = {
    "money": ["refund"],
    "refunded": ["refund"],
    "refunds": ["refund"],
    "returning": ["return"],
    "returns": ["return"],
    "returned": ["return"],
    "package": ["shipment", "order"],
    "parcel": ["shipment"],
    "late": ["delayed", "estimated"],
    "delayed": ["late"],
    "missing": ["lost", "received"],
    "lost": ["missing"],
    "broken": ["damaged", "defective"],
    "cracked": ["damaged"],
    "damaged": ["defective"],
    "flicker": ["flickering"],
    "flickers": ["flickering"],
    "human": ["escalate", "escalation", "person"],
    "manager": ["escalate", "escalation"],
    "swap": ["exchange"],
    "dishwasher": ["care"],
    "warranty": ["defect"],
}

KB_CATEGORIES = ("policies", "faqs", "procedures")


def parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    """Parse a simple `key: value` YAML-style header delimited by `---` lines.

    Only flat string values are supported (that's all this project needs), which
    keeps us free of a YAML dependency.
    """
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    meta: dict[str, str] = {}
    for index in range(1, len(lines)):
        line = lines[index]
        if line.strip() == "---":
            body = "\n".join(lines[index + 1 :]).lstrip("\n")
            return meta, body
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
    return {}, text  # no closing delimiter: treat the whole file as body


def tokenize(text: str, expand: bool = False) -> list[str]:
    tokens = [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]
    if expand:
        extra = [syn for t in tokens for syn in _SYNONYMS.get(t, [])]
        tokens.extend(extra)
    return tokens


@dataclass
class Section:
    doc_id: str
    heading: str
    text: str
    tokens: list[str] = field(default_factory=list)


@dataclass
class Document:
    doc_id: str  # path relative to knowledge-base/, e.g. "policies/return-policy.md"
    title: str
    doc_type: str
    metadata: dict[str, str]
    body: str
    sections: list[Section]

    @property
    def category(self) -> str:
        return self.doc_id.split("/", 1)[0]


@dataclass
class SearchHit:
    doc_id: str
    title: str
    doc_type: str
    section: str
    snippet: str
    score: float

    def as_dict(self) -> dict:
        return {
            "document_id": self.doc_id,
            "title": self.title,
            "doc_type": self.doc_type,
            "section": self.section,
            "snippet": self.snippet,
            "score": round(self.score, 3),
        }


def _split_sections(doc_id: str, title: str, body: str) -> list[Section]:
    """Split a Markdown body at `##`/`###` headings so results point at a section."""
    sections: list[Section] = []
    heading = title
    buffer: list[str] = []

    def flush() -> None:
        text = "\n".join(buffer).strip()
        if text:
            # Heading words count twice: they are strong relevance signals.
            tokens = tokenize(f"{title} {heading} {heading} {text}")
            sections.append(Section(doc_id, heading, text, tokens))

    for line in body.splitlines():
        match = re.match(r"^(#{1,3})\s+(.*)", line)
        if match:
            flush()
            heading = match.group(2).strip()
            buffer = []
        else:
            buffer.append(line)
    flush()
    return sections


class KnowledgeBase:
    """Loads every `.md` file under `knowledge-base/` and searches it with BM25."""

    k1 = 1.4
    b = 0.75

    def __init__(self, root: Path):
        if not root.is_dir():
            raise FileNotFoundError(f"Knowledge base folder not found: {root}")
        self.root = root
        self.documents: dict[str, Document] = {}
        for path in sorted(root.rglob("*.md")):
            doc_id = path.relative_to(root).as_posix()
            meta, body = parse_front_matter(path.read_text(encoding="utf-8"))
            title = meta.get("title") or path.stem.replace("-", " ").title()
            doc_type = meta.get("doc_type", doc_id.split("/", 1)[0].rstrip("s"))
            self.documents[doc_id] = Document(doc_id, title, doc_type, meta, body, _split_sections(doc_id, title, body))
        if not self.documents:
            raise FileNotFoundError(f"No Markdown documents found under {root}")
        self._index()

    def _index(self) -> None:
        self.sections = [s for d in self.documents.values() for s in d.sections]
        self.avg_len = sum(len(s.tokens) for s in self.sections) / max(len(self.sections), 1)
        df: Counter[str] = Counter()
        for section in self.sections:
            df.update(set(section.tokens))
        n = len(self.sections)
        self.idf = {term: math.log(1 + (n - freq + 0.5) / (freq + 0.5)) for term, freq in df.items()}

    def _bm25(self, query_tokens: list[str], section: Section) -> float:
        counts = Counter(section.tokens)
        length = len(section.tokens)
        score = 0.0
        for term in set(query_tokens):
            tf = counts.get(term, 0)
            if not tf:
                continue
            norm = tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * length / self.avg_len))
            score += self.idf.get(term, 0.0) * norm
        return score

    def search(self, query: str, category: str = "all", max_results: int = 4) -> list[SearchHit]:
        query_tokens = tokenize(query, expand=True)
        if not query_tokens:
            return []
        best_per_doc: dict[str, SearchHit] = {}
        for section in self.sections:
            doc = self.documents[section.doc_id]
            if category != "all" and doc.category != category:
                continue
            score = self._bm25(query_tokens, section)
            if doc.metadata.get("authority") == "authoritative":
                score *= 1.15  # gentle preference for official policy over summaries
            if score <= 0:
                continue
            current = best_per_doc.get(doc.doc_id)
            if current is None or score > current.score:
                best_per_doc[doc.doc_id] = SearchHit(
                    doc.doc_id,
                    doc.title,
                    doc.doc_type,
                    section.heading,
                    _snippet(section.text),
                    score,
                )
        hits = sorted(best_per_doc.values(), key=lambda h: h.score, reverse=True)
        return hits[: max(1, min(max_results, 8))]

    def get(self, doc_id: str) -> Document | None:
        doc_id = doc_id.strip().lstrip("/")
        if doc_id.startswith("knowledge-base/"):
            doc_id = doc_id[len("knowledge-base/") :]
        if not doc_id.endswith(".md"):
            doc_id += ".md"
        return self.documents.get(doc_id)


def _snippet(text: str, limit: int = 320) -> str:
    flat = re.sub(r"\s+", " ", text).strip()
    return flat if len(flat) <= limit else flat[: limit - 1].rsplit(" ", 1)[0] + "…"


class WorkflowLibrary:
    """The agent's procedures in `agent/workflows/*.md`, loaded on demand."""

    def __init__(self, folder: Path):
        if not folder.is_dir():
            raise FileNotFoundError(f"Workflow folder not found: {folder}")
        self.folder = folder
        self.workflows = {p.stem: p for p in sorted(folder.glob("*.md"))}

    @property
    def names(self) -> list[str]:
        return list(self.workflows)

    def read(self, name: str) -> str | None:
        path = self.workflows.get(name.strip().removesuffix(".md"))
        return path.read_text(encoding="utf-8") if path else None
