#!/usr/bin/env python3
"""Read the course materials sitting in the workspace and turn them into citable notes.

A course video may ship with slides, handouts or a textbook excerpt. Read these after
video understanding, keeping their sources separate. Current Codex reads rendered PDF
pages and extracted images; this script prepares files and does not understand them.

    python materials.py                          # auto-discover materials in the workspace
    python materials.py 课件/ 讲义.pdf -o skills/qm/references/课件笔记
    python materials.py 扫描件.pdf --cloud-ocr    # scanned PDF -> MinerU cloud (uploads it)

Text extraction and PDF rendering run locally. Use --render-pages for scanned or visual
pages, then open the images in current Codex. Optional --cloud-ocr sends scanned PDFs
to MinerU (needs MINERU_API_TOKEN and explicit authorization).
"""

import argparse
import html
import posixpath
import xml.etree.ElementTree as ET
import json
import os
import re
import sys
import time
import zipfile
from pathlib import Path

from common import fingerprint, read_json, write_json
from bili_fetch import safe_name, parse_pages

MATERIAL_EXTS = {".pdf", ".pptx", ".docx", ".md", ".txt", ".markdown"}
# Directories people actually dump course files into; checked before scanning the root.
CONVENTION_DIRS = ("课件", "courseware", "materials", "讲义", "slides", "ppt", "资料")

# A PDF page with less than this many characters is treated as an image, which is how
# scanned handouts announce themselves. 40 is comfortably below a real slide's text.
MIN_CHARS_PER_PAGE = 40
MINERU_BASE = os.getenv("MINERU_API_BASE", "https://mineru.net/api/v4")
MINERU_TOKEN = os.getenv("MINERU_API_TOKEN", "")


# --------------------------------------------------------------------------- parsing

def pptx_slides(archive: zipfile.ZipFile) -> list[str]:
    """Presentation order is independent of slide file numbering after reordering."""
    names = archive.namelist()
    if 'ppt/presentation.xml' not in names or 'ppt/_rels/presentation.xml.rels' not in names:
        return sorted((n for n in names if re.fullmatch(r'ppt/slides/slide\d+\.xml', n)),
                      key=lambda n: int(re.search(r'(\d+)', Path(n).stem).group(1)))
    relationships = {r.attrib['Id']: r.attrib['Target'] for r in ET.fromstring(archive.read('ppt/_rels/presentation.xml.rels'))}
    slides = []
    for element in ET.fromstring(archive.read('ppt/presentation.xml')).iter():
        if element.tag.rsplit('}', 1)[-1] != 'sldId':
            continue
        rid = next((value for key, value in element.attrib.items() if key.endswith('}id')), None)
        target = relationships.get(rid, '')
        name = target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join('ppt', target))
        if name not in names or not re.fullmatch(r'ppt/slides/slide\d+\.xml', name):
            raise ValueError('PPT presentation refers to a missing slide')
        slides.append(name)
    return slides


def text_from_pptx(path: Path) -> list[tuple[str, str]]:
    """Return citable text in the actual presentation order."""
    out = []
    with zipfile.ZipFile(path) as zf:
        for number, name in enumerate(pptx_slides(zf), 1):
            xml = zf.read(name).decode("utf-8", errors="ignore")
            # ponytail: stdlib unzip + <a:t> instead of python-pptx -- we only need the
            # text. Whole-slide rendering remains a separate image-tool operation.
            parts = [element.text or '' for element in ET.fromstring(xml).iter() if element.tag.rsplit('}', 1)[-1] == 't']
            text = " ".join(p.strip() for p in parts if p.strip())
            if text:
                out.append((f"第{number}页（幻灯片）", text))
    return out


def text_from_docx(path: Path) -> list[tuple[str, str]]:
    """Return [(paragraph_label, text)] grouped per chunk of the document body."""
    with zipfile.ZipFile(path) as zf:
        xml = zf.read("word/document.xml").decode("utf-8", errors="ignore")
    paragraphs = re.findall(r"<w:p[ >].*?</w:p>", xml, re.S)
    lines = []
    for number, para in enumerate(paragraphs, 1):
        runs = re.findall(r"<w:t[^>]*>(.*?)</w:t>", para, re.S)
        text = html.unescape("".join(runs)).strip()
        if text:
            lines.append((f"第{number}段", text))
    return lines


