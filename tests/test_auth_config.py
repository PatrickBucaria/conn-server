"""Tests for auth and config modules."""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from conn_server.auth import verify_token
from conn_server.config import load_config, get_auth_token, get_working_dir, get_port, get_host, print_startup_banner


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

        with patch("conn_server.config.CONFIG_DIR", config_dir), \
             patch("conn_server.config.CONFIG_FILE", config_file), \
             patch("conn_server.config.HISTORY_DIR", tmp_path / "history"), \
             patch("conn_server.config.UPLOADS_DIR", tmp_path / "uploads"), \
             patch("conn_server.config.LOG_DIR", tmp_path / "logs"), \
             patch("conn_server.config.RELEASES_DIR", tmp_path / "releases"), \
             patch("conn_server.config.PROJECTS_CONFIG_DIR", tmp_path / "projects"):
            config = load_config()

        assert config_file.exists()
        assert len(config["auth_token"]) == 64  # hex(32) = 64 chars
        assert config["port"] == 8443

    def test_generated_config_persists(self, tmp_path):
        config_file = tmp_path / "config.json"

        with patch("conn_server.config.CONFIG_DIR", tmp_path), \
             patch("conn_server.config.CONFIG_FILE", config_file), \
             patch("conn_server.config.HISTORY_DIR", tmp_path / "history"), \
             patch("conn_server.config.UPLOADS_DIR", tmp_path / "uploads"), \
             patch("conn_server.config.LOG_DIR", tmp_path / "logs"), \
             patch("conn_server.config.RELEASES_DIR", tmp_path / "releases"), \
             patch("conn_server.config.PROJECTS_CONFIG_DIR", tmp_path / "projects"):
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


class TestEnvVarOverrides:
    def test_conn_working_dir_env(self, tmp_config_dir):
        with patch.dict(os.environ, {"CONN_WORKING_DIR": "/tmp/my-projects"}):
            assert get_working_dir() == "/tmp/my-projects"

    def test_conn_port_env(self, tmp_config_dir):
        with patch.dict(os.environ, {"CONN_PORT": "9090"}):
            assert get_port() == 9090

    def test_conn_host_env(self, tmp_config_dir):
        with patch.dict(os.environ, {"CONN_HOST": "127.0.0.1"}):
            assert get_host() == "127.0.0.1"

    def test_env_takes_precedence_over_config(self, tmp_config_dir):
        """Env vars override config file values."""
        with patch.dict(os.environ, {"CONN_WORKING_DIR": "/override"}):
            assert get_working_dir() == "/override"
        # Without env var, falls back to config
        assert get_working_dir() == str(tmp_config_dir["projects_dir"])


class TestStartupBanner:
    def test_prints_connection_info(self, tmp_config_dir, capsys):
        print_startup_banner()
        output = capsys.readouterr().out
        assert "Conn Server" in output
        assert "URL:" in output
        assert "Auth token:" in output
        assert "Projects:" in output
        assert tmp_config_dir["token"] in output

    def test_first_run_message(self, tmp_path, capsys):
        config_file = tmp_path / "config.json"
        with patch("conn_server.config.CONFIG_DIR", tmp_path), \
             patch("conn_server.config.CONFIG_FILE", config_file), \
             patch("conn_server.config.HISTORY_DIR", tmp_path / "history"), \
             patch("conn_server.config.UPLOADS_DIR", tmp_path / "uploads"), \
             patch("conn_server.config.LOG_DIR", tmp_path / "logs"), \
             patch("conn_server.config.RELEASES_DIR", tmp_path / "releases"), \
             patch("conn_server.config.PROJECTS_CONFIG_DIR", tmp_path / "projects"):
            print_startup_banner()
        output = capsys.readouterr().out
        assert "Config generated" in output

    def test_warns_missing_working_dir(self, tmp_config_dir, capsys):
        with patch.dict(os.environ, {"CONN_WORKING_DIR": "/nonexistent/path"}):
            print_startup_banner()
        output = capsys.readouterr().out
        assert "Warning" in output
        assert "/nonexistent/path" in output


class TestBuildPrompt:
    """Test the _build_prompt helper for image handling."""

    def test_no_images(self):
        from conn_server.server import _build_prompt
        assert _build_prompt("hello", []) == "hello"

    def test_with_images(self):
        from conn_server.server import _build_prompt
        result = _build_prompt("describe this", ["/tmp/img.jpg"])
        assert "[The user attached an image" in result
        assert "/tmp/img.jpg" in result
        assert "describe this" in result

    def test_images_only(self):
        from conn_server.server import _build_prompt
        result = _build_prompt("", ["/tmp/img.jpg"])
        assert "View and describe it" in result
        assert "/tmp/img.jpg" in result

    def test_multiple_images(self):
        from conn_server.server import _build_prompt
        result = _build_prompt("look", ["/tmp/a.jpg", "/tmp/b.jpg"])
        assert "/tmp/a.jpg" in result
        assert "/tmp/b.jpg" in result
        assert "look" in result
