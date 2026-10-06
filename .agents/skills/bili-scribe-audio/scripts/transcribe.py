#!/usr/bin/env python3
"""Turn lecture audio into timestamped transcripts, and clean up ASR term errors.

Three transcript sources, tried in the order the SKILL.md workflow dictates:
  1. the video's own subtitles  -- fetched by bili_fetch.py subs
  2. a cloud ASR endpoint       -- any OpenAI-compatible /audio/transcriptions
  3. local faster-whisper       -- GPU first, CPU fallback, no torch required

    python transcribe.py --check                       # verify device + model, then stop
    python transcribe.py downloads/ -o transcripts      # auto: cloud if configured, else local
    python transcribe.py downloads/ --engine local
    python transcribe.py --apply-glossary terms.json -o transcripts --dry-run

Engine choice: `auto` uses the cloud endpoint when ASR_API_KEY is set, otherwise local.
"""

import argparse
import csv
import mimetypes
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from common import confirmed_units, media_duration, read_json, save_transcript

# Keep model downloads inside the workspace cache by default. This avoids a read-only
# global Hugging Face cache and stays excluded by the repository's .gitignore.
os.environ.setdefault("HF_HOME", str((Path(".cache") / "bili-scribe" / "huggingface").resolve()))
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

AUDIO_EXTS = {".m4a", ".mp3", ".wav", ".flac", ".aac", ".opus", ".webm", ".mp4", ".mkv"}
TEXT_EXTS = (".md", ".txt", ".srt")
GLOSSARY_EXTS = (".json",)

# turbo is the accuracy/speed sweet spot for lecture audio; small is the CPU default
# because turbo on CPU is slower than a 2-hour-lecture budget allows.
MODEL_GPU, MODEL_CPU = "large-v3-turbo", "small"

# Cloud ASR: any OpenAI-compatible /audio/transcriptions endpoint. Most providers cap
# uploads around 25 MB, and a 90-minute lecture is ~50 MB, so audio gets split first.
CLOUD_BASE = os.getenv("ASR_BASE_URL", "https://api.openai.com/v1")
CLOUD_MODEL = os.getenv("ASR_MODEL", "whisper-1")
CLOUD_KEY = os.getenv("ASR_API_KEY", "")
SEGMENT_SECONDS = 90
def find_ffmpeg() -> str:
    configured = os.getenv("FFMPEG")
    if configured and (shutil.which(configured) or Path(configured).is_file()):
        return shutil.which(configured) or configured
    system = shutil.which("ffmpeg")
    if system: return system
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return "ffmpeg"


FFMPEG = find_ffmpeg()

# The Hub is unreachable from mainland China, and its newer Xet storage backend has no
# mirror, so the mirror alone is not enough -- Xet must be off. Every user there hits
# this on the first run, so the failure says what to do instead of dumping a traceback.
DOWNLOAD_HINT = """[hint] could not download the model weights. If you are in mainland China:
       PowerShell:  $env:HF_ENDPOINT='https://hf-mirror.com'; $env:HF_HUB_DISABLE_XET='1'
       bash:        export HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1
       Or pick a smaller --model (e.g. small) / use --device cpu."""

CLOUD_HINT = """[hint] no cloud ASR configured, using local faster-whisper. To use a cloud endpoint instead:
       export ASR_API_KEY=...            # required
       export ASR_BASE_URL=https://api.siliconflow.cn/v1   # any OpenAI-compatible endpoint
       export ASR_MODEL=FunAudioLLM/SenseVoiceSmall"""


# --------------------------------------------------------------------------- local

def pick_device(requested: str) -> tuple[str, str]:
    """Return (device, compute_type). 8 GB VRAM fits int8_float16, not float16."""
    import ctranslate2

    if requested == "cpu":
        return "cpu", "int8"
    try:
        if ctranslate2.get_cuda_device_count() < 1:
            raise RuntimeError("no CUDA device visible to CTranslate2")
        return "cuda", "int8_float16"
    except Exception as e:
        if requested == "cuda":
            raise
        print(f"[warn] CUDA unusable ({e}); falling back to CPU", file=sys.stderr)
        return "cpu", "int8"


def _is_cuda_error(e: Exception) -> bool:
    s = str(e).lower()
    return any(k in s for k in ("cublas", "cudnn", "cuda", "no kernel image"))


