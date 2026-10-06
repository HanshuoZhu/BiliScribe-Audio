#!/usr/bin/env python3
"""Bilibili metadata, manifest, subtitles and serial audio acquisition."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from common import confirmed_units, ffmpeg_executable, read_json, save_transcript

API = "https://api.bilibili.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36"
BEST_AUDIO = 30280
INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WBI_TABLE = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 57, 13, 55, 4, 11, 20, 6, 16, 26, 24, 22, 30, 34, 37, 40, 54, 1, 7, 17, 21, 48, 51, 56, 59, 60, 61, 62, 63, 44, 41, 25, 36, 52]


class DownloadError(RuntimeError):
    """A recoverable download or decode failure."""


def safe_name(value: str, limit: int = 96) -> str:
    value = INVALID.sub("_", str(value)).strip().rstrip(".")
    value = re.sub(r"\s+", " ", value)
    value = value[:limit].rstrip(" ._") or "untitled"
    if re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", value, re.I):
        value = "_" + value
    return value


def parse_bv(raw: str) -> str:
    match = re.search(r"BV[0-9A-Za-z]{10}", raw)
    if not match:
        raise ValueError(f"no BV id found in {raw!r}")
    return match.group(0)


def parse_bvs(values: list[str]) -> list[str]:
    found: list[str] = []
    for value in values:
        for bv in re.findall(r"BV[0-9A-Za-z]{10}", value):
            if bv not in found:
                found.append(bv)
    if not found:
        raise ValueError("no BV ids found")
    return found


def parse_range(spec: str | None, parts: list[dict]) -> list[dict]:
    if not spec or spec.lower() == "all":
        return parts
    wanted: set[int] = set()
    for chunk in spec.split(","):
        if "-" in chunk:
            a, b = (int(x.strip()) for x in chunk.split("-", 1))
            wanted.update(range(min(a, b), max(a, b) + 1))
        else:
            wanted.add(int(chunk.strip()))
    selected = [p for p in parts if int(p.get("page", 0)) in wanted]
    if not selected:
        raise ValueError(f"selection {spec!r} matched no pages")
    return selected


def _curl(url: str, headers: dict[str, str], timeout: int) -> bytes:
    command = ["curl", "-sS", "--max-time", str(timeout), "--compressed"]
    for key, value in headers.items():
        command += ["-H", f"{key}: {value}"]
    command.append(url)
    proc = subprocess.run(command, capture_output=True)
    if proc.returncode:
        raise DownloadError(proc.stderr.decode("utf-8", "replace") or f"curl exited {proc.returncode}")
    return proc.stdout


def _get(url: str, headers: dict[str, str], timeout: int = 30) -> bytes:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        if error.code == 412 and shutil.which("curl"):
            return _curl(url, headers, timeout)
        raise DownloadError(f"HTTP {error.code} for {url}") from error
    except urllib.error.URLError as error:
        raise DownloadError(f"cannot reach {url}: {error.reason}") from error


def api(path: str, params: dict, cookie: str | None = None) -> dict:
    headers = {"User-Agent": UA, "Referer": "https://www.bilibili.com"}
    if cookie:
        headers["Cookie"] = cookie
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    for attempt in range(3):
        try:
            body = json.loads(_get(url, headers).decode("utf-8"))
            if body.get("code") != 0:
                raise DownloadError(f"{path}: code {body.get('code')}: {body.get('message')}")
            return body.get("data") or {}
        except (DownloadError, json.JSONDecodeError) as error:
            if attempt == 2:
                raise DownloadError(f"{path}: {error}") from error
            time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def fetch_video(bv: str, cookie: str | None = None) -> tuple[str, list[dict]]:
    data = api("/x/web-interface/view", {"bvid": bv}, cookie)
    pages = []
    for page in data.get("pages") or []:
        pages.append({"page": int(page.get("page", len(pages) + 1)), "cid": int(page["cid"]),
                      "part": page.get("part") or f"P{len(pages) + 1}", "duration": int(page.get("duration") or 0)})
    return data.get("title") or bv, pages


def stable_stem(unit: dict) -> str:
    return f"{unit['lesson_id']}_{unit['bvid']}_cid{unit['cid']}_{safe_name(unit.get('title') or 'untitled')}"


def build_manifest(inputs: list[str], cookie: str | None = None, source_type: str = "manual",
                   delay: float = 1.0) -> dict:
    bvs = parse_bvs(inputs); units: list[dict] = []; source_order = 0; titles = []
    for bv in bvs:
        title, pages = fetch_video(bv, cookie); titles.append(title)
        for page in pages:
            source_order += 1
            units.append({"lesson_id": f"L{source_order:03d}", "bvid": bv, "cid": page["cid"],
                          "page": page["page"], "title": page["part"], "duration": page["duration"],
                          "source_type": source_type, "source_order": source_order, "study_order": source_order,
                          "selection": "pending", "added_by": "auto"})
        if delay: time.sleep(delay)
    course_id = safe_name("-".join(titles), 64).lower().replace(" ", "-")
    return {"schema_version": 1, "course_id": course_id,
            "title": titles[0] if len(titles) == 1 else "多 BV 课程", "units": units}


def archive_records(raw: str, cookie: str | None = None) -> tuple[str, list[dict]]:
    """Resolve a Bilibili season/series URL to ordered archive records."""
    parsed = urllib.parse.urlparse(raw)
    query = urllib.parse.parse_qs(parsed.query)
    path_match = re.search(r"/lists/(\d+)", parsed.path)
    season_id = (query.get("season_id") or query.get("sid") or (path_match.groups() if path_match and query.get("type", [""])[0] == "season" else [None]))[0]
    series_id = (query.get("series_id") or (path_match.groups() if path_match and query.get("type", [""])[0] == "series" else [None]))[0]
    mid_match = re.search(r"space\.bilibili\.com/(\d+)", raw)
    mid = (query.get("mid") or (mid_match.groups() if mid_match else [None]))[0]
    if season_id and mid:
        endpoint, params, page_key = "/x/polymer/web-space/seasons_archives_list", {"mid": mid, "season_id": season_id, "sort_reverse": "false", "page_size": 100}, "page_num"
        source = "collection"
    elif series_id and mid:
        endpoint, params, page_key = "/x/series/archives", {"mid": mid, "series_id": series_id, "ps": 100}, "pn"
        source = "series"
    else:
        raise ValueError("collection/series URL needs space.bilibili.com/<mid> and season_id/series_id")
    records, seen = [], set()
    for page_number in range(1, 101):
        data = api(endpoint, {**params, page_key: page_number}, cookie)
        archives = data.get('archives') or data.get('list') or data.get('items') or data.get('archives_list') or []
        if isinstance(archives, dict):
            archives = archives.get('archives') or archives.get('list') or []
        count = len(records)
        for item in archives:
            bv = item.get('bvid') or item.get('bvid_str') or item.get('bv_id')
            if not bv or bv in seen:
                continue
            seen.add(bv)
            records.append({'bvid': bv, 'title': item.get('title') or item.get('name') or ''})
        total = int((data.get('page') or {}).get('total') or (data.get('page') or {}).get('count') or 0)
        if not archives or (total and len(records) >= total) or len(archives) < 100:
            break
        if len(records) == count:
            raise DownloadError('collection pagination repeated a page; coverage cannot be established')
        time.sleep(1)
    else:
        raise DownloadError('collection discovery exceeds the 100-page bound; narrow the scope')
    if not records:
        raise ValueError(f'{source} response contained no video archives')
    return source, records


def up_records(uid: str, keyword: str | None, cookie: str | None = None) -> list[dict]:
    """Discover UP uploads with WBI signing and bounded pagination; results remain pending."""
    nav = api("/x/web-interface/nav", {}, cookie)
    images = (nav.get("wbi_img") or {})
    img_url, sub_url = images.get("img_url"), images.get("sub_url")
    if not img_url or not sub_url:
        raise DownloadError("Bilibili did not return WBI image keys")
    img_key = urllib.parse.urlparse(img_url).path.rsplit("/", 1)[-1].split(".")[0]
    sub_key = urllib.parse.urlparse(sub_url).path.rsplit("/", 1)[-1].split(".")[0]
    mixin = "".join((img_key + sub_key)[index] for index in WBI_TABLE)[:32]
    records: list[dict] = []
    for page_number in range(1, 11):
        params = {"mid": uid, "keyword": keyword or "", "pn": page_number, "ps": 50, "order": "pubdate", "platform": "web", "web_location": 1550101, "wts": int(time.time())}
        query = urllib.parse.urlencode(sorted(params.items()))
        params["w_rid"] = hashlib.md5((query + mixin).encode()).hexdigest()
        data = api("/x/space/wbi/arc/search", params, cookie)
        page = data.get("list") or {}
        items = page.get("vlist") or page.get("tlist") or []
        if not items: break
        records.extend({"bvid": item.get("bvid"), "title": re.sub(r"<[^>]+>", "", item.get("title", "")), "duration": item.get("length", ""), "pubdate": item.get("created")} for item in items if item.get("bvid"))
        total = int((data.get("page") or {}).get("count") or 0)
        if (total and len(records) >= total) or len(items) < 50: break
        time.sleep(1.0)
    return records


def manifest_from_records(records: list[dict], title: str, source_type: str, cookie: str | None = None) -> dict:
    units, seen = [], set()
    for record in records:
        bv = record['bvid']
        _, pages = fetch_video(bv, cookie)
        for page in pages:
            if (bv, page['cid']) in seen:
                continue
            seen.add((bv, page['cid']))
            index = len(units) + 1
            units.append({'lesson_id': f'L{index:03d}', 'bvid': bv, 'cid': page['cid'], 'page': page['page'],
                          'title': page['part'], 'duration': page['duration'],
                          'source_type': record.get('source_type', source_type), 'source_order': index,
                          'study_order': index, 'selection': 'pending', 'added_by': 'auto',
                          **({'pubdate': record['pubdate']} if record.get('pubdate') else {})})
        time.sleep(1)
    return {'schema_version': 1, 'course_id': safe_name(title or 'bilibili-course', 64).lower().replace(' ', '-'),
            'title': title or 'Bilibili course', 'units': units}

def expected_bytes(play: dict, duration: int) -> tuple[list[str], int]:
    streams = list((play.get("dash") or {}).get("audio") or [])
    if streams:
        streams.sort(key=lambda stream: (stream.get("id") != BEST_AUDIO, -stream.get("bandwidth", 0)))
        stream = streams[0]
        return [stream.get('baseUrl') or stream['base_url'], *(stream.get('backupUrl') or stream.get('backup_url') or [])], int(stream.get("bandwidth", 0) / 8 * duration)
    durl = play.get("durl") or []
    if not durl: raise DownloadError("no audio stream returned")
    if len(durl) != 1:
        raise DownloadError('segmented legacy audio/video is unsupported; provide local media for transcription')
    return [durl[0]["url"], *(durl[0].get("backup_url") or [])], 0


def decode_errors(path: Path) -> int | None:
    ffmpeg = ffmpeg_executable()
    if not ffmpeg: return None
    result = subprocess.run([ffmpeg, "-v", "error", "-i", str(path), "-f", "null", "-"], capture_output=True, text=True, encoding='utf-8', errors='replace')
    return max(int(result.returncode != 0), sum(bool(line.strip()) for line in result.stderr.splitlines()))




def download(urls: list[str], target: Path, cookie: str | None, expected: int = 0,
             retries: int = 2, verify: bool = True) -> None:
    headers = {"User-Agent": UA, "Referer": "https://www.bilibili.com"}
    if cookie: headers["Cookie"] = cookie
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(target.suffix + ".part"); failures: list[str] = []
    for host, url in enumerate(urls, 1):
        for attempt in range(retries):
            part.unlink(missing_ok=True)
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=90) as response, part.open("wb") as handle:
                    total = int(response.headers.get("Content-Length") or 0)
                    while chunk := response.read(1 << 20): handle.write(chunk)
                if not part.stat().st_size or total and part.stat().st_size != total: raise DownloadError("short response")
                if verify:
                    errors = decode_errors(part)
                    if errors is None: raise DownloadError("ffmpeg unavailable; pass --allow-weak-verify")
                    if errors: raise DownloadError(f"{errors} ffmpeg decode error(s)")
                part.replace(target); return
            except Exception as error:
                failures.append(f"host{host}/try{attempt + 1}: {error}"); time.sleep(1)
    part.unlink(missing_ok=True); raise DownloadError("; ".join(failures[:4]))


def cmd_list(args) -> int:
    bv = parse_bv(args.input); title, pages = fetch_video(bv, args.cookie)
    print(f"{title} ({bv}, {len(pages)} 分P)")
    for page in pages: print(f"{page['page']:>3} {page['duration'] // 60:>3}:{page['duration'] % 60:02d} {page['cid']:>12} {page['part']}")
    return 0


def parse_pages(spec: str) -> set[int]:
    pages: set[int] = set()
    for chunk in spec.split(","):
        if "-" in chunk:
            a, b = map(int, chunk.split("-", 1)); pages.update(range(min(a, b), max(a, b) + 1))
        else: pages.add(int(chunk))
    return pages


def cmd_manifest(args) -> int:
    manifest = build_manifest(args.inputs, args.cookie, args.source_type, args.delay)
    chosen = None if args.include in ("all", "pending") else parse_pages(args.include)
    for unit in manifest["units"]:
        unit["selection"] = "pending" if args.include == "pending" else ("confirmed" if chosen is None or unit["page"] in chosen else "excluded")
    output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[done] {len(manifest['units'])} units -> {output}"); return 0


def input_units(args) -> list[dict]:
    if Path(args.input).is_file() and Path(args.input).suffix.lower() == '.json':
        units = confirmed_units(read_json(Path(args.input)))
        if not units:
            raise ValueError('no confirmed lessons; confirm the course scope before acquisition')
        return units
    bv = parse_bv(args.input)
    _, pages = fetch_video(bv, args.cookie)
    return [{'lesson_id': f"P{p['page']:03d}", 'bvid': bv, 'cid': p['cid'],
             'title': p['part'], 'duration': p['duration'], 'page': p['page']}
            for p in parse_range(args.items, pages)]


def cmd_subs(args) -> int:
    units = input_units(args)
    outdir = Path(args.outdir); count = 0; failed = []; missing = []
    for unit in units:
        try:
            data = api('/x/player/v2', {'bvid': unit['bvid'], 'cid': unit['cid']}, args.cookie)
            tracks = (data.get('subtitle') or {}).get('subtitles') or []
            if not tracks:
                missing.append(unit['lesson_id'])
                print(f"[missing-subtitles] {unit['lesson_id']}: use ASR for this lesson")
                if args.delay:
                    time.sleep(args.delay)
                continue
            track = next((item for item in tracks if str(item.get('lan', '')).startswith('zh')), tracks[0])
            url = track.get('subtitle_url', '')
            url = 'https:' + url if url.startswith('//') else url
            body = json.loads(_get(url, {'User-Agent': UA}).decode('utf-8'))
            rows = [{'start': item['from'], 'end': item['to'], 'text': item['content'],
                     'timing': 'segment'} for item in body.get('body', []) if item.get('content')]
            save_transcript(stable_stem(unit), rows, outdir, unit, 'native-subtitles')
            count += 1
        except (DownloadError, ValueError, KeyError, OSError) as error:
            failed.append({'lesson_id': unit['lesson_id'], 'error': str(error)})
        if args.delay:
            time.sleep(args.delay)
    print(json.dumps({'subtitle_lessons': count, 'missing_subtitles': missing, 'failed': failed}, ensure_ascii=False))
    return bool(failed)



def cmd_audio(args) -> int:
    """Resolve streams one at a time and download serially; failures are aggregated."""
    units = input_units(args)
    outdir = Path(args.outdir); failures = []
    for index, unit in enumerate(units, 1):
        target = outdir / f"{stable_stem(unit)}.m4a"
        if target.exists() and not args.force and (decode_errors(target) == 0 if ffmpeg_executable() else args.allow_weak_verify and target.stat().st_size > 0):
            print(f"[{index}/{len(units)}] skip {target.name}"); continue
        try:
            play = api("/x/player/playurl", {"bvid": unit["bvid"], "cid": unit["cid"], "fnval": 16, "fourk": 1}, args.cookie)
            urls, expected = expected_bytes(play, int(unit.get("duration") or 0))
            if not ffmpeg_executable() and not args.allow_weak_verify:
                raise DownloadError("ffmpeg unavailable; pass --allow-weak-verify after acknowledging weak checks")
            ffmpeg = ffmpeg_executable()
            if not (play.get('dash') or {}).get('audio'):
                if not ffmpeg:
                    raise DownloadError('legacy combined video/audio needs ffmpeg to extract audio')
                legacy, converted = target.with_suffix('.legacy.mp4'), target.with_suffix('.remux.m4a')
                try:
                    download(urls, legacy, args.cookie, verify=True)
                    result = subprocess.run([ffmpeg, '-v', 'error', '-i', str(legacy), '-vn', '-c:a', 'aac',
                                             '-b:a', '128k', '-y', str(converted)], capture_output=True)
                    if result.returncode or decode_errors(converted):
                        raise DownloadError('legacy audio extraction failed; provide local media')
                    converted.replace(target)
                finally:
                    legacy.unlink(missing_ok=True); converted.unlink(missing_ok=True)
            else:
                download(urls, target, args.cookie, expected, verify=bool(ffmpeg))
            print(f"[{index}/{len(units)}] ok {target.name}")
        except (DownloadError, KeyError, ValueError, OSError) as error:
            failures.append({"lesson_id": unit.get("lesson_id"), "error": str(error)})
            print(f"[{index}/{len(units)}] FAIL {unit.get('lesson_id')}: {error}", file=sys.stderr)
        if args.delay: time.sleep(args.delay)
    if failures:
        print(json.dumps({"failed": failures}, ensure_ascii=False, indent=2), file=sys.stderr); return 1
    return 0


def cmd_discover(args) -> int:
    """Make a candidate manifest from explicit BVs; discovery never auto-confirms."""
    if args.up:
        try:
            records = up_records(args.up, args.series, args.cookie)
        except (DownloadError, ValueError) as error:
            print(f"[error] UP discovery failed: {error}\n       supply explicit BV ids if this endpoint requires WBI authentication", file=sys.stderr)
            return 1
        if not records:
            print("[info] no UP uploads matched the keyword", file=sys.stderr)
            return 0
        manifest = manifest_from_records(records, f"UP {args.up} {args.series or ''}".strip(), "up-search", args.cookie)
    elif args.inputs and any("BV" in item for item in args.inputs):
        manifest = build_manifest(args.inputs, args.cookie, args.source_type, args.delay)
    else:
        if not args.inputs:
            print("[error] discover needs BV ids, a collection/series URL, or --up UID", file=sys.stderr)
            return 2
        records = []
        for raw in args.inputs:
            kind, found = archive_records(raw, args.cookie)
            records.extend({**item, "source_type": kind} for item in found)
        manifest = manifest_from_records(records, "Bilibili collection/series", args.source_type, args.cookie)
    for unit in manifest["units"]:
        unit["selection"] = "pending"
        unit["added_by"] = "auto"
    output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("候选视频已写入清单，需用户确认 selection 和 study_order 后再处理：", output)
    return 0


def cmd_verify(args) -> int:
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8")); missing, bad = [], []
    ffmpeg = ffmpeg_executable()
    if not ffmpeg and not args.allow_weak_verify:
        print("[error] ffmpeg is required for reliable verification; pass --allow-weak-verify for size-only", file=sys.stderr); return 2
    for unit in manifest.get("units", []):
        if unit.get("selection") != "confirmed": continue
        target = Path(args.outdir) / f"{stable_stem(unit)}.m4a"
        if not target.exists(): missing.append(unit["lesson_id"]); continue
        minimum = max(1024, int(unit.get("duration") or 0) * 100)
        if target.stat().st_size < minimum:
            bad.append({"lesson_id": unit["lesson_id"], "error": f"file too small for weak check ({target.stat().st_size} < {minimum})"})
            continue
        errors = decode_errors(target) if ffmpeg else 0
        if errors: bad.append({"lesson_id": unit["lesson_id"], "decode_errors": errors})
    print(json.dumps({"missing": missing, "bad": bad, "weak_check": not bool(ffmpeg)}, ensure_ascii=False, indent=2)); return 1 if missing or bad else 0


def main() -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("list"); p.add_argument("input"); p.add_argument("--cookie"); p.set_defaults(func=cmd_list)
    p = sub.add_parser("manifest"); p.add_argument("inputs", nargs="+"); p.add_argument("-o", "--output", default="course_manifest.json"); p.add_argument("--cookie"); p.add_argument("--source-type", default="manual", choices=["manual", "collection", "series", "up-search"]); p.add_argument("--include", default="pending", help="pending (default), all, or page numbers after user confirmation"); p.add_argument("--delay", type=float, default=1.0); p.set_defaults(func=cmd_manifest)
    p = sub.add_parser("subs"); p.add_argument("input"); p.add_argument("-I", "--items", default="all"); p.add_argument("-o", "--outdir", default="transcripts"); p.add_argument("--cookie"); p.add_argument("--delay", type=float, default=1.0); p.set_defaults(func=cmd_subs)
    p = sub.add_parser("audio"); p.add_argument("input", help="BV/URL or course_manifest.json"); p.add_argument("-I", "--items", default="all"); p.add_argument("-o", "--outdir", default="downloads"); p.add_argument("--cookie"); p.add_argument("--delay", type=float, default=1.0); p.add_argument("--force", action="store_true"); p.add_argument("--allow-weak-verify", action="store_true"); p.set_defaults(func=cmd_audio)
    p = sub.add_parser("discover"); p.add_argument("inputs", nargs="*", help="candidate BV/collection/series URLs (confirm before processing)"); p.add_argument("--up", help="UP UID for public upload search"); p.add_argument("--series", help="keyword for UP search, or URL type hint"); p.add_argument("-o", "--output", default="course_manifest.json"); p.add_argument("--cookie"); p.add_argument("--source-type", default="manual", choices=["manual", "collection", "series"]); p.add_argument("--delay", type=float, default=1.0); p.set_defaults(func=cmd_discover)
    p = sub.add_parser("verify"); p.add_argument("manifest"); p.add_argument("-o", "--outdir", default="downloads"); p.add_argument("--allow-weak-verify", action="store_true"); p.set_defaults(func=cmd_verify)
    args = parser.parse_args()
    try: return args.func(args)
    except (ValueError, DownloadError) as error: print(f"[error] {error}", file=sys.stderr); return 1


if __name__ == "__main__": raise SystemExit(main())