def text_from_pdf(path: Path, page_spec: str | None = None) -> tuple[list[tuple[str, str]], int, int]:
    """Return ([(第N页, text)], page_count, scanned_page_count)."""
    try:
        from pypdf import PdfReader
    except ImportError:
        raise SystemExit(
            "[error] reading PDFs needs pypdf:  pip install pypdf\n"
            "        (MD/TXT/PPTX/DOCX need nothing extra)"
        )
    reader = PdfReader(str(path))
    requested = parse_pages(page_spec) if page_spec else set(range(1, len(reader.pages) + 1))
    if not requested or min(requested) < 1 or max(requested) > len(reader.pages):
        raise ValueError('PDF page selection outside document')
    pages, scanned = [], 0
    for i, page in enumerate(reader.pages, 1):
        if i not in requested:
            continue
        try:
            text = (page.extract_text() or "").strip()
        except Exception:
            text = ""
        if len(text) < MIN_CHARS_PER_PAGE:
            scanned += 1
            continue
        pages.append((f"第{i}页", " ".join(text.split())))
    return pages, len(requested), scanned


def textbook_chapters(path: Path) -> tuple[list[dict], str]:
    """Extract conservative chapter ranges; book page numbers stay unknown unless mapped."""
    try:
        from pypdf import PdfReader
    except ImportError as error:
        raise RuntimeError("textbook mode needs pypdf") from error
    reader = PdfReader(str(path)); chapters: list[dict] = []
    try:
        outline = reader.outline
    except Exception:
        outline = []

    def flatten(items, level=1):
        for item in items:
            if isinstance(item, list):
                yield from flatten(item, level + 1)
            else:
                try:
                    page = reader.get_destination_page_number(item) + 1
                    title = str(getattr(item, "title", item)).strip()
                    if title and 1 <= page <= len(reader.pages):
                        yield title, page, level
                except Exception:
                    continue
    for title, page, level in flatten(outline):
        if title:
            chapters.append({"id": f"C{len(chapters) + 1:03d}", "title": title, "pdf_start": page, "confidence": "high", "source": "outline", "level": level})
    if chapters:
        chapters.sort(key=lambda c: (c["pdf_start"], c["level"]))
        for index, chapter in enumerate(chapters):
            next_start = next((c["pdf_start"] for c in chapters[index + 1:] if c.get("level", 1) <= chapter.get("level", 1) and c["pdf_start"] > chapter["pdf_start"]), len(reader.pages) + 1)
            chapter["pdf_end"] = next_start - 1
        return chapters, "outline"

    # ponytail: one page scan, no OCR; headings become reviewable low-confidence candidates.
    heading = re.compile(r"^(第\s*[一二三四五六七八九十百\d]+\s*[章节]|\d+(?:\.\d+)*\s+[^。]{2,80}|Chapter\s+\d+)", re.I)
    for page_no, page in enumerate(reader.pages, 1):
        text = " ".join((page.extract_text() or "").split())
        match = heading.search(text)
        if match:
            title = match.group(1).strip()
            chapters.append({"id": f"C{len(chapters) + 1:03d}", "title": title, "pdf_start": page_no, "confidence": "low", "source": "title-heuristic"})
    for index, chapter in enumerate(chapters):
        chapter["pdf_end"] = (chapters[index + 1]["pdf_start"] - 1) if index + 1 < len(chapters) else len(reader.pages)
    return chapters, "title-heuristic" if chapters else "none"


