"""RAG top-k memory recall: simple lexical retrieval baseline for KDD experiments.

Unlike memory_store.py (family-level leave-self-out retrieval), this module implements
a deterministic BM25-like lexical retrieval that ranks historical cases by query-document
overlap. It serves as a stronger "retrieval-augmented" baseline than raw prompt memory
for KDD reviewers.

Query features: bug_class, design name, signal names, failure signature
Document: memory skill record (design, family, rationale, bug_class)

No replay/no-regression promotion. No vector DB. No LLM in the retrieval loop.
"""
from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from pathlib import Path


def _tokenize(text: str) -> list[str]:
    """Simple whitespace + punctuation tokenizer."""
    return re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", text.lower())


def _build_inverted_index(docs: list[dict]) -> dict[str, dict[int, int]]:
    """Build inverted index: term -> {doc_idx: term_frequency}."""
    idx: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for i, doc in enumerate(docs):
        text = " ".join([
            doc.get("design", ""),
            doc.get("family", ""),
            doc.get("bug_class", ""),
            doc.get("rationale", ""),
        ])
        for tok in _tokenize(text):
            idx[tok][i] += 1
    return idx


def bm25_score(
    query: str,
    doc_idx: int,
    inverted_index: dict[str, dict[int, int]],
    doc_lengths: list[int],
    avg_dl: float,
    n_docs: int,
    k1: float = 1.2,
    b: float = 0.75,
) -> float:
    """BM25 scoring for one document."""
    score = 0.0
    query_terms = _tokenize(query)
    dl = doc_lengths[doc_idx]
    for term in set(query_terms):
        postings = inverted_index.get(term, {})
        df = len(postings)
        if df == 0:
            continue
        tf = postings.get(doc_idx, 0)
        idf = math.log((n_docs - df + 0.5) / (df + 0.5) + 1.0)
        numerator = tf * (k1 + 1)
        denominator = tf + k1 * (1 - b + b * dl / avg_dl)
        score += idf * numerator / denominator
    return score


def _build_query(case: dict) -> str:
    """Build a query string from case features."""
    parts = [
        case.get("design_name", ""),
        case.get("top_module", ""),
        case.get("mutation_type", "") or case.get("mut", ""),
    ]
    # Add signal names from golden/buggy RTL if available
    return " ".join(parts)


def _doc_text(doc: dict) -> str:
    """Full text of a memory document for length computation."""
    return " ".join([
        doc.get("design", ""),
        doc.get("family", ""),
        doc.get("bug_class", ""),
        doc.get("rationale", ""),
    ])


def make_rag_recall_fn(memory_path, k: int = 3):
    """Create a RAG-based recall function using BM25 lexical retrieval.

    Args:
        memory_path: Path to JSONL memory file (same format as memory_store.py).
        k: Number of top documents to return.

    Returns:
        recall_fn(case: dict) -> str: Formatted memory context string.
    """
    # Load memory
    mem_path = Path(memory_path)
    if not mem_path.exists():
        def _empty(case: dict) -> str:
            return ""
        return _empty

    docs = [json.loads(l) for l in mem_path.read_text().splitlines() if l.strip()]
    if not docs:
        def _empty(case: dict) -> str:
            return ""
        return _empty

    # Classify each doc with a bug_class (same heuristic as generalized_memory.py)
    for doc in docs:
        if "bug_class" not in doc:
            doc["bug_class"] = _classify_bug(doc.get("rationale", ""))

    # Build BM25 index
    inverted_index = _build_inverted_index(docs)
    doc_lengths = [len(_tokenize(_doc_text(d))) for d in docs]
    avg_dl = sum(doc_lengths) / len(doc_lengths) if doc_lengths else 1.0

    def recall(case: dict) -> str:
        name = case.get("design_name", "")
        query = _build_query(case)

        # Score all docs, excluding exact same design (leave-self-out)
        scored = []
        for i, doc in enumerate(docs):
            if doc.get("design") == name:
                continue  # leave-self-out
            s = bm25_score(query, i, inverted_index, doc_lengths, avg_dl, len(docs))
            if s > 0:
                scored.append((s, doc))

        scored.sort(key=lambda x: -x[0])
        cands = [doc for _, doc in scored[:k]]

        if not cands:
            return ""

        lines = ["", "## 检索到的相似修复经验（BM25 检索，仅供参考；当前 bug 需独立判断）"]
        for i, s in enumerate(cands, 1):
            lines.append(
                f"案例{i}[{s['design']}/{s.get('bug_class','?')}]: "
                f"修法 — {s.get('rationale','')[:120]}"
            )
        return "\n".join(lines)

    return recall


# ---- bug classification heuristic (mirrors generalized_memory.classify_bug) ----
_BUG_CLASS_KEYWORDS: dict[str, list[str]] = {
    "constant_error": ["常量", "constant", "literal", "位宽", "width"],
    "off_by_one": ["off by one", "差一", "+1", "-1", "边界", "boundary", "range"],
    "condition_error": ["条件", "condition", "if", "case", "分支", "branch", "switch"],
    "operator_error": ["运算符", "operator", "+", "-", "*", "&", "|", "^", "运算"],
    "data_flow_error": ["数据流", "data flow", "assign", "wire", "reg", "连接", "驱动"],
    "state_swap_error": ["状态", "state", "fsm", "swap", "交换", "错位", "reg_map"],
    "index_error": ["索引", "index", "数组", "array", "memory"],
    "assignment_error": ["赋值", "assign", "阻塞", "非阻塞", "blocking", "nonblocking"],
    "signal_error": ["信号", "signal", "端口", "port", "未声明", "未连接", "undeclared"],
}


def _classify_bug(rationale: str) -> str:
    """Classify a bug based on keyword matching in rationale text."""
    text = rationale.lower()
    scores: dict[str, int] = {}
    for cls, keywords in _BUG_CLASS_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw.lower() in text)
        if score > 0:
            scores[cls] = score
    if not scores:
        return "other"
    return max(scores, key=scores.get)
