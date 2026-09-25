from app.ingestion.chunker import MAX_CHUNK_LINES, chunk_file
from app.ingestion.filters import should_index_path
from app.text import expand_identifiers, query_terms, split_identifier

PY = '''import redis

CACHE_TTL = 300
client = redis.Redis()


def get_user(user_id):
    """Fetch a user, cached in Redis."""
    cached = client.get(user_id)
    return cached


class Store:
    def save(self, key, value):
        client.set(key, value, ex=CACHE_TTL)
'''

TS = '''import { db } from "./db";

export const fetchOrder = async (id: string) => {
  return db.orders.find(id);
};

export class OrderService {
  cancel(id: string) {
    return db.orders.delete(id);
  }
}

export interface Order { id: string }
'''


def by_name(chunks):
    return {c.symbol_name: c for c in chunks if c.symbol_name}


def test_python_definitions_and_module_code():
    chunks = chunk_file("app/cache.py", PY, "python")
    names = by_name(chunks)
    assert names["get_user"].symbol_kind == "function"
    assert (names["get_user"].start_line, names["get_user"].end_line) == (7, 10)
    assert names["Store"].symbol_kind == "class"
    module = [c for c in chunks if c.symbol_kind == "module"]
    assert module and "CACHE_TTL = 300" in module[0].content


def test_typescript_exports_and_arrow_functions():
    names = by_name(chunk_file("src/orders.ts", TS, "typescript"))
    assert names["fetchOrder"].symbol_kind == "function"
    assert names["OrderService"].symbol_kind == "class"
    assert names["Order"].symbol_kind == "interface"
    assert names["fetchOrder"].content.startswith("export const fetchOrder")


def test_large_class_is_split_into_methods():
    methods = "\n".join(
        f"    def m{i}(self):\n" + "".join(f"        x = {j}\n" for j in range(20))
        for i in range(10)
    )
    src = f"class Big:\n    \"\"\"Doc.\"\"\"\n{methods}"
    chunks = chunk_file("big.py", src, "python")
    names = by_name(chunks)
    assert names["Big"].metadata.get("truncated")
    assert names["Big.m3"].symbol_kind == "method"
    assert all(c.end_line - c.start_line < MAX_CHUNK_LINES for c in chunks)


def test_markdown_sections():
    md = "# Title\nintro\n\n## Caching\nWe use Redis because...\n\n```\n# not a heading\n```\n"
    chunks = chunk_file("README.md", md, "markdown")
    assert [c.symbol_name for c in chunks] == ["Title", "Caching"]
    assert all(c.source_type == "doc" for c in chunks)


def test_unknown_language_falls_back_to_windows():
    text = "\n".join(f"line {i}" for i in range(150))
    chunks = chunk_file("data.txtx", text, None)
    assert chunks[0].start_line == 1 and chunks[-1].end_line == 150
    assert all(c.symbol_kind == "window" for c in chunks)


def test_identifier_helpers():
    assert split_identifier("getUserById") == ["get", "user", "by", "id"]
    assert split_identifier("HTTPServerError") == ["http", "server", "error"]
    assert "redis" in expand_identifiers("RedisCacheClient()")
    terms = query_terms("Why does getUserById use Redis?")
    assert "getuserbyid" in terms and "redis" in terms and "why" not in terms


def test_path_filters():
    assert should_index_path("src/app.py")
    assert not should_index_path("node_modules/x/index.js")
    assert not should_index_path("web/package-lock.json")
    assert not should_index_path("static/logo.png")


def test_query_identifiers_keep_qualified_names():
    from app.text import query_identifiers
    assert query_identifiers("Why is Job.fetch implemented this way?") == ["job.fetch"]


def test_lexical_query_matches_word_forms():
    from app.text import lexical_query

    q = lexical_query(["escaping", "speed", "extension", "api", "queues"])
    assert q == "escap:* | speed:* | extension:* | api | queu:*"


def test_plain_text_is_not_vim_help():
    from app.ingestion.chunker import detect_language

    assert detect_language("LICENSE.txt") is None
    assert detect_language("src/app.py") == "python"


def test_remove_clone_only_touches_app_made_clones(tmp_path, monkeypatch):
    from app import main

    repos_dir = tmp_path / "repos"
    clone = repos_dir / "o__r"
    checkout = tmp_path / "my-project"  # a user's own local checkout
    for d in (clone, checkout):
        d.mkdir(parents=True)
        (d / "file.py").write_text("x = 1\n")

    class S:
        pass
    S.repos_dir = repos_dir
    monkeypatch.setattr(main, "get_settings", lambda: S)
    main._remove_clone(checkout)
    main._remove_clone(repos_dir)
    main._remove_clone(clone)
    assert checkout.exists() and repos_dir.exists() and not clone.exists()
