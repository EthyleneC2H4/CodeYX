"""Regression tests for the 2026-08 audit Wave 2 config-merge fix.

The three-layer merge (user → project → local) used "differs from default"
as its was-this-set sentinel, so a project could never set a field BACK to
its default — e.g. overriding a user-level `permission_mode: acceptEdits`
with `permission_mode: default` was silently ignored."""

from __future__ import annotations

from pathlib import Path

import yaml

from codeyx.config import (
    AppConfig,
    WorktreeConfig,
    _merge_config,
    load_config,
)


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data), encoding="utf-8")


_MIN_PROVIDERS = [
    {
        "name": "p1",
        "protocol": "anthropic",
        "base_url": "",
        "model": "claude-haiku-4-5-20251001",
    }
]


class TestThreeLayerMerge:
    def test_project_can_reset_permission_mode_to_default(
        self, tmp_path, monkeypatch
    ):
        home = tmp_path / "home"
        proj = tmp_path / "proj"
        _write(home / ".codeyx" / "config.yaml", {
            "providers": _MIN_PROVIDERS,
            "permission_mode": "acceptEdits",
        })
        _write(proj / ".codeyx" / "config.yaml", {
            "permission_mode": "default",
        })

        monkeypatch.chdir(proj)
        monkeypatch.setenv("HOME", str(home))
        merged = load_config()

        assert merged.permission_mode == "default", (
            "project-level `permission_mode: default` must override the "
            "user-level value"
        )

    def test_loaded_file_records_explicit_keys(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        _write(cfg_file, {"providers": _MIN_PROVIDERS, "teammate_mode": ""})
        loaded = load_config(cfg_file)
        assert "providers" in loaded.explicit
        assert "teammate_mode" in loaded.explicit
        assert "permission_mode" not in loaded.explicit

    def test_upper_layer_absent_key_keeps_lower_value(self):
        base = AppConfig(providers=[], permission_mode="bypassPermissions")
        override = AppConfig(providers=[])  # nothing written by this layer
        merged = _merge_config(base, override)
        assert merged.permission_mode == "bypassPermissions"

    def test_boolean_can_be_disabled_by_upper_layer(self):
        base = AppConfig(providers=[], enable_fork=True)
        override = AppConfig(providers=[], enable_fork=False)
        override.explicit = {"enable_fork"}
        merged = _merge_config(base, override)
        assert merged.enable_fork is False, (
            "explicit `enable_fork: false` must be able to disable it"
        )

    def test_unwritten_bool_stays_absent(self):
        base = AppConfig(providers=[], enable_fork=True)
        override = AppConfig(providers=[], enable_fork=False)  # default value,
        assert "enable_fork" not in override.explicit  # not written by file
        merged = _merge_config(base, override)
        assert merged.enable_fork is True

    def test_worktree_keys_presence_based(self):
        base = AppConfig(providers=[], worktree=WorktreeConfig())
        base.worktree.stale_cutoff_hours = 72

        override = AppConfig(
            providers=[],
            worktree=WorktreeConfig(stale_cutoff_hours=24),
        )
        override.worktree_explicit = {"stale_cutoff_hours"}
        merged = _merge_config(base, override)
        assert merged.worktree.stale_cutoff_hours == 24
