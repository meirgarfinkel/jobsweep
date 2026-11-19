"""Facts read out of a posting's description: years of experience asked for,
and the technologies it names. Used by scoring and by the review board."""

from __future__ import annotations

import re

# "5+ years of experience", "7-10 years of professional experience",
# "at least 6 years' experience", "experience of 8 years", "5+ שנות ניסיון".
_YEARS_PATTERNS = (
    re.compile(r"(?:\b(over|more than)\s+)?\b(\d{1,2})\s*(?:\+|plus)?\s*"
               r"(?:(?:-|–|to)\s*\d{1,2}\s*\+?\s*)?years?\b[^.\n]{0,60}?"
               r"\bexperience", re.I),
    re.compile(r"\bexperience\b[^.\n]{0,25}?\b(?:(over|more than)\s+)?"
               r"(\d{1,2})\s*\+?\s*years?\b", re.I),
    re.compile(r"()(\d{1,2})\s*\+?\s*(?:שנות|שנים)[^.\n]{0,20}?ניסיון"),
    re.compile(r"ניסיון[^.\n]{0,20}?(?:(מעל|לפחות)\s*)?(\d{1,2})\s*\+?\s*שנ"),
)


def years_required(text: str) -> int | None:
    """Largest years-of-experience requirement stated in the text, or None.

    Numbers above 15 are ignored: those are company boasts ("20 years of
    experience in the industry"), not requirements."""
    found = []
    for pat in _YEARS_PATTERNS:
        for m in pat.finditer(text or ""):
            n = int(m.group(2))
            if m.group(1) in ("over", "more than", "מעל"):
                n += 1
            if 0 < n <= 15:
                found.append(n)
    return max(found) if found else None


# (label, pattern). Case-insensitive unless the pattern sets (?-i): words
# like React, Go, Rust and Swift are also ordinary English.
_TECH = [
    ("Python", r"\bpython\b"), ("Java", r"\bjava\b(?!\s*script)"),
    ("JavaScript", r"\bjavascript\b"), ("TypeScript", r"\btypescript\b"),
    ("Node.js", r"\bnode(?:\.?js)?\b"), ("Go", r"\bgolang\b|(?-i:(?<=[\s,/(])Go(?=[\s,/)]))"),
    ("Rust", r"(?-i:\bRust\b)"), ("C++", r"\bc\+\+"), ("C#", r"\bc#"),
    (".NET", r"\.net\b"), ("Kotlin", r"\bkotlin\b"), ("Scala", r"\bscala\b"),
    ("Ruby", r"\bruby\b"), ("Rails", r"\brails\b"), ("PHP", r"\bphp\b"),
    ("Swift", r"(?-i:\bSwift\b)"),
    ("React", r"(?-i:\bReact\b)|\breact\.?js\b"), ("Next.js", r"\bnext\.?js\b"),
    ("Angular", r"\bangular\b"), ("Vue", r"\bvue(?:\.?js)?\b"),
    ("Django", r"\bdjango\b"), ("Flask", r"\bflask\b"),
    ("FastAPI", r"\bfastapi\b"), ("Spring", r"(?-i:\bSpring\b)"),
    ("NestJS", r"\bnest\.?js\b"), ("GraphQL", r"\bgraphql\b"),
    ("gRPC", r"\bgrpc\b"), ("REST", r"(?-i:\bREST(?:ful)?\b)"),
    ("SQL", r"(?-i:\bSQL\b)"), ("PostgreSQL", r"\bpostgres(?:ql)?\b"),
    ("MySQL", r"\bmysql\b"), ("MongoDB", r"\bmongo(?:db)?\b"),
    ("Redis", r"\bredis\b"), ("Elasticsearch", r"\belastic(?:search)?\b"),
    ("Kafka", r"\bkafka\b"), ("RabbitMQ", r"\brabbitmq\b"),
    ("Spark", r"(?-i:\bSpark\b)|\bpyspark\b"), ("Airflow", r"\bairflow\b"),
    ("Snowflake", r"\bsnowflake\b"), ("BigQuery", r"\bbigquery\b"),
    ("AWS", r"\baws\b|\bamazon web services\b"),
    ("GCP", r"\bgcp\b|\bgoogle cloud\b"), ("Azure", r"\bazure\b"),
    ("Docker", r"\bdocker\b"), ("Kubernetes", r"\bkubernetes\b|\bk8s\b"),
    ("Terraform", r"\bterraform\b"), ("Linux", r"\blinux\b"),
    ("Microservices", r"\bmicro-?services?\b"),
    ("LLMs", r"\bllms?\b|\blarge language models?\b"),
    ("RAG", r"(?-i:\bRAG\b)"), ("LangChain", r"\blangchain\b"),
    ("PyTorch", r"\bpytorch\b"), ("TensorFlow", r"\btensorflow\b"),
    ("OpenAI", r"\bopenai\b"),
]
_TECH_RE = [(label, re.compile(p, re.I)) for label, p in _TECH]

# Plain substrings that must appear (lowercased) before a pattern can match.
# Checking these first skips most regex searches; it was ~10x faster than
# one combined alternation, which backtracks at every position.
_HINTS = {
    "Node.js": ("node",), "Next.js": ("next",), "NestJS": ("nest",),
    "Kubernetes": ("kubernetes", "k8s"), "AWS": ("aws", "amazon web"),
    "GCP": ("gcp", "google cloud"), "LLMs": ("llm", "large language"),
    "Microservices": ("micro",), "PostgreSQL": ("postgres",),
    "MongoDB": ("mongo",), "Elasticsearch": ("elastic",),
    "Spark": ("spark",), "React": ("react",), "Vue": ("vue",),
    "Go": ("go",),
}
_TECH_CHECK = [(label, _HINTS.get(label, (label.lower(),)), pat)
               for label, pat in _TECH_RE]


def tech_stack(text: str) -> list[str]:
    """Technologies the text names, in order of first mention."""
    text = text or ""
    low = text.lower()
    hits = []
    for label, hints, pat in _TECH_CHECK:
        if not any(h in low for h in hints):
            continue
        m = pat.search(text)
        if m:
            hits.append((m.start(), label))
    return [label for _, label in sorted(hits)]