def load_model(model_name: str | None, device: str, compute_type: str, cpu_threads: int | None):
    from faster_whisper import WhisperModel

    name = model_name or (MODEL_GPU if device == "cuda" else MODEL_CPU)
    print(f"[info] loading {name} on {device}/{compute_type}", flush=True)
    t0 = time.time()
    try:
        model = WhisperModel(name, device=device, compute_type=compute_type, cpu_threads=cpu_threads or 0)
    except Exception as e:
        if device != "cuda" or not _is_cuda_error(e):
            if "connect" in str(e).lower() or "snapshot" in str(e).lower():
                print(DOWNLOAD_HINT, file=sys.stderr)
            raise
        # Missing cublas64_12.dll is the usual cause; see README.
        print(f"[warn] CUDA load failed ({e}); falling back to CPU int8", file=sys.stderr)
        name, device, compute_type = MODEL_CPU, "cpu", "int8"
        model = WhisperModel(name, device=device, compute_type=compute_type, cpu_threads=cpu_threads or 0)
    print(f"[info] loaded in {time.time() - t0:.1f}s", flush=True)
    return model, name, device


def local_segments(model, audio: Path, language: str | None):
    """Yield (seconds, text) pairs, printing progress as it goes."""
    segments, info = model.transcribe(
        str(audio),
        language=language,
        vad_filter=True,  # also skips silence, so long lectures get faster
        condition_on_previous_text=False,  # breaks Whisper's repetition loops
        beam_size=5,
    )
    total = info.duration or 0.0
    for seg in segments:
        text = seg.text.strip()
        if text:
            pct = f"{seg.end / total * 100:5.1f}%" if total else "  ?  "
            print(f"\r  {pct} {hms(seg.end)}/{hms(total)}", end="", flush=True)
            yield {"start": seg.start, "end": seg.end, "text": text, "timing": "segment"}


# --------------------------------------------------------------------------- cloud

def ffmpeg_split(audio: Path, workdir: Path, seconds: int) -> list[tuple[Path, float]]:
    """Cut audio into upload-sized pieces. Returns [(path, start_offset_seconds)]."""
    if not Path(FFMPEG).is_file() and not shutil.which(FFMPEG):
        # A short file is already below common upload limits; send it directly.
        # Longer files still need ffmpeg for deterministic offsets and are rejected clearly.
        if audio.stat().st_size <= 25 * 1024 * 1024:
            return [(audio, 0.0)]
        raise SystemExit(f"[error] ffmpeg is required to split large audio ({FFMPEG} not found); short files can be sent directly")
    workdir.mkdir(parents=True, exist_ok=True)
    pattern = workdir / f"{audio.stem}.%03d.mp3"
    segment_list = workdir / f"{audio.stem}.segments.csv"
    for stale in workdir.glob(f"{audio.stem}.*.mp3"):
        stale.unlink()
    subprocess.run(
        [FFMPEG, "-v", "error", "-i", str(audio), "-f", "segment",
         "-segment_time", str(seconds), "-segment_list", str(segment_list), "-segment_list_type", "csv", "-acodec", "libmp3lame", "-q:a", "4", "-y", str(pattern)],
        check=True, capture_output=True,
    )
    parts = sorted(workdir.glob(f"{audio.stem}.*.mp3"))
    if not parts:
        raise SystemExit(f"[error] ffmpeg produced no audio segments for {audio.name}")
    with segment_list.open(encoding="utf-8") as handle:
        offsets = [float(row[1]) for row in csv.reader(handle)]
    if len(offsets) != len(parts):
        raise RuntimeError("audio segment timing list mismatch")
    return list(zip(parts, offsets))