def select_chapters(chapters: list[dict], spec: str | None) -> set[str]:
    if not spec:
        return set()
    selected: set[str] = set()
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if "-" in chunk and chunk.replace("-", "").isdigit():
            a, b = map(int, chunk.split("-", 1))
            lo, hi = min(a, b), max(a, b)
            if lo < 1 or hi > len(chapters):
                raise ValueError(f"chapter range out of bounds: {chunk}")
            selected.update(chapters[i - 1]["id"] for i in range(lo, hi + 1))
        elif chunk.isdigit() and 1 <= int(chunk) <= len(chapters):
            selected.add(chapters[int(chunk) - 1]["id"])
        elif chunk.isdigit():
            raise ValueError(f"chapter number out of bounds: {chunk}")
        elif chunk in {chapter["id"] for chapter in chapters}:
            selected.add(chunk)
        else:
            raise ValueError(f"unknown chapter selector: {chunk}")
    return selected


def cmd_textbook(args) -> int:
    path = Path(args.pdf); chapters, method = textbook_chapters(path)
    try:
        selected = select_chapters(chapters, args.select)
    except ValueError as error:
        print(f"[error] {error}", file=sys.stderr)
        return 2
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    page_map = {}
    if args.book_page_offset is not None:
        page_map = {str(i): str(i + args.book_page_offset) for i in range(1, len(reader.pages) + 1) if i + args.book_page_offset > 0}
    elif args.confirm_page_labels and reader.trailer["/Root"].get("/PageLabels"):
        page_map = {str(i): label for i, label in enumerate(reader.page_labels, 1)}
    for chapter in chapters:
        chapter["selected"] = chapter["id"] in selected
        chapter["book_pages"] = [page_map.get(str(chapter["pdf_start"])), page_map.get(str(chapter["pdf_end"]))] if page_map else None
        chapter["pdf_pages"] = [chapter["pdf_start"], chapter["pdf_end"]]
        chapter["page_status"] = "confirmed" if page_map else "待确认"
    manifest = {"schema_version": 1, "source": str(path), "parse_method": method, "chapters": chapters, "book_page_map": page_map,
                "excluded_chapters": [c["id"] for c in chapters if not c["selected"]]}
    output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[textbook] {len(chapters)} chapters ({method}) -> {output}")
    for chapter in chapters:
        mark = "[selected]" if chapter["selected"] else "[excluded/pending]"
        print(f"{mark} {chapter['id']} {chapter['title']} PDF {chapter['pdf_start']}-{chapter['pdf_end']} ({chapter['confidence']})")
    if not args.select:
        print("[action] choose chapters with --select, for example --select 1,2,5-7; no chapter was selected")
    return 0


def read_material(path: Path) -> list[tuple[str, str]]:
    suffix = path.suffix.lower()
    if suffix in (".md", ".txt", ".markdown"):
        return [("全文", " ".join(path.read_text(encoding="utf-8", errors="ignore").split()))]
    if suffix == ".pdf":
        pages, _, _ = text_from_pdf(path)
        return pages
    if suffix == ".pptx":
        return text_from_pptx(path)
    if suffix == ".docx":
        return text_from_docx(path)
    raise ValueError(f"unsupported: {path.name}")


# --------------------------------------------------------------------------- discovery

def discover(explicit: list[str]) -> list[Path]:
    found: dict[Path, None] = {}
    if explicit:
        for raw in explicit:
            p = Path(raw).expanduser()
            if p.is_dir():
                for item in sorted(p.rglob("*")):
                    if item.suffix.lower() in MATERIAL_EXTS and item.is_file():
                        found[item] = None
            elif p.is_file():
                found[p] = None
            else:
                print(f"[warn] no such file or directory: {raw}", file=sys.stderr)
        return list(found)
    for name in CONVENTION_DIRS:
        d = Path(name)
        if d.is_dir():
            for item in sorted(d.rglob("*")):
                if item.suffix.lower() in MATERIAL_EXTS and item.is_file():
                    found[item] = None
    for item in sorted(Path(".").iterdir()):
        if item.is_file() and item.suffix.lower() in MATERIAL_EXTS and item.name.lower() not in ("readme.md", "requirements.txt", "license", "notice"):
            found[item] = None
    return list(found)


def lesson_number(path: Path) -> int | None:
    m = re.search(r"(\d{1,3})", path.stem)
    return int(m.group(1)) if m else None


