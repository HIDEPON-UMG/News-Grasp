from __future__ import annotations

import argparse
import json
import sys

from tools.youtube_podcast.upload_episode import YouTubePodcastClient


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description="YouTube Podcast 用 playlist を作成または取得します。").parse_args(argv)
    try:
        playlist_id = YouTubePodcastClient.from_local_secrets().ensure_playlist()
        print(json.dumps({"playlist_id": playlist_id}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(f"[youtube-podcast][WARN] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

