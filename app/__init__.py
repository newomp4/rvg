# Pin HuggingFace cache to the project folder so deleting RVG2/ removes
# everything we ever downloaded. Must run before any HF library imports.
import os
from pathlib import Path
_root = Path(__file__).resolve().parent.parent
os.environ.setdefault("HF_HOME", str(_root / "models"))
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(_root / "models" / "playwright"))
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
(_root / "models").mkdir(parents=True, exist_ok=True)
(_root / "models" / "playwright").mkdir(parents=True, exist_ok=True)