# --------------------------------------------------------------------------- cloud OCR

def mineru_parse(paths: list[Path], workdir: Path, model_version: str, language: str) -> dict[str, list[tuple[str, str]]]:
    """Send scanned PDFs to mineru.net and bring back Markdown. Only called with --cloud-ocr."""
    try:
        import requests
    except ImportError as error:
        raise RuntimeError("--cloud-ocr needs requests; install requirements.txt first") from error

    if not MINERU_TOKEN:
        raise SystemExit(
            "[error] --cloud-ocr needs a MinerU token (free): https://mineru.net\n"
            "        export MINERU_API_TOKEN=..."
        )
    headers = {"Authorization": f"Bearer {MINERU_TOKEN}", "Content-Type": "application/json"}

    def safe_id(p: Path) -> str:
        return (re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", p.stem).strip("_") or "doc")[:100]

    payload = {
        "enable_formula": True,
        "enable_table": True,
        "language": language,
        "model_version": model_version,
        "files": [{"name": f"{safe_id(p)}.pdf", "is_ocr": True, "data_id": safe_id(p)} for p in paths],
    }
    resp = requests.post(f"{MINERU_BASE.rstrip('/')}/file-urls/batch", headers=headers,
                         data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), timeout=60)
    resp.raise_for_status()
    body = resp.json()
    if body.get("code") != 0:
        raise SystemExit(f"[error] MinerU refused the upload request: {body}")
    data = body["data"]
    urls = data.get("file_urls") or []
    if len(urls) != len(paths):
        raise SystemExit(f"[error] MinerU returned {len(urls)} upload URLs for {len(paths)} files")
    for src, item in zip(paths, urls):
        print(f"  uploading {src.name} (this leaves your machine)")
        with src.open("rb") as fh:
            up = requests.put(item["upload_url"], data=fh, timeout=600)
        up.raise_for_status()

    print("  waiting for MinerU to parse (scanned files take a while)")
    deadline = time.monotonic() + 1200
    while True:
        if time.monotonic() > deadline:
            raise RuntimeError("MinerU polling timed out after 20 minutes")
        r = requests.get(f"{MINERU_BASE.rstrip('/')}/extract-results/batch/{data['batch_id']}",
                         headers=headers, timeout=60)
        r.raise_for_status()
        results = r.json().get("data", {}).get("extract_result", [])
        if results and all(i.get("state") == "done" for i in results):
            break
        failed = [i for i in results if i.get("state") == "failed"]
        if failed:
            raise SystemExit(f"[error] MinerU failed on: {[i.get('data_id') for i in failed]}")
        time.sleep(20)

    out: dict[str, list[tuple[str, str]]] = {}
    for item in results:
        data_id = item["data_id"]
        target = workdir / "mineru" / data_id
        target.mkdir(parents=True, exist_ok=True)
        zip_path = target / "full.zip"
        if not zip_path.exists():
            blob = requests.get(item["full_zip_url"], timeout=600)
            blob.raise_for_status()
            zip_path.write_bytes(blob.content)
        with zipfile.ZipFile(zip_path) as zf:
            for member in zf.infolist():
                resolved = (target / member.filename).resolve()
                if not resolved.is_relative_to(target.resolve()):
                    raise RuntimeError("OCR archive contains a path outside its output directory")
            zf.extractall(target)
        chunks: list[tuple[str, str]] = []
        for md in sorted(target.rglob("*.md")):
            text = " ".join(md.read_text(encoding="utf-8", errors="ignore").split())
            if text:
                chunks.append((f"{md.parent.name}/{md.stem}", text))
        out[data_id] = chunks
    return out


