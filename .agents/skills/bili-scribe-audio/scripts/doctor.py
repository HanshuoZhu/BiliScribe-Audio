"""Check local preparation dependencies without network or model downloads."""
import importlib.util
import os
import sys
from common import ffmpeg_executable

def main():
    required = ['requests', 'pypdf', 'imageio_ffmpeg']
    present = {name: importlib.util.find_spec(name) is not None for name in required + ['faster_whisper']}
    ffmpeg = ffmpeg_executable()
    print(f'python={sys.executable}; version={sys.version.split()[0]}')
    for name in required:
        print(f'{name}={"OK" if present[name] else "MISSING"}')
    print(f'ffmpeg={ffmpeg or "MISSING"}')
    print(f'local_asr={"installed" if present["faster_whisper"] else "optional; setup.py --with-asr"}')
    print(f'cloud_asr={"configured" if os.getenv("ASR_API_KEY") else "optional; not configured"}')
    print('understanding_engine=current Codex session; Python cannot certify semantic accuracy')
    print("video_mode=transcript-only; video images are outside this edition")
    return 0 if sys.version_info >= (3,11) and all(present[n] for n in required) and ffmpeg else 1

if __name__ == '__main__':
    raise SystemExit(main())
