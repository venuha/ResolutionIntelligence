"""Package only explicit public source assets; never credentials, sessions or databases."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

root = Path(__file__).resolve().parents[1]
out = root / "outputs"
out.mkdir(exist_ok=True)
paths = [root / name for name in ("README.md", "requirements.txt", "requirements.lock", "pyproject.toml", ".env.example", ".gitignore", "run.sh", "Dockerfile", ".dockerignore", "render.yaml")]
for folder in ("resolution", "scripts", "tests", "data", "docs"):
    paths.extend(p for p in (root / folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts
                 and p.suffix in (".py", ".html", ".css", ".js", ".json", ".md", ".jpg"))
target = out / "refund-resolution-source.zip"
with ZipFile(target, "w", ZIP_DEFLATED) as archive:
    for path in sorted(paths):
        archive.write(path, path.relative_to(root))
print(target)
