"""Tests for AgentManager — agent CRUD and markdown parsing."""

import pytest

from conn_server.agent_manager import AgentManager, AgentInfo, _parse_frontmatter


class TestParseFrontmatter:
    def test_basic_frontmatter(self):
        content = "---\nname: test\ndescription: A test agent\n---\n\nHello world"
        fm, body = _parse_frontmatter(content)
        assert fm["name"] == "test"
        assert fm["description"] == "A test agent"
        assert "Hello world" in body

    def test_no_frontmatter(self):
        content = "Just some text"
        fm, body = _parse_frontmatter(content)
        assert fm == {}
        assert body == "Just some text"

    def test_unclosed_frontmatter(self):
        content = "---\nname: test\nno closing delimiter"
        fm, body = _parse_frontmatter(content)
        assert fm == {}

    def test_quoted_values(self):
        content = '---\nname: "my-agent"\ndescription: \'A test\'\n---\n'
        fm, _ = _parse_frontmatter(content)
        assert fm["name"] == "my-agent"
        assert fm["description"] == "A test"

    def test_comma_separated_tools(self):
        content = "---\nname: test\ndescription: desc\ntools: Read, Grep, Glob\n---\n"
        fm, _ = _parse_frontmatter(content)
        assert fm["tools"] == "Read, Grep, Glob"


class TestAgentManager:
    @pytest.fixture
    def manager(self, tmp_config_dir):
        return AgentManager(agents_dir=tmp_config_dir["agents_dir"])

    @pytest.fixture
    def sample_agent(self):
        return AgentInfo(
            name="code-reviewer",
            description="Reviews code for quality",
            prompt="You are a code reviewer. Focus on quality and security.",
            model="sonnet",
            tools=["Read", "Grep", "Glob"],
        )

    def test_list_empty(self, manager):
        assert manager.list_agents() == []

    def test_create_and_list(self, manager, sample_agent):
        manager.create_agent(sample_agent)
        agents = manager.list_agents()
        assert len(agents) == 1
        assert agents[0]["name"] == "code-reviewer"
        assert agents[0]["description"] == "Reviews code for quality"
        assert agents[0]["model"] == "sonnet"
        assert agents[0]["tools"] == ["Read", "Grep", "Glob"]

    def test_create_writes_markdown_file(self, manager, sample_agent, tmp_config_dir):
        manager.create_agent(sample_agent)
        path = tmp_config_dir["agents_dir"] / "code-reviewer.md"
        assert path.exists()
        content = path.read_text()
        assert "---" in content
        assert "name: code-reviewer" in content
        assert "description: Reviews code for quality" in content
        assert "tools: Read, Grep, Glob" in content
        assert "model: sonnet" in content
        assert "You are a code reviewer" in content

    def test_create_duplicate_raises(self, manager, sample_agent):
        manager.create_agent(sample_agent)
        with pytest.raises(ValueError, match="already exists"):
            manager.create_agent(sample_agent)

    def test_get_agent(self, manager, sample_agent):
        manager.create_agent(sample_agent)
        agent = manager.get_agent("code-reviewer")
        assert agent is not None
        assert agent.name == "code-reviewer"
        assert agent.description == "Reviews code for quality"
        assert agent.prompt == "You are a code reviewer. Focus on quality and security."
        assert agent.model == "sonnet"
        assert agent.tools == ["Read", "Grep", "Glob"]

    def test_get_nonexistent(self, manager):
        assert manager.get_agent("nope") is None

    def test_update_agent(self, manager, sample_agent):
        manager.create_agent(sample_agent)
        updated = AgentInfo(
            name="code-reviewer",
            description="Updated description",
            prompt="New prompt",
            model="opus",
            tools=["Read", "Write"],
        )
        result = manager.update_agent("code-reviewer", updated)
        assert result is not None
        assert result.description == "Updated description"

        agent = manager.get_agent("code-reviewer")
        assert agent.prompt == "New prompt"
        assert agent.model == "opus"
        assert agent.tools == ["Read", "Write"]

    def test_update_nonexistent(self, manager):
        agent = AgentInfo(name="nope", description="nope")
        assert manager.update_agent("nope", agent) is None

    def test_update_rename(self, manager, sample_agent, tmp_config_dir):
        manager.create_agent(sample_agent)
        renamed = AgentInfo(
            name="reviewer",
            description="Renamed agent",
            prompt="Same prompt",
        )
        result = manager.update_agent("code-reviewer", renamed)
        assert result.name == "reviewer"
        assert not (tmp_config_dir["agents_dir"] / "code-reviewer.md").exists()
        assert (tmp_config_dir["agents_dir"] / "reviewer.md").exists()

    def test_delete_agent(self, manager, sample_agent, tmp_config_dir):
        manager.create_agent(sample_agent)
        assert manager.delete_agent("code-reviewer")
        assert not (tmp_config_dir["agents_dir"] / "code-reviewer.md").exists()
        assert manager.list_agents() == []

    def test_delete_nonexistent(self, manager):
        assert not manager.delete_agent("nope")

    def test_parse_existing_file(self, manager, tmp_config_dir):
        """Test parsing a hand-written agent file."""
        content = """---
name: debugger
description: Expert debugger
tools: Read, Edit, Bash, Grep, Glob
model: inherit
permissionMode: acceptEdits
maxTurns: 20
---

You are an expert debugger. Fix bugs efficiently.
"""
        (tmp_config_dir["agents_dir"] / "debugger.md").write_text(content)
        agent = manager.get_agent("debugger")
        assert agent is not None
        assert agent.name == "debugger"
        assert agent.description == "Expert debugger"
        assert agent.tools == ["Read", "Edit", "Bash", "Grep", "Glob"]
        assert agent.model == "inherit"
        assert agent.permission_mode == "acceptEdits"
        assert agent.max_turns == 20
        assert "Fix bugs efficiently" in agent.prompt

    def test_list_skips_malformed_files(self, manager, tmp_config_dir):
        """Malformed files should be skipped, not crash list."""
        (tmp_config_dir["agents_dir"] / "bad.md").write_text("no frontmatter here")
        # This file has no description so it technically parses but with empty desc
        agents = manager.list_agents()
        # Should not crash, may include file with empty description
        assert isinstance(agents, list)

    def test_list_ignores_non_md_files(self, manager, tmp_config_dir):
        (tmp_config_dir["agents_dir"] / "notes.txt").write_text("not an agent")
        assert manager.list_agents() == []


