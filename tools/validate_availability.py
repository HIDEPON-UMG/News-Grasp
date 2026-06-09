#!/usr/bin/env python3
"""公開面が空白化していないことを確認する Availability Gate。"""
from __future__ import annotations

import sys

from tools.publish_fallback import main


if __name__ == "__main__":
    sys.exit(main(["validate", *sys.argv[1:]]))
