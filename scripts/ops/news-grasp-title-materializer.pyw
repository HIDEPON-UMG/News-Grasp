"""05:59 JST の Codex App title materialization 用 hidden entrypoint。"""

from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.news_grasp_title_materializer import main  # noqa: E402


raise SystemExit(main(["--repo-root", str(REPO_ROOT)]))