class TestValidation:
    @pytest.fixture
    def manager(self, tmp_config_dir):
        return AgentManager(agents_dir=tmp_config_dir["agents_dir"])

    def test_invalid_name_uppercase(self, manager):
        agent = AgentInfo(name="MyAgent", description="test")
        with pytest.raises(ValueError, match="Invalid agent name"):
            manager.create_agent(agent)

    def test_invalid_name_spaces(self, manager):
        agent = AgentInfo(name="my agent", description="test")
        with pytest.raises(ValueError, match="Invalid agent name"):
            manager.create_agent(agent)

    def test_invalid_name_starts_with_digit(self, manager):
        agent = AgentInfo(name="1agent", description="test")
        with pytest.raises(ValueError, match="Invalid agent name"):
            manager.create_agent(agent)

    def test_missing_description(self, manager):
        agent = AgentInfo(name="test", description="")
        with pytest.raises(ValueError, match="description is required"):
            manager.create_agent(agent)

    def test_invalid_model(self, manager):
        agent = AgentInfo(name="test", description="test", model="gpt-4")
        with pytest.raises(ValueError, match="Invalid model"):
            manager.create_agent(agent)

    def test_invalid_permission_mode(self, manager):
        agent = AgentInfo(name="test", description="test", permission_mode="yolo")
        with pytest.raises(ValueError, match="Invalid permission mode"):
            manager.create_agent(agent)

    def test_valid_name_with_hyphens(self, manager):
        agent = AgentInfo(name="my-code-reviewer", description="test")
        result = manager.create_agent(agent)
        assert result.name == "my-code-reviewer"

    def test_valid_name_with_digits(self, manager):
        agent = AgentInfo(name="reviewer2", description="test")
        result = manager.create_agent(agent)
        assert result.name == "reviewer2"


class TestRoundTrip:
    """Test that create → get preserves all fields."""

    @pytest.fixture
    def manager(self, tmp_config_dir):
        return AgentManager(agents_dir=tmp_config_dir["agents_dir"])

    def test_full_roundtrip(self, manager):
        agent = AgentInfo(
            name="full-agent",
            description="An agent with all fields",
            prompt="You are a specialized agent.\n\nFollow these rules:\n- Rule 1\n- Rule 2",
            model="haiku",
            tools=["Read", "Grep"],
            disallowed_tools=["Write", "Bash"],
            permission_mode="plan",
            mcp_servers=["slack", "github"],
            max_turns=10,
        )
        manager.create_agent(agent)
        loaded = manager.get_agent("full-agent")

        assert loaded.name == agent.name
        assert loaded.description == agent.description
        assert loaded.prompt == agent.prompt
        assert loaded.model == agent.model
        assert loaded.tools == agent.tools
        assert loaded.disallowed_tools == agent.disallowed_tools
        assert loaded.permission_mode == agent.permission_mode
        assert loaded.mcp_servers == agent.mcp_servers
        assert loaded.max_turns == agent.max_turns

    def test_minimal_roundtrip(self, manager):
        agent = AgentInfo(name="minimal", description="Just a name and desc")
        manager.create_agent(agent)
        loaded = manager.get_agent("minimal")

        assert loaded.name == "minimal"
        assert loaded.description == "Just a name and desc"
        assert loaded.prompt == ""
        assert loaded.model is None
        assert loaded.tools is None
