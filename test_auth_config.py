"""Tests for auth and config modules."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from auth import verify_token
from config import load_config, get_auth_token, get_working_dir, get_port, get_host


class TestVerifyToken:
    def test_valid_token(self, tmp_config_dir):
        assert verify_token(tmp_config_dir["token"]) is True

    def test_invalid_token(self, tmp_config_dir):
        assert verify_token("wrong-token") is False

    def test_empty_token(self, tmp_config_dir):
        assert verify_token("") is False


class TestLoadConfig:
    def test_loads_existing_config(self, tmp_config_dir):
        config = load_config()
        assert config["auth_token"] == tmp_config_dir["token"]
        assert config["port"] == 8080

    def test_generates_config_when_missing(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_dir = tmp_path

        with patch("config.CONFIG_DIR", config_dir), \
             patch("config.CONFIG_FILE", config_file), \
             patch("config.HISTORY_DIR", tmp_path / "history"), \
             patch("config.UPLOADS_DIR", tmp_path / "uploads"), \
             patch("config.LOG_DIR", tmp_path / "logs"):
            config = load_config()

        assert config_file.exists()
        assert len(config["auth_token"]) == 64  # hex(32) = 64 chars
        assert config["port"] == 8080

    def test_generated_config_persists(self, tmp_path):
        config_file = tmp_path / "config.json"

        with patch("config.CONFIG_DIR", tmp_path), \
             patch("config.CONFIG_FILE", config_file), \
             patch("config.HISTORY_DIR", tmp_path / "history"), \
             patch("config.UPLOADS_DIR", tmp_path / "uploads"), \
             patch("config.LOG_DIR", tmp_path / "logs"):
            config1 = load_config()
            config2 = load_config()

        assert config1["auth_token"] == config2["auth_token"]


class TestConfigHelpers:
    def test_get_auth_token(self, tmp_config_dir):
        assert get_auth_token() == tmp_config_dir["token"]

    def test_get_working_dir(self, tmp_config_dir):
        result = get_working_dir()
        assert result == str(tmp_config_dir["projects_dir"])

    def test_get_port(self, tmp_config_dir):
        assert get_port() == 8080

    def test_get_host(self, tmp_config_dir):
        assert get_host() == "0.0.0.0"


class TestBuildPrompt:
    """Test the _build_prompt helper for image handling."""

    def test_no_images(self):
        from server import _build_prompt
        assert _build_prompt("hello", []) == "hello"

    def test_with_images(self):
        from server import _build_prompt
        result = _build_prompt("describe this", ["/tmp/img.jpg"])
        assert "[The user attached an image" in result
        assert "/tmp/img.jpg" in result
        assert "describe this" in result

    def test_images_only(self):
        from server import _build_prompt
        result = _build_prompt("", ["/tmp/img.jpg"])
        assert "View and describe it" in result
        assert "/tmp/img.jpg" in result

    def test_multiple_images(self):
        from server import _build_prompt
        result = _build_prompt("look", ["/tmp/a.jpg", "/tmp/b.jpg"])
        assert "/tmp/a.jpg" in result
        assert "/tmp/b.jpg" in result
        assert "look" in result
