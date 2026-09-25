"""Text helpers shared by ingestion (indexing) and retrieval (querying)."""

import re

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_QUALIFIED = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:(?:\.|::)[A-Za-z_][A-Za-z0-9_]*)*")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")

STOPWORDS = frozenset(
    """a an and are as at be but by can could did do does for from had has have how i if in
    into is it its me my of on or our should so that the their them then there these this
    those to was we were what when where which who whom why will with would you your
    code function file files repo repository used use uses using does doing exist exists
    introduced introduce change changed changes please explain tell show way work works
    implemented implement written write happen happens affect affected""".split()
)


def split_identifier(ident: str) -> list[str]:
    """`getUserById` -> [get, user, by, id]; `MAX_RETRY_COUNT` -> [max, retry, count]."""
    parts: list[str] = []
    for piece in ident.split("_"):
        parts.extend(m.group(0).lower() for m in _CAMEL.finditer(piece))
    return [p for p in parts if p]


def expand_identifiers(text: str) -> str:
    """Append split forms of compound identifiers so lexical search can match
    `redis` inside `RedisCacheClient` or `cache` inside `cache_ttl`."""
    extra: list[str] = []
    seen: set[str] = set()
    for m in _IDENT.finditer(text):
        ident = m.group(0)
        if ident in seen:
            continue
        seen.add(ident)
        pieces = split_identifier(ident)
        if len(pieces) > 1:
            extra.extend(pieces)
    return text if not extra else f"{text}\n{' '.join(extra)}"


def query_identifiers(question: str) -> list[str]:
    """Whole identifiers mentioned in a question (candidate symbol names),
    keeping qualified forms like `Job.fetch` intact."""
    out: list[str] = []
    for ident in _QUALIFIED.findall(question):
        low = ident.lower().replace("::", ".")
        if len(low) > 2 and low not in STOPWORDS and low not in out:
            out.append(low)
    return out


def query_terms(question: str, limit: int = 24) -> list[str]:
    """Keywords for lexical search: identifiers kept whole *and* split,
    stopwords dropped, only [a-z0-9_] so they are safe inside to_tsquery."""
    terms: list[str] = []
    for m in _IDENT.finditer(question):
        ident = m.group(0)
        candidates = [ident.lower(), *split_identifier(ident)]
        for t in candidates:
            if len(t) < 2 or t in STOPWORDS or t in terms or not any(ch.isalnum() for ch in t):
                continue
            terms.append(t)
    return terms[:limit]


_SUFFIXES = ("ations", "ation", "ings", "ing", "ers", "ies", "er", "es", "ed", "s")


def word_stem(term: str) -> str:
    """Strip one common English suffix, keeping at least four characters."""
    for suffix in _SUFFIXES:
        if term.endswith(suffix) and len(term) - len(suffix) >= 4:
            return term[: -len(suffix)]
    return term


def lexical_query(terms: list[str]) -> str:
    """A to_tsquery OR-query where each term also matches other word forms and
    longer identifiers: "escaping" -> escap:* (escape, escaped), "speed" ->
    speed:* (speedups). Short terms stay exact to avoid matching everything."""
    parts: list[str] = []
    for t in terms:
        root = word_stem(t)
        part = f"{root}:*" if len(root) >= 4 else t
        if part not in parts:
            parts.append(part)
    return " | ".join(parts)
