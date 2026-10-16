import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import tempfile  # noqa: E402

# Point all data paths at a throwaway dir before any app module imports config.
os.environ["PIREADER_DIR"] = tempfile.mkdtemp(prefix="pireader-test-")