def render_pdf_pages(path: Path, pages: str, outdir: Path) -> dict:
    try:
        import pymupdf as fitz
    except ImportError as error:
        raise RuntimeError('PDF page images need PyMuPDF; run setup.ps1 or use Codex PDF rendering tools') from error
    requested = parse_pages(pages)
    with fitz.open(path) as document:
        if not requested or min(requested) < 1 or max(requested) > len(document):
            raise ValueError('PDF page selection outside document')
        outdir.mkdir(parents=True, exist_ok=True)
        rendered = []
        for number in sorted(requested):
            target = outdir / f'{safe_name(path.stem)}-{fingerprint(str(path.resolve()))[:8]}-page{number}.png'
            document[number - 1].get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).save(target)
            rendered.append({'pdf_page': number, 'image_path': str(target.resolve())})
    result = {'source': str(path.resolve()), 'pages': rendered, 'understood': False}
    write_json(outdir / 'pages_manifest.json', result)
    return result


def extract_pptx_images(path: Path, outdir: Path) -> list[dict]:
    images = []
    with zipfile.ZipFile(path) as archive:
        for slide, slide_name in enumerate(pptx_slides(archive), 1):
            name = f'ppt/slides/_rels/{Path(slide_name).name}.rels'
            if name not in archive.namelist():
                continue
            for relationship in ET.fromstring(archive.read(name)):
                if not relationship.attrib.get('Type', '').endswith('/image') or relationship.attrib.get('TargetMode') == 'External':
                    continue
                target = posixpath.normpath(posixpath.join('ppt/slides', relationship.attrib['Target']))
                if not target.startswith('ppt/media/') or target not in archive.namelist():
                    continue
                suffix = Path(target).suffix.lower()
                if suffix not in ('.png', '.jpg', '.jpeg', '.webp'):
                    continue
                output = outdir / f'{safe_name(path.stem)}-{fingerprint(str(path.resolve()))[:8]}-slide{slide}-{Path(target).name}'
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(archive.read(target))
                images.append({'slide': slide, 'image_path': str(output.resolve())})
    return images


# --------------------------------------------------------------------------- main