def multipart_transcribe(url: str, path: Path, model: str, key: str, language: str | None) -> tuple[int, str]:
    """POST one audio file with stdlib multipart; avoids a hidden requests dependency."""
    boundary = "----bili-scribe-" + uuid.uuid4().hex
    fields = {"model": model, **({"language": language} if language else {})}
    body = bytearray()
    for name, value in fields.items():
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()
    body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\nContent-Type: {mimetypes.guess_type(path.name)[0] or 'application/octet-stream'}\r\n\r\n".encode()
    body += path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    request = urllib.request.Request(url, data=bytes(body), method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(request, timeout=900) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")


def cloud_segments(audio: Path, workdir: Path, base: str, model: str, key: str, language: str | None):
    """Yield (seconds, text) from an OpenAI-compatible transcription endpoint."""
    url = f"{base.rstrip('/')}/audio/transcriptions"
    for part, offset in ffmpeg_split(audio, workdir, SEGMENT_SECONDS):
        for attempt in range(1, 4):
            if attempt > 1:
                time.sleep(min(2**attempt, 30))
                print(f"\r  retry {attempt}/3 {part.name}", flush=True)
            status, response_text = multipart_transcribe(url, part, model, key, language)
            if status == 200:
                break
            if status in (429, 500, 502, 503, 504):
                continue
            body = response_text[:300].strip()
            if status in (401, 403):
                raise RuntimeError(f"[error] cloud ASR refused the key ({status}). "
                                 f"Check ASR_API_KEY. Server said: {body[:160]}")
            if status == 404 or body[:1] == "<":
                # The usual cause is a chat-model gateway: it serves /chat/completions and
                # answers an HTML 404 here, which is confusing if we just dump the page.
                raise RuntimeError(
                    f"[error] {url} does not offer /audio/transcriptions (HTTP {status}).\n"
                    f"        ASR_BASE_URL must point at a service that does speech-to-text --\n"
                    f"        e.g. 硅基流动 https://api.siliconflow.cn/v1 with ASR_MODEL=FunAudioLLM/SenseVoiceSmall,\n"
                    f"        or any OpenAI-compatible ASR endpoint. Chat-model gateways do not work.\n"
                    f"        Use --engine local to transcribe on this machine instead."
                )
            raise RuntimeError(f"[error] cloud ASR rejected the request ({status}): {body}")
        else:
            raise RuntimeError(f"[error] cloud ASR failed after 3 attempts on {part.name}")

        try:
            payload = json.loads(response_text)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"[error] cloud ASR returned invalid JSON: {response_text[:200]}") from error
        rows = payload.get('segments') or []
        if rows:
            for row in rows:
                if row.get('text'):
                    yield {'start': offset + float(row['start']), 'end': offset + float(row['end']),
                           'text': row['text'].strip(), 'timing': 'segment'}
        else:
            text = (payload.get('text') or '').strip()
            duration = media_duration(part)
            if text and duration:
                yield {'start': offset, 'end': offset + duration, 'text': ' '.join(text.split()), 'timing': 'coarse'}
            elif text:
                raise RuntimeError('cloud ASR did not return timings and local media duration is unavailable')
        print(f'\r  {part.name} transcribed', end='', flush=True)


# --------------------------------------------------------------------------- glossary

