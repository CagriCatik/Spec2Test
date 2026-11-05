"""The .env loader: convenience for the API key, without leaking it."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from spec2test.bootstrap import ENV_FILENAME, load_env_file

KEY = "SPEC2TEST_TEST_KEY"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv(KEY, raising=False)
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)


def write_env(directory: Path, content: str) -> Path:
    path = directory / ENV_FILENAME
    path.write_text(content, encoding="utf-8")
    return path


def test_values_are_loaded_into_the_environment(tmp_path):
    write_env(tmp_path, f"{KEY}=secret-value\n")
    assert load_env_file(tmp_path / ENV_FILENAME) == [KEY]
    assert os.environ[KEY] == "secret-value"


def test_only_names_are_returned_never_values(tmp_path):
    write_env(tmp_path, f"{KEY}=secret-value\n")
    returned = load_env_file(tmp_path / ENV_FILENAME)
    assert "secret-value" not in " ".join(returned)


def test_comments_blank_lines_and_export_prefixes_are_handled(tmp_path):
    write_env(
        tmp_path,
        "# a comment\n"
        "\n"
        f"export {KEY}=from-export\n"
        "   # indented comment\n"
        "NOT_AN_ASSIGNMENT\n",
    )
    assert load_env_file(tmp_path / ENV_FILENAME) == [KEY]
    assert os.environ[KEY] == "from-export"


@pytest.mark.parametrize("quoted", ['"quoted value"', "'quoted value'"])
def test_quotes_are_stripped(tmp_path, quoted):
    write_env(tmp_path, f"{KEY}={quoted}\n")
    load_env_file(tmp_path / ENV_FILENAME)
    assert os.environ[KEY] == "quoted value"


def test_an_exported_variable_wins_over_the_file(tmp_path, monkeypatch):
    monkeypatch.setenv(KEY, "from-shell")
    write_env(tmp_path, f"{KEY}=from-file\n")
    assert load_env_file(tmp_path / ENV_FILENAME) == []
    assert os.environ[KEY] == "from-shell"


def test_override_is_possible_when_asked_for(tmp_path, monkeypatch):
    monkeypatch.setenv(KEY, "from-shell")
    write_env(tmp_path, f"{KEY}=from-file\n")
    assert load_env_file(tmp_path / ENV_FILENAME, override=True) == [KEY]
    assert os.environ[KEY] == "from-file"


def test_a_missing_file_is_not_an_error(tmp_path):
    assert load_env_file(tmp_path / "nothing-here") == []


def test_the_working_directory_is_searched(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_env(tmp_path, f"{KEY}=from-cwd\n")
    assert load_env_file() == [KEY]
    assert os.environ[KEY] == "from-cwd"


def test_the_example_file_ships_without_a_value():
    example = Path(__file__).resolve().parents[1] / ".env.example"
    assert example.exists(), ".env.example is the documented starting point"
    lines = [
        line
        for line in example.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert lines == ["OLLAMA_API_KEY="], "the example must not contain a key"


def test_env_files_are_git_ignored_but_the_example_is_not():
    root = Path(__file__).resolve().parents[1]
    ignore = (root / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in ignore
    assert "!.env.example" in ignore
