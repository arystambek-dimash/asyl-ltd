"""Require the latest main CI run for the exact release before image builds."""

import json
import sys


def verify_release(payload, *, sha, repository):
    candidates = [
        run for run in payload["workflow_runs"]
        if run.get("head_sha") == sha
        and run.get("head_branch") == "main"
        and run.get("event") in {"push", "workflow_dispatch"}
        and run.get("head_repository", {}).get("full_name") == repository
        and run.get("path", "").split("@")[0] == ".github/workflows/ci-checks.yml"
    ]
    if not candidates:
        raise ValueError("Release has no main CI run. Run CI checks for this commit first.")
    latest = max(candidates, key=lambda run: (run["id"], run.get("run_attempt", 1)))
    if latest.get("status") != "completed" or latest.get("conclusion") != "success":
        raise ValueError(
            f"Release CI {latest['id']} is {latest.get('conclusion') or latest.get('status')}; "
            "wait for successful checks or rerun the failed/cancelled CI."
        )
    return latest["id"]


if __name__ == "__main__":
    try:
        run_id = verify_release(json.load(sys.stdin), sha=sys.argv[1], repository=sys.argv[2])
    except (ValueError, KeyError, TypeError, IndexError) as error:
        print(f"::error::{error}", file=sys.stderr)
        sys.exit(1)
    print(f"Verified successful CI run {run_id} for release {sys.argv[1]}.")
