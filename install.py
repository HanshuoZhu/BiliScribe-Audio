"""Install a self-contained Codex skill; no downloads or dependency installation."""
import argparse
import json
import os
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent

def install(destination, update=False):
    source = next((ROOT / '.agents/skills').iterdir())
    target = Path(destination).expanduser().resolve() / source.name
    if target.is_symlink():
        raise ValueError('refusing to update a symlink; choose an ordinary directory')
    if target.exists() and not update:
        raise ValueError(f'{target} exists; inspect it and use --update if appropriate')
    if target.exists() and not (target / 'SKILL.md').is_file():
        raise ValueError('destination is not an existing skill')
    target.mkdir(parents=True, exist_ok=True)
    for item in source.rglob('*'):
        if not item.is_file() or '__pycache__' in item.parts:
            continue
        relative = item.relative_to(source)
        output = target / relative
        if output.is_symlink() or any(p.is_symlink() for p in output.parents if p != target.parent):
            raise ValueError('refusing to write through a symlink')
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(item, output)
    for name in ('requirements.txt', 'requirements-asr.txt', '.env.example', 'LICENSE', 'NOTICE', 'VERSION'):
        shutil.copyfile(ROOT / name, target / name)
    shutil.copytree(ROOT / 'licenses', target / 'licenses', dirs_exist_ok=True)
    shutil.copytree(ROOT / 'personas', target / 'personas', dirs_exist_ok=True)
    return target

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    codex_dir = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
    parser.add_argument('--destination', type=Path, default=codex_dir / 'skills')
    parser.add_argument('--update', action='store_true', help='update source files; preserve .env and .venv')
    args = parser.parse_args()
    try:
        target = install(args.destination, args.update)
    except (ValueError, OSError) as error:
        parser.exit(1, f'Install failed: {error}\n')
    print(json.dumps({'installed': str(target), 'next': f'python "{target / "scripts/setup.py"}"'}, ensure_ascii=False))
    print('Open a new Codex conversation. Dependency setup is a separate explicit command.')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
