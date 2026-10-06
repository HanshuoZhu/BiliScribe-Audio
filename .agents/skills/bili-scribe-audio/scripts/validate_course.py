#!/usr/bin/env python3
"""Deterministic coverage, provenance and executable-operation checks.

Completed image reads are agent declarations; semantic correctness and successful
user reproduction require the separate sampled audit recorded in the delivery report.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from common import confirmed_units, digest, read_json, seconds, write_json
from evidence import coverage

TIME = re.compile(r'\b(?:\d+:)?\d{1,2}:\d{2}(?:\.\d+)?\b')
PAGE = re.compile(r'(?:课本\s*)?P\s*(\d+)|(?:PDF\s*)?第\s*(\d+)\s*页|第\s*(\d+)\s*(?:张|段)', re.I)
CHAPTER = re.compile(r'\bC\d{3}\b')
ENTRY = re.compile(r'\b[A-Za-z0-9_-]+-E\d+\b')
EMPHASIS = ('期末必考', '类型题必考', '以前考过', '重点', '易错题型')


def timestamp_seconds(value: str) -> float | None:
    try:
        return seconds(value.strip('\x60'))
    except (ValueError, TypeError):
        return None


def validate(root: Path) -> tuple[list[str], list[str], list[str]]:
    errors, warnings, samples = [], [], []
    try:
        manifest = read_json(root / 'course_manifest.json')
        units = confirmed_units(manifest)
    except (OSError, ValueError, KeyError, TypeError) as error:
        return [f'invalid course manifest: {error}'], warnings, samples
    if not units:
        errors.append('no confirmed lessons')
    by_lesson = {u['lesson_id']: u for u in units}
    for name in ('大纲.md', '术语表.md', '习题讲解.md', '交付报告.md'):
        path = root / name
        if not path.is_file() or not path.read_text(encoding='utf-8').strip():
            errors.append(f'missing or empty deliverable: {name}')
    mode = manifest.get('understanding_mode')
    understood = {}
    operations_by_lesson = set()
    if mode in ('transcript-images', 'transcript-only'):
        try:
            understanding = read_json(root / 'understanding_manifest.json')
            evidence = read_json(Path(understanding['evidence_path']))
            if evidence.get('course_manifest_sha256') != digest(root / 'course_manifest.json'):
                errors.append('course manifest changed after evidence preparation')
            if mode != evidence.get('mode'):
                errors.append('requested and actual understanding modes differ')
            errors.extend(coverage(evidence, understanding))
            for packet in evidence['packets']:
                if packet['identity']['lesson_id'] not in by_lesson:
                    errors.append('evidence belongs to an unconfirmed lesson')
                for frame in packet['frames']:
                    if not Path(frame['frame_path']).is_file() or digest(Path(frame['frame_path'])) != frame['sha256']:
                        errors.append(f"missing or changed evidence image: {frame['frame_id']}")
            for record in understanding['records']:
                if record.get('raw_audio_understood'):
                    errors.append('this workflow cannot claim raw audio understanding')
                for entry in record.get('entries', []):
                    if entry['entry_id'] in understood:
                        errors.append('duplicate understanding entry ID')
                    understood[entry['entry_id']] = {**entry, 'lesson_id': record['identity']['lesson_id']}
                for operation in record.get('operations', []):
                    operations_by_lesson.add(record['identity']['lesson_id'])
                    if not operation.get('reproducible'):
                        warnings.append(f"operation needs more evidence: {operation.get('title')}")
                warnings.extend(f"{record['packet_id']}: {gap}" for gap in record.get('gaps', []))
        except (OSError, ValueError, KeyError, TypeError) as error:
            errors.append(f'invalid/missing understanding evidence: {error}')
    elif mode is not None:
        errors.append('unsupported understanding_mode')
    else:
        warnings.append('legacy output has no understanding_mode; no multimodal completion is established')

    notes = list((root / '讲义').glob('*.md'))
    lesson_files = {}
    for path in notes:
        text = path.read_text(encoding='utf-8')
        first_heading = next((line for line in text.splitlines() if line.startswith('# ')), '')
        match = re.search(r'\b(L\d+|P\d+)\b', first_heading)
        if not match or match.group(0) not in by_lesson:
            errors.append(f'unknown/missing lesson in heading: {path.name}'); continue
        lesson = match.group(0)
        if lesson in lesson_files:
            errors.append(f'duplicate note for {lesson}')
        lesson_files[lesson] = path
        substantive = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith(('#', '<!--', '>'))]
        if not substantive:
            errors.append(f'empty lesson note: {lesson}')
        if mode and not ENTRY.search(text) and understood:
            errors.append(f'lesson has no understanding entry citations: {lesson}')
        if lesson in operations_by_lesson:
            for alternatives in (('目标',), ('前置',), ('位置', '入口'), ('动作',), ('输入', '参数'), ('结果',), ('核对', '检查')):
                if not any(word in text for word in alternatives):
                    errors.append(f'operation note missing {"/".join(alternatives)}: {lesson}')
    for lesson in by_lesson:
        if lesson not in lesson_files:
            errors.append(f'confirmed unit has no lesson note: {lesson}')

    textbook = {}
    try:
        if (root / 'textbook_manifest.json').exists():
            textbook = read_json(root / 'textbook_manifest.json')
    except (OSError, ValueError) as error:
        errors.append(f'invalid textbook manifest: {error}')
    chapters = {c['id']: c for c in textbook.get('chapters', [])}
    all_files = [p for p in root.rglob('*.md') if p.name != '交付报告.md' and '课件笔记' not in p.parts]
    for path in all_files:
        current_lesson = next((key for key, value in lesson_files.items() if value == path), None)
        lines = path.read_text(encoding='utf-8').splitlines()
        for number, line in enumerate(lines, 1):
            location = f'{path.name}:{number}'
            times = [timestamp_seconds(m.group(0)) for m in TIME.finditer(line)]
            if any(t is None for t in times):
                errors.append(f'invalid timestamp: {location}')
            if current_lesson and any(t is not None and t >= float(by_lesson[current_lesson].get('duration') or float('inf')) for t in times):
                errors.append(f'timestamp beyond lesson duration: {location}')
            ids = ENTRY.findall(line)
            for eid in ids:
                if eid not in understood and mode:
                    errors.append(f'unknown understanding entry: {eid}')
                elif eid in understood and current_lesson and understood[eid]['lesson_id'] != current_lesson:
                    errors.append(f'entry belongs to another lesson: {location}')
                if eid in understood and understood[eid].get('timing') == 'coarse' and not any(word in line for word in ('粗略', '全讲转录', 'coarse')):
                    errors.append(f'coarse transcript citation needs an explicit timing label: {location}')
            if any(f'[{label}]' in line for label in EMPHASIS) and not times:
                errors.append(f'emphasis without timestamp: {location}')
            if '[视频画面]' in line:
                if mode == 'transcript-only':
                    errors.append(f'visual claim in transcript-only mode: {location}')
                supporting = [understood[eid] for eid in ids if eid in understood and understood[eid]['provenance'] == 'COURSE_VISUAL']
                if mode and (not supporting or not times or not any(abs(entry['timestamp'] - t) < 1 for entry in supporting for t in times if t is not None)):
                    errors.append(f'visual claim has no matching understood frame: {location}')
                if not mode and not times:
                    errors.append(f'visual claim without timestamp: {location}')
            if '[课件]' in line and not PAGE.search(line) and '全文' not in line:
                errors.append(f'material citation without page/slide/paragraph: {location}')
            if '[课本补充]' in line:
                selected = [chapters[cid] for cid in CHAPTER.findall(line) if cid in chapters and chapters[cid].get('selected')]
                page = PAGE.search(line)
                if not selected or not page:
                    errors.append(f'textbook citation needs selected chapter ID and page: {location}')
                else:
                    if any(c.get('page_status') != 'confirmed' for c in selected):
                        errors.append(f'textbook printed pages unconfirmed: {location}')
                    physical = re.search(r'PDF\s*第\s*(\d+)\s*页', line, re.I)
                    if not physical:
                        errors.append(f'textbook citation needs physical PDF page for range validation: {location}')
                    elif not any(c['pdf_start'] <= int(physical.group(1)) <= c['pdf_end'] for c in selected):
                        errors.append(f'textbook page outside selected chapter: {location}')
                    else:
                        printed = re.search(r'课本\s*P\s*(\d+)', line, re.I)
                        mapped = textbook.get('book_page_map', {}).get(physical.group(1))
                        if not printed or str(mapped) != printed.group(1):
                            errors.append(f'textbook printed/PDF page mapping mismatch: {location}')
            if '课程仅答案' in line and '课程完整解' in line:
                errors.append(f'answer-only item also marked complete: {location}')
            if 'DEFERRED' in line and not re.search(r'(?<!UN)RESOLVED|UNRESOLVED', ' '.join(lines[number - 1:number + 3])):
                errors.append(f'deferred event has no final follow-up status: {location}')
            if any(token in line for token in ('重点', '题目', '结论', '老师原话', '[视频画面]', '操作', '点击')):
                samples.append(f'{location} {line.strip()}')
    if textbook.get('chapters'):
        if any(c.get('selected') for c in chapters.values()) and not (root / '互补大纲.md').exists():
            errors.append('selected textbook has no complementary outline')
        for chapter in chapters.values():
            if chapter.get('selected') and chapter.get('confidence') == 'low':
                warnings.append(f"chapter boundary needs confirmation: {chapter['id']}")
    report = root / '交付报告.md'
    if report.exists() and mode:
        text = report.read_text(encoding='utf-8')
        if mode not in text:
            errors.append('delivery report does not state actual understanding mode')
        if '未直接听取原始音频' not in text:
            errors.append('delivery report must disclose transcript-based understanding')
        if '语义抽查' not in text:
            errors.append('delivery report must state semantic audit coverage and result')
        warnings.append('image-read records are agent declarations; inspect sampled evidence and operational reproducibility')
    return list(dict.fromkeys(errors)), list(dict.fromkeys(warnings)), samples


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--audit-sample', type=int, default=10)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    try:
        errors, warnings, samples = validate(args.root)
        result = {'errors': errors, 'warnings': warnings, 'audit_sample': samples[:max(0, args.audit_sample)],
                  'semantic_audit_completed_by_validator': False, 'image_reads_independently_verified': False}
        print(__import__('json').dumps(result, ensure_ascii=False, indent=2))
        if args.report:
            write_json(args.report, result)
        return bool(errors)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f'[error] {error}', file=sys.stderr); return 1


if __name__ == '__main__':
    raise SystemExit(main())