def load_glossary(path: Path, min_len: int) -> list[tuple[str, str, bool]]:
    """Load {wrong: right} or {wrong: {replace: ..., regex: true}} term corrections.

    Keys shorter than min_len are dropped: for Chinese, single-character replacements
    match inside unrelated words and silently corrupt the transcript. The upstream tool
    this format comes from has no such guard.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SystemExit(f"[error] {path} must be a JSON object of wrong -> right mappings")
    rules = []
    for key, value in raw.items():
        if key.startswith("_"):
            continue  # allow "_readme"/"_notes" free-text comments inside the glossary
        if isinstance(value, dict):
            if not value.get("regex"):
                raise SystemExit(f"[error] {path}: object form is only valid with \"regex\": true ({key!r})")
            rules.append((key, str(value.get("replace", "")), True))
        elif isinstance(value, str):
            if len(key) < min_len:
                print(f"[warn] skipping {key!r}: shorter than {min_len} chars", file=sys.stderr)
                continue
            rules.append((re.escape(key), value, False))
        else:
            raise SystemExit(f"[error] {path}: value for {key!r} must be a string or an object")
    return rules


def apply_rules(text: str, rules: list[tuple[str, str, bool]]) -> tuple[str, list[tuple[str, int]]]:
    hits = []
    for pattern, repl, _ in rules:
        text, n = re.subn(pattern, repl, text)
        if n:
            hits.append((pattern, n))
    return text, hits


def configure_console() -> None:
    """Keep Chinese diagnostics readable even when called outside the CLI entrypoint."""
    for stream in (sys.stdout, sys.stderr):
        if callable(getattr(stream, "reconfigure", None)):
            stream.reconfigure(encoding="utf-8", errors="replace")


def run_glossary(args) -> int:
    configure_console()
    rules = load_glossary(Path(args.apply_glossary), args.glossary_min_len)
    files = []
    for raw in args.inputs or [str(args.outdir)]:
        p = Path(raw)
        if p.is_dir():
            files += sorted(f for f in p.iterdir() if f.suffix.lower() in TEXT_EXTS or f.suffix.lower() == '.json')
        elif p.is_file():
            files.append(p)
    if not files:
        raise SystemExit("[error] no .md/.txt/.srt files to correct")
    total_hits = 0
    for f in files:
        original = f.read_text(encoding="utf-8")
        if f.suffix.lower() == ".json":
            data = read_json(f)
            if not isinstance(data, dict) or not isinstance(data.get("segments"), list):
                continue
            hits = []
            for row in data["segments"]:
                row["text"], found = apply_rules(row["text"], rules)
                hits.extend(found)
            fixed = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        else:
            fixed, hits = apply_rules(original, rules)
        total_hits += sum(n for _, n in hits)
        detail = ", ".join(f"{p}×{n}" for p, n in sorted(hits, key=lambda x: -x[1]))
        # A term replaced far more often than its share of the text usually means the
        # pattern is matching inside longer words -- report it instead of hiding it.
        # The bar is high on purpose: some entries are legitimately frequent (ψ alone
        # appears hundreds of times in a lecture), so only outliers are worth flagging.
        loud = [p for p, n in hits if n > 200]
        print(f"{f.name}: {sum(n for _, n in hits)} hit(s){' -- ' + detail if detail else ''}")
        if loud:
            print(f"  [warn] suspiciously frequent, check these patterns: {', '.join(loud)}", file=sys.stderr)
        if not args.dry_run and fixed != original:
            f.write_text(fixed, encoding="utf-8")
    if args.dry_run:
        print(f"[dry-run] {total_hits} replacement(s) in {len(files)} file(s); nothing written")
    else:
        print(f"[done] {total_hits} replacement(s) in {len(files)} file(s)")
    return 0


# --------------------------------------------------------------------------- shared

def hms(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def write_outputs(stem: str, segments: list[dict], outdir: Path,
                  unit: dict | None = None, source: str = 'local-asr') -> None:
    save_transcript(stem, segments, outdir, unit, source)
    print(f"\r  done {len(segments)} segments -> {stem}.json, .md, .txt")

def collect_audio(inputs: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw in inputs:
        p = Path(raw)
        if p.is_dir():
            files += sorted(f for f in p.iterdir() if f.suffix.lower() in AUDIO_EXTS)
        elif p.is_file():
            files.append(p)
        else:
            print(f"[warn] skipping missing path {raw}", file=sys.stderr)
    return files


def manifest_audio(manifest_path: Path, audio_dir: Path) -> list[tuple[Path, str]]:
    """Map audio files to confirmed manifest identities; title-only matches are rejected."""
    manifest = read_json(manifest_path)
    pairs: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for unit in confirmed_units(manifest):
        stem = f"{unit['lesson_id']}_{unit['bvid']}_cid{unit['cid']}_"
        candidates = sorted(p for p in audio_dir.glob(f"{stem}*") if p.is_file() and p.suffix.lower() in AUDIO_EXTS)
        if len(candidates) > 1:
            raise ValueError(f"multiple audio files match {unit['lesson_id']}: {[p.name for p in candidates]}")
        if not candidates:
            print(f"[warn] no audio for {unit['lesson_id']} ({unit.get('title', '')})", file=sys.stderr)
            continue
        output_stem = candidates[0].stem
        if output_stem in seen:
            raise ValueError(f"duplicate transcript output stem: {output_stem}")
        seen.add(output_stem); pairs.append((candidates[0], output_stem))
    return pairs


# --------------------------------------------------------------------------- main

def main() -> int:
    configure_console()

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="*", help="audio files and/or directories")
    ap.add_argument("--manifest", help="course_manifest.json; maps confirmed units to stable audio names")
    ap.add_argument("-o", "--outdir", default="transcripts", help="where .md/.txt go (default: transcripts)")
    ap.add_argument("--engine", choices=["auto", "cloud", "local"], default="auto",
                    help="auto: cloud when ASR_API_KEY is set, else local")
    ap.add_argument("--model", help=f"whisper model for --engine local (default: {MODEL_GPU} on GPU, {MODEL_CPU} on CPU)")
    ap.add_argument("--device", choices=["auto", "cuda", "cpu"], default="auto")
    ap.add_argument("--language", default="zh", help="'auto' to detect (default: zh)")
    ap.add_argument("--cpu-threads", type=int, help="default: all cores")
    ap.add_argument("--force", action="store_true", help="redo files that already have transcripts")
    ap.add_argument("--check", action="store_true", help="verify the local model loads, then exit")
    ap.add_argument("--apply-glossary", metavar="TERMS.json",
                    help="standalone mode: apply term corrections to existing .md/.txt/.srt and exit")
    ap.add_argument("--glossary-min-len", type=int, default=2, help="shortest replaceable key (default: 2)")
    ap.add_argument("--dry-run", action="store_true", help="report glossary hits without writing")
    args = ap.parse_args()

    if args.apply_glossary:
        return run_glossary(args)

    language = None if args.language == "auto" else args.language
    outdir = Path(args.outdir)

    use_cloud = args.engine == "cloud" or (args.engine == "auto" and bool(CLOUD_KEY))
    if args.engine == "cloud" and not CLOUD_KEY:
        raise SystemExit("[error] --engine cloud requires ASR_API_KEY")
    if args.engine == "auto" and not CLOUD_KEY:
        print(CLOUD_HINT, file=sys.stderr)

    model = name = None
    if args.check:
        device, compute_type = pick_device(args.device)
        model, name, device = load_model(args.model, device, compute_type, args.cpu_threads)
        import numpy as np

        test_segments, _ = model.transcribe(np.zeros(16000, dtype=np.float32), beam_size=1)
        list(test_segments)
        print(f"[ok] {name} on {device}/{compute_type} can transcribe")
        if CLOUD_KEY:
            print(f"[ok] cloud ASR configured: {CLOUD_BASE} model={CLOUD_MODEL}")
        else:
            print("[info] no cloud ASR configured (ASR_API_KEY unset)")
        return 0

    # Decide what still needs work before paying for a model load: re-running over an
    # already-transcribed directory should be instant, not a 1.6 GB download.
    pairs = manifest_audio(Path(args.manifest), Path(args.inputs[0] if args.inputs else "downloads")) if args.manifest else [(f, f.stem) for f in collect_audio(args.inputs)]
    files = [f for f, _ in pairs]
    if not files:
        ap.error("no audio files given (pass files or a directory)")
    todo = [(f, stem) for f, stem in pairs if args.force or not (outdir / f"{stem}.json").exists()]
    if not todo:
        print(f"[info] nothing to do: all {len(files)} file(s) already transcribed in {outdir}/")
        return 0
    if model is None and not use_cloud:
        device, compute_type = pick_device(args.device)
        model, name, device = load_model(args.model, device, compute_type, args.cpu_threads)

    where = f"cloud {CLOUD_MODEL}" if use_cloud else f"local {name}"
    print(f"[info] engine: {where}; {len(files)} file(s), {len(files) - len(todo)} already done, {len(todo)} to go")
    started = time.time()
    for i, (f, stem) in enumerate(todo, 1):
        print(f"\n[{i}/{len(todo)}] {f.name}", flush=True)
        t0 = time.time()
        source = 'cloud-asr' if use_cloud else 'local-asr'
        if use_cloud:
            try:
                segments = list(cloud_segments(f, outdir / '_segments', CLOUD_BASE, CLOUD_MODEL, CLOUD_KEY, language))
                if not segments:
                    raise RuntimeError('cloud ASR returned no speech')
            except (RuntimeError, OSError, ValueError) as error:
                if args.engine != 'auto':
                    raise
                print(f'[warn] cloud ASR failed: {error}; trying local ASR', file=sys.stderr)
                if model is None:
                    device, compute_type = pick_device(args.device)
                    model, name, device = load_model(args.model, device, compute_type, args.cpu_threads)
                segments = list(local_segments(model, f, language)); source = 'local-asr'
        else:
            segments = list(local_segments(model, f, language))
        unit = None
        if args.manifest:
            unit = next(u for u in read_json(Path(args.manifest))['units']
                        if stem.startswith(f"{u['lesson_id']}_{u['bvid']}_cid{u['cid']}_"))
        write_outputs(stem, segments, outdir, unit, source)
        print(f"  elapsed {hms(time.time() - t0)}")
    if len(todo) > 1:
        print(f"\n[done] {len(todo)} file(s) in {hms(time.time() - started)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
