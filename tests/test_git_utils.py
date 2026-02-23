"""Tests for git_utils module — branch detection and worktree management."""
import subprocess

import pytest

from conn_server.git_utils import get_current_branch, is_git_repo, create_worktree, remove_worktree


def _init_git_repo(path, branch="main"):
    """Helper to create a minimal git repo at the given path."""
    subprocess.run(["git", "init", "-b", branch, str(path)], capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(path), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(path), capture_output=True, check=True)
    # Need at least one commit for worktrees to work
    (path / "README.md").write_text("test")
    subprocess.run(["git", "add", "."], cwd=str(path), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=str(path), capture_output=True, check=True)


class TestGetCurrentBranch:
    def test_returns_branch_for_git_repo(self, tmp_path):
        _init_git_repo(tmp_path, branch="main")
        assert get_current_branch(str(tmp_path)) == "main"

    def test_returns_custom_branch_name(self, tmp_path):
        _init_git_repo(tmp_path, branch="develop")
        assert get_current_branch(str(tmp_path)) == "develop"

    def test_returns_none_for_non_git_dir(self, tmp_path):
        assert get_current_branch(str(tmp_path)) is None

    def test_returns_none_for_nonexistent_path(self):
        assert get_current_branch("/nonexistent/path/abc123") is None


class TestIsGitRepo:
    def test_true_for_git_repo(self, tmp_path):
        _init_git_repo(tmp_path)
        assert is_git_repo(str(tmp_path)) is True

    def test_false_for_non_git_dir(self, tmp_path):
        assert is_git_repo(str(tmp_path)) is False

    def test_false_for_nonexistent_path(self):
        assert is_git_repo("/nonexistent/path/abc123") is False


class TestCreateWorktree:
    def test_creates_worktree_successfully(self, tmp_path, tmp_config_dir):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)

        wt_path = create_worktree(str(repo), "conv_123")
        assert wt_path is not None
        assert (tmp_config_dir["worktrees_dir"] / "conv_123").exists()

        # Verify the worktree is on the right branch
        branch = get_current_branch(wt_path)
        assert branch == "conn/conv_123"

    def test_returns_none_for_non_git_dir(self, tmp_path, tmp_config_dir):
        result = create_worktree(str(tmp_path), "conv_456")
        assert result is None

    def test_creates_from_custom_base_branch(self, tmp_path, tmp_config_dir):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo, branch="develop")

        wt_path = create_worktree(str(repo), "conv_789", base_branch="develop")
        assert wt_path is not None
        assert get_current_branch(wt_path) == "conn/conv_789"


class TestRemoveWorktree:
    def test_removes_worktree_and_branch(self, tmp_path, tmp_config_dir):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)

        wt_path = create_worktree(str(repo), "conv_del")
        assert wt_path is not None
        assert (tmp_config_dir["worktrees_dir"] / "conv_del").exists()

        result = remove_worktree(str(repo), "conv_del")
        assert result is True
        assert not (tmp_config_dir["worktrees_dir"] / "conv_del").exists()

        # Verify branch is deleted
        branch_check = subprocess.run(
            ["git", "branch", "--list", "conn/conv_del"],
            cwd=str(repo), capture_output=True, text=True,
        )
        assert branch_check.stdout.strip() == ""

    def test_idempotent_remove(self, tmp_path, tmp_config_dir):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)

        # Remove a worktree that doesn't exist — should not raise
        result = remove_worktree(str(repo), "conv_nonexistent")
        assert result is True  # No worktree dir to remove, branch -D is best-effort
