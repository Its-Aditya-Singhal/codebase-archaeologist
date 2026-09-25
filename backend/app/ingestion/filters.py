"""Decide which tracked files are worth indexing."""

from pathlib import PurePosixPath

SKIP_DIRS = {
    "node_modules", "vendor", "third_party", "dist", "build", "out", "target", ".next",
    ".nuxt", "__pycache__", ".venv", "venv", "coverage", ".idea", ".vscode", "bower_components",
    "Pods", ".gradle", ".terraform",
}
SKIP_NAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "uv.lock", "Cargo.lock",
    "Gemfile.lock", "composer.lock", "go.sum", "Pipfile.lock", "bun.lockb",
}
BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".bmp", ".tiff", ".psd", ".pdf", ".zip",
    ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".jar", ".war", ".class", ".so", ".dylib",
    ".dll", ".exe", ".bin", ".o", ".a", ".wasm", ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp3", ".mp4", ".mov", ".avi", ".webm", ".wav", ".ogg", ".flac", ".pyc", ".db", ".sqlite",
    ".parquet", ".pkl", ".npy", ".npz", ".onnx", ".pt", ".h5", ".ckpt", ".safetensors",
}
SKIP_SUFFIXES = (".min.js", ".min.css", ".map", ".snap", ".svg")


def should_index_path(path: str) -> bool:
    p = PurePosixPath(path)
    if any(part in SKIP_DIRS or part.startswith(".git") for part in p.parts[:-1]):
        return False
    if p.name in SKIP_NAMES:
        return False
    lower = p.name.lower()
    if p.suffix.lower() in BINARY_EXTENSIONS or lower.endswith(SKIP_SUFFIXES):
        return False
    return True


def decode_text(data: bytes) -> str | None:
    """Return text, or None for binary / generated-looking content."""
    if b"\0" in data:
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    lines = text.splitlines()
    # Minified / generated: very long average line length.
    if lines and len(text) / len(lines) > 400:
        return None
    return text
