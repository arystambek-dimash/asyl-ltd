import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("verify_release_ci", ROOT / "deploy/verify-release-ci.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ReleaseCITest(unittest.TestCase):
    def run_record(self, **changes):
        return {
            "id": 17, "head_sha": "a" * 40, "head_branch": "main",
            "head_repository": {"full_name": "owner/repo"},
            "path": ".github/workflows/ci-checks.yml", "event": "push",
            "status": "completed", "conclusion": "success", **changes,
        }

    def verify(self, *runs):
        return module.verify_release(
            {"workflow_runs": list(runs)}, sha="a" * 40, repository="owner/repo",
        )

    def test_accepts_successful_main_ci_for_release_only(self):
        self.assertEqual(self.verify(self.run_record()), 17)
        self.assertEqual(self.verify(self.run_record(event="workflow_dispatch")), 17)

    def test_rejects_other_sha_branch_repo_workflow_and_pr(self):
        cases = [
            {"head_sha": "b" * 40}, {"head_branch": "feature"},
            {"head_repository": {"full_name": "fork/repo"}},
            {"path": ".github/workflows/deploy-production.yml"},
            {"event": "pull_request"},
        ]
        for change in cases:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.verify(self.run_record(**change))

    def test_rejects_missing_failed_cancelled_and_unfinished_checks(self):
        with self.assertRaises(ValueError):
            self.verify()
        for status, conclusion in [("completed", "failure"), ("completed", "cancelled"),
                                   ("in_progress", None), ("queued", None)]:
            with self.subTest(status=status, conclusion=conclusion), self.assertRaises(ValueError):
                self.verify(self.run_record(status=status, conclusion=conclusion))

    def test_old_success_cannot_mask_a_new_failed_run_or_attempt(self):
        with self.assertRaises(ValueError):
            self.verify(self.run_record(), self.run_record(id=18, conclusion="failure"))
        with self.assertRaises(ValueError):
            self.verify(self.run_record(), self.run_record(run_attempt=2, conclusion="cancelled"))


if __name__ == "__main__":
    unittest.main()
