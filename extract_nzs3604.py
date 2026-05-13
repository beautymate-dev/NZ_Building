"""
Extracts NZS 3604 (Timber Framed Buildings) PDF into searchable JSON chunks
for the Telegram bot to use as its primary knowledge source.

Run once locally:  python extract_nzs3604.py
Output:            nzs3604_chunks.json  (commit this, not the PDF)
"""

import sys
import subprocess
import importlib.util
import json
import re
from pathlib import Path


def ensure(pkg, import_as=None):
    name = import_as or pkg
    if importlib.util.find_spec(name) is None:
        print(f"  Installing {pkg}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "-q"])


ensure("pypdf")

from pypdf import PdfReader  # noqa: E402

PDF_PATH = Path(__file__).parent / "nzs-3604.pdf"
OUT_PATH = Path(__file__).parent / "nzs3604_chunks.json"

PAGES_PER_CHUNK = 4  # ~800–1,200 tokens per chunk; tune if needed


# ── Helpers ───────────────────────────────────────────────────────────────────

def clean(text: str) -> str:
    """Normalise whitespace."""
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]{2,}', ' ', text)
    return text.strip()


def detect_heading(text: str) -> str | None:
    """
    Return the first plausible section heading found in the first 10 lines,
    or None if nothing recognisable is found.
    Matches patterns like:
      "3.4  Wall Framing"
      "SECTION 5  FLOORS"
      "8  CONNECTIONS"
    """
    for line in text.split('\n')[:10]:
        line = line.strip()
        if not line:
            continue
        # Numbered clause: "3.4 Something" or "3 SOMETHING"
        if re.match(r'^\d+(\.\d+){0,2}\s+\w', line) and len(line) < 100:
            return line
        # Explicit section keyword
        if re.match(r'^(SECTION|Section)\s+\d+', line) and len(line) < 100:
            return line
    return None


# ── Main extraction ───────────────────────────────────────────────────────────

def extract_chunks(pdf_path: Path) -> list[dict]:
    reader = PdfReader(str(pdf_path))
    total = len(reader.pages)
    print(f"  Pages: {total}")

    # 1. Extract text from every page
    pages: list[str] = []
    for i, page in enumerate(reader.pages):
        pages.append(clean(page.extract_text() or ""))
        if (i + 1) % 50 == 0:
            print(f"  Extracted {i + 1}/{total} pages…")

    # 2. Group into fixed-size chunks
    chunks: list[dict] = []
    for start in range(0, total, PAGES_PER_CHUNK):
        end = min(start + PAGES_PER_CHUNK, total)
        content = "\n\n".join(pages[start:end])
        heading = detect_heading(content) or f"Pages {start + 1}–{end}"
        chunks.append({
            "section": heading,
            "pages": f"{start + 1}–{end}",
            "content": content,
        })

    return chunks


if __name__ == "__main__":
    if not PDF_PATH.exists():
        print(f"ERROR: {PDF_PATH} not found.")
        print("Place nzs-3604.pdf in the same directory as this script.")
        sys.exit(1)

    print(f"Extracting {PDF_PATH.name}…")
    chunks = extract_chunks(PDF_PATH)

    OUT_PATH.write_text(
        json.dumps(chunks, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    total_chars = sum(len(c["content"]) for c in chunks)
    avg = total_chars // len(chunks) if chunks else 0
    print(f"\nDone: {len(chunks)} chunks  |  {total_chars:,} chars total  |  ~{avg:,} chars/chunk")
    print(f"Written to {OUT_PATH}")
    print("\nNext: commit nzs3604_chunks.json and push to GitHub.")
