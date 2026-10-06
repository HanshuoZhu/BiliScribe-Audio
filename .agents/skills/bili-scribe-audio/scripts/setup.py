"""Create a private virtual environment on Windows, Linux or macOS."""
import argparse
import subprocess
import sys
from pathlib import Path

def main():
    root = next((p for p in Path(__file__).resolve().parents if (p / 'requirements.txt').is_file()), None)
    if root is None:
        raise SystemExit('requirements.txt not found; install or open the complete source project')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--with-asr', action='store_true', help='also install optional local faster-whisper')
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        parser.error('Python 3.11+ is required; Python 3.12 is recommended')
    venv = root / '.venv'
    python = venv / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    if not python.is_file():
        subprocess.run([sys.executable, '-m', 'venv', str(venv)], check=True)
    subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(root / 'requirements.txt')], check=True)
    if args.with_asr:
        subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(root / 'requirements-asr.txt')], check=True)
    return subprocess.run([str(python), str(Path(__file__).with_name('doctor.py'))], check=False).returncode

if __name__ == '__main__':
    raise SystemExit(main())
