"""Git utilities for branch detection and worktree management."""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from .config import WORKTREES_DIR

logger = logging.getLogger(__name__)


def get_current_branch(repo_path: str) -> str | None:
    """Return the current git branch name for a directory, or None if not a git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def is_git_repo(path: str) -> bool:
    """Check if a directory is inside a git repository."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def create_worktree(repo_path: str, conversation_id: str, base_branch: str | None = None) -> str | None:
    """Create a git worktree for a conversation.

    Creates branch 'conn/{conversation_id}' from the current branch.
    Returns the worktree path on success, None on failure.
    """
    WORKTREES_DIR.mkdir(parents=True, exist_ok=True)
    worktree_path = WORKTREES_DIR / conversation_id
    branch_name = f"conn/{conversation_id}"

    if base_branch is None:
        base_branch = get_current_branch(repo_path) or "HEAD"

    try:
        result = subprocess.run(
            ["git", "worktree", "add", "-b", branch_name, str(worktree_path), base_branch],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            logger.info(f"Created worktree at {worktree_path} (branch: {branch_name})")
            return str(worktree_path)
        else:
            logger.error(f"Failed to create worktree: {result.stderr}")
            return None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.error(f"Worktree creation error: {e}")
        return None


def remove_worktree(repo_path: str, conversation_id: str) -> bool:
    """Remove a worktree and its branch. Returns True on success."""
    worktree_path = WORKTREES_DIR / conversation_id
    branch_name = f"conn/{conversation_id}"

    success = True

    # Remove worktree
    if worktree_path.exists():
        try:
            result = subprocess.run(
                ["git", "worktree", "remove", str(worktree_path), "--force"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                logger.error(f"Failed to remove worktree: {result.stderr}")
                success = False
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            logger.error(f"Worktree removal error: {e}")
            success = False

    # Delete the branch
    try:
        subprocess.run(
            ["git", "branch", "-D", branch_name],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass  # Branch may already be gone

    return success
