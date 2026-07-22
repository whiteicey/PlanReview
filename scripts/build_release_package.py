"""Build a complete release ZIP directly from a Git tree and verify it."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from verify_release_package import verify_release_package


def build_release_package(repo: Path, tree_ish: str, output: Path) -> dict[str, object]:
    repo = repo.resolve()
    output = output.resolve()
    if output.exists():
        raise FileExistsError(f"release output already exists: {output.name}")
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "archive", "--format=zip", f"--output={output}", tree_ish],
        cwd=repo,
        check=True,
    )
    try:
        result = verify_release_package(output)
    except Exception:
        output.unlink(missing_ok=True)
        raise
    return {
        "archive": output.name,
        "tree_ish": tree_ish,
        "member_count": result.member_count,
        "required_file_count": result.required_file_count,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--tree-ish", default="HEAD")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = build_release_package(args.repo, args.tree_ish, args.output)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