def main() -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", help="material files/dirs (default: auto-discover)")
    ap.add_argument("-o", "--outdir", default="课件笔记", help="where the .md notes go (default: 课件笔记)")
    ap.add_argument("--cloud-ocr", action="store_true",
                    help="send scanned PDFs to mineru.net for OCR (uploads the files; needs MINERU_API_TOKEN)")
    ap.add_argument("--model-version", default=os.getenv("MINERU_MODEL_VERSION", "vlm"))
    ap.add_argument("--language", default=os.getenv("MINERU_LANGUAGE", "ch"))
    ap.add_argument("--textbook", action="store_true", help="parse one PDF into a chapter manifest")
    ap.add_argument("--select", help="textbook chapter numbers or ranges; required before using supplements")
    ap.add_argument("--manifest-output", default="textbook_manifest.json")
    ap.add_argument("--book-page-offset", type=int, help="confirmed printed page = PDF physical page + offset")
    ap.add_argument("--confirm-page-labels", action="store_true", help="user confirmed PDF PageLabels match printed book pages")
    ap.add_argument('--pages', help='extract only selected physical PDF pages, e.g. 10-30,45-50; one PDF input')
    ap.set_defaults(render_pages=None, extract_images=False, image_dir=None)
    args = ap.parse_args()
    if args.pages and (len(args.paths) != 1 or Path(args.paths[0]).suffix.lower() != '.pdf' or args.textbook or args.render_pages):
        ap.error('--pages requires one PDF for text extraction; use --render-pages separately for images')
    if args.render_pages:
        if len(args.paths) != 1 or Path(args.paths[0]).suffix.lower() != ".pdf":
            ap.error("--render-pages requires one PDF")
        try:
            result = render_pdf_pages(Path(args.paths[0]), args.render_pages, args.image_dir)
            print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
        except (ValueError, RuntimeError, OSError) as error:
            print(f"[error] {error}", file=sys.stderr); return 1

    if args.textbook:
        if len(args.paths) != 1 or Path(args.paths[0]).suffix.lower() != ".pdf":
            ap.error("--textbook expects exactly one PDF path")
        args.pdf = args.paths[0]; args.output = args.manifest_output
        return cmd_textbook(args)

    files = discover(args.paths)
    if not files:
        print("[info] no course materials found. Pass paths explicitly, or drop them in one of:\n"
              f"       {', '.join(CONVENTION_DIRS[:4])}/")
        return 0

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    manifest: dict = {"sources": [], "scanned": [], "no_text": [], "errors": []}
    scanned_pdfs: list[Path] = []

    for path in files:
        print(f"[read] {path.name}")
        try:
            if path.suffix.lower() == ".pdf":
                chunks, pages_total, scanned_pages = text_from_pdf(path, args.pages)
                if scanned_pages and scanned_pages >= max(1, pages_total // 2):
                    manifest["scanned"].append({"file": str(path), "pages": pages_total,
                                                "scanned_pages": scanned_pages})
                    scanned_pdfs.append(path)
                    print(f"  [warn] looks scanned ({scanned_pages}/{pages_total} pages are images)")
                    continue
            else:
                chunks = read_material(path)
        except SystemExit as e:
            print(str(e), file=sys.stderr)
            manifest["errors"].append({"file": str(path), "error": "parser unavailable"})
            continue
        except Exception as e:
            print(f"  [warn] could not read: {e}", file=sys.stderr)
            manifest["errors"].append({"file": str(path), "error": str(e)})
            continue
        images = extract_pptx_images(path, args.image_dir) if args.extract_images and path.suffix.lower() == '.pptx' else []
        if not chunks and not images:
            manifest["no_text"].append(str(path))
            print("  [warn] no extractable text")
            continue
        body = [f"# {path.stem}", "", f"> 来源：`{path}`", ""]
        for label, text in chunks:
            body += [f"## {label}", "", text, ""]
        if images:
            body += ['## 待当前 Codex 实际读取的嵌入图片', '']
            body += [f"- 第{image['slide']}页（幻灯片）：{image['image_path']}" for image in images]
            body += ['']
        target = outdir / f"{safe_name(path.stem)}-{fingerprint(str(path.resolve()))[:8]}.md"
        target.write_text("\n".join(body).rstrip() + "\n", encoding="utf-8")
        manifest["sources"].append({
            "file": str(path.resolve()), "note": str(target.resolve()), "chunks": len(chunks),
            "pdf_pages": sorted(parse_pages(args.pages)) if args.pages else None,
            "images": images,
            "lesson": lesson_number(path), "chars": sum(len(t) for _, t in chunks),
        })
        print(f"  -> {target.name} ({len(chunks)} section(s))")

    if args.cloud_ocr and scanned_pdfs:
        print(f"\n[cloud] sending {len(scanned_pdfs)} scanned PDF(s) to MinerU")
        by_id = mineru_parse(scanned_pdfs, outdir, args.model_version, args.language)
        for path in scanned_pdfs:
            data_id = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", path.stem).strip("_")[:100] or "doc"
            chunks = by_id.get(data_id, [])
            if not chunks:
                print(f"  [warn] MinerU returned no matching result for {path.name}", file=sys.stderr)
                manifest["errors"].append({"file": str(path), "error": "no matching MinerU result"})
                continue
            body = [f"# {path.stem}（扫描件 OCR）", "", f"> 来源：`{path}`，由 MinerU 云端解析", ""]
            for label, text in chunks:
                body += [f"## {label}", "", text, ""]
            target = outdir / f"{path.stem}.md"
            target.write_text("\n".join(body).rstrip() + "\n", encoding="utf-8")
            manifest["sources"].append({
                "file": str(path), "note": str(target), "chunks": len(chunks),
                "lesson": lesson_number(path), "chars": sum(len(t) for _, t in chunks),
                "source": "mineru-cloud",
            })
            print(f"  -> {target.name} (OCR)")
    elif scanned_pdfs:
        print('\n[next] scanned PDF(s) need authorized OCR or BiliScribe-Vision')
        print('       optional --cloud-ocr uploads the file to MinerU when explicitly authorized')

    (outdir / "_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    total = sum(s["chars"] for s in manifest["sources"])
    print(f"\n[done] {len(manifest['sources'])} material(s), {total} chars -> {outdir}/_manifest.json")
    return 1 if manifest["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
