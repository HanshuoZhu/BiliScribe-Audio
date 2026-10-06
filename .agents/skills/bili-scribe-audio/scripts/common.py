"""Shared identities, timed transcripts and local file handling; no model calls."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
from pathlib import Path


def load_env() -> None:
    """Load only this project's supported settings; shell values take precedence."""
    root = next((p for p in Path(__file__).resolve().parents if (p / 'requirements.txt').is_file()), None)
    if root is None:
        return
    path = root / '.env'
    if not path.is_file():
        return
    allowed = ('ASR_', 'MINERU_', 'HF_')
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        if key == 'FFMPEG' or key.startswith(allowed):
            os.environ.setdefault(key, value.strip().strip('\"\''))


def seconds(value) -> float:
    if isinstance(value, bool):
        raise ValueError('boolean is not a timestamp')
    if isinstance(value, str) and ':' in value:
        parts = value.strip('`[] ').split(':')
        if len(parts) not in (2, 3) or not all(re.fullmatch(r'\d+(?:\.\d+)?', p) for p in parts):
            raise ValueError(f'invalid timestamp: {value}')
        if float(parts[-1]) >= 60 or (len(parts) == 3 and float(parts[-2]) >= 60):
            raise ValueError(f'invalid timestamp: {value}')
        result = sum(float(p) * 60 ** i for i, p in enumerate(reversed(parts)))
    else:
        result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f'invalid timestamp: {value}')
    return result


def hms(value) -> str:
    total = int(seconds(value))
    return f'{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}'


def read_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def digest(path: Path) -> str:
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def fingerprint(data) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def identity(unit: dict) -> dict:
    return {key: unit[key] for key in ('lesson_id', 'bvid', 'cid')}


def confirmed_units(manifest: dict) -> list[dict]:
    units = manifest.get('units')
    if not isinstance(units, list) or not units:
        raise ValueError('course manifest must contain units')
    lessons, videos = set(), set()
    for unit in units:
        if not isinstance(unit, dict) or not re.fullmatch(r'[A-Za-z0-9_-]+', str(unit.get('lesson_id', ''))):
            raise ValueError('invalid lesson_id')
        if not re.fullmatch(r'BV[A-Za-z0-9]{10}', str(unit.get('bvid', ''))) or not isinstance(unit.get('cid'), int) or isinstance(unit['cid'], bool) or unit['cid'] <= 0:
            raise ValueError(f"invalid BVID/CID for {unit['lesson_id']}")
        if unit['lesson_id'] in lessons or (unit['bvid'], unit['cid']) in videos:
            raise ValueError('duplicate lesson_id or BVID/CID')
        lessons.add(unit['lesson_id']); videos.add((unit['bvid'], unit['cid']))
        if unit.get('selection') not in ('pending', 'confirmed', 'excluded'):
            raise ValueError(f"invalid selection for {unit['lesson_id']}")
        for key in ('source_order', 'study_order'):
            if not isinstance(unit.get(key), int) or isinstance(unit[key], bool) or unit[key] < 1:
                raise ValueError(f'invalid {key}')
    selected = sorted((u for u in units if u['selection'] == 'confirmed'), key=lambda u: u['study_order'])
    if len({u['study_order'] for u in selected}) != len(selected):
        raise ValueError('duplicate confirmed study_order')
    return selected


def ffmpeg_executable() -> str | None:
    configured = os.getenv('FFMPEG')
    if configured:
        return shutil.which(configured) or (configured if Path(configured).is_file() else None)
    executable = shutil.which('ffmpeg')
    if executable:
        return executable
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return None


def media_duration(path: Path) -> float | None:
    ffmpeg = ffmpeg_executable()
    if not ffmpeg:
        return None
    result = subprocess.run([ffmpeg, '-hide_banner', '-i', str(path)], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
    match = re.search(r'Duration: (\d+:\d+:\d+(?:\.\d+)?)', result.stderr)
    return seconds(match.group(1)) if match else None


def save_transcript(stem: str, segments: list[dict], outdir: Path, unit: dict | None, source: str) -> None:
    records = []
    for segment in segments:
        if not str(segment.get('text', '')).strip():
            continue
        start = seconds(segment['start'])
        end = seconds(segment['end']) if segment.get('end') is not None else None
        if end is not None and end <= start:
            raise ValueError('transcript end must be after start')
        records.append({**segment, 'id': f'T{len(records) + 1:05d}', 'start': start, 'end': end, 'text': segment['text'].strip()})
    if not records:
        raise ValueError(f'no speech detected in {stem}')
    write_json(outdir / f'{stem}.json', {'schema_version': 2, 'identity': identity(unit) if unit else None, 'source': source, 'segments': records})
    (outdir / f'{stem}.md').write_text(f'# {stem}\n\n' + '\n'.join(f"[{hms(s['start'])}] {s['text']}" for s in records) + '\n', encoding='utf-8')
    (outdir / f'{stem}.txt').write_text('\n'.join(s['text'] for s in records) + '\n', encoding='utf-8')


load_env()
