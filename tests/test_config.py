"""The shipped configuration must stay portable and readable by both layers."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from config_models import AppConfig
from spec2test.adapters.ollama.client import CLOUD_HOST, MODE_CLOUD, MODE_LOCAL
from spec2test.bootstrap import (
    generation_settings_from_config,
    load_config,
    ollama_settings_from_config,
)

CONFIG = Path(__file__).resolve().parents[1] / "config.yml"
WINDOWS_PATH = re.compile(r"[A-Za-z]:\\")


def test_no_user_specific_absolute_paths_remain():
    text = CONFIG.read_text(encoding="utf-8")
    assert not WINDOWS_PATH.search(text), "absolute Windows paths are not portable"
    assert "/Users/" not in text and "/home/" not in text


def test_no_api_key_is_stored_in_the_configuration():
    text = CONFIG.read_text(encoding="utf-8").lower()
    assert "api_key:" not in text
    assert "authorization" not in text
    assert "bearer" not in text


def test_configured_paths_exist_relative_to_the_repository():
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    root = CONFIG.parent
    for key in ("pdf_path", "prompt_path"):
        value = config["paths"][key]
        assert not Path(value).is_absolute()
        assert (root / value).exists(), f"{key} points to {value}, which is missing"


def test_the_new_layer_reads_the_configuration():
    config = load_config(CONFIG)
    settings = ollama_settings_from_config(config)
    assert settings.mode == MODE_CLOUD
    assert settings.endpoint == f"{CLOUD_HOST}/api/chat"
    assert "num_gpu" not in settings.options(), "GPU options are local-only"

    local = ollama_settings_from_config(config, mode=MODE_LOCAL)
    assert local.resolved_host == "http://localhost:11434"

    generation = generation_settings_from_config(config)
    assert generation.max_repair_attempts == 2
    assert generation.job_timeout_s == 900


def test_the_legacy_layer_still_reads_the_configuration():
    config = load_config(CONFIG)
    legacy = AppConfig.from_dict(config)
    assert legacy.model.name
    assert legacy.generation.combine_pages >= 1
    assert legacy.paths.prompt_path is not None


def test_the_documented_model_name_is_not_a_local_alias():
    from spec2test.adapters.ollama.models import is_local_cloud_alias

    config = load_config(CONFIG)
    name = config["model"]["name"]
    assert not is_local_cloud_alias(name), (
        f"{name!r} is a local alias; the cloud API expects the /api/tags name"
    )
