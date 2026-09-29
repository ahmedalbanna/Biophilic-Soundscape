"""نقطة دخول PyInstaller: يبني ملف exe واحد للتطبيق."""

import multiprocessing
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.context_aware_audio.app import main
from src.context_aware_audio.log_setup import install as install_logging

if __name__ == "__main__":
    multiprocessing.freeze_support()
    # لازم قبل main(): وضع --windowed لا يملك stdout، فأي خطأ قبل الواجهة يختفي
    main(log_path=install_logging())
