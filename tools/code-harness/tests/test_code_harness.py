"""Unit tests for code-harness."""

from __future__ import annotations

import io
import json
import os
import pathlib
import stat
import tempfile
import unittest
from unittest import mock

from code_harness import cli
from code_harness.config import (
    CliNotFoundError,
    ConfigError,
    load_config,
    resolve_cli_bin,
)


def _make_executable(path: pathlib.Path, content: str = "#!/bin/sh\n") -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(content, encoding="utf-8")
  path.chmod(path.stat().st_mode | stat.S_IXUSR)


class ConfigTests(unittest.TestCase):

  def test_default_config_uses_agy(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      env = {"XDG_CONFIG_HOME": tmp}
      with mock.patch("pathlib.Path.home", return_value=pathlib.Path(tmp)):
        cfg = load_config(env=env)
    self.assertEqual(cfg.default_flavor, "agy")
    self.assertIn("agy", cfg.flavors)
    self.assertEqual(cfg.flavors["agy"].data_dir_name, "antigravity-cli")

  def test_custom_flavor_and_builtin_extension(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      cfg_file = pathlib.Path(tmp) / "config.json"
      cfg_file.write_text(
          json.dumps({
              "default_flavor": "alt",
              "flavors": {
                  "alt": {
                      "env_bin": "ALT_CLI_BIN",
                      "candidate_bins": ["/opt/alt/bin/alt", "alt-cli"],
                      "data_dir_name": "alt-cli",
                      "match_basenames": ["alt-cli"],
                      "match_path_substrings": ["/opt/alt/"],
                  },
                  "agy": {
                      "extra_match_path_substrings": ["/custom/agy/build"],
                  },
              },
              "extra_non_tui_subcommands": ["custom-sub"],
              "extra_cli_takes_value": ["--custom-flag"],
          }),
          encoding="utf-8",
      )
      cfg = load_config(config_path=cfg_file)

    self.assertEqual(cfg.default_flavor, "alt")
    self.assertEqual(cfg.flavors["alt"].data_dir_name, "alt-cli")
    self.assertIn("agy", cfg.flavors["agy"].match_basenames)
    self.assertEqual(
        cfg.flavors["agy"].match_path_substrings, ("/custom/agy/build",)
    )
    self.assertIn("custom-sub", cfg.non_tui_subcommands)
    self.assertIn("--custom-flag", cfg.cli_takes_value)

  def test_resolve_skips_excluded_ide_path(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      ide_bin = pathlib.Path(tmp) / "opt" / "antigravity" / "bin" / "agy"
      real_bin = pathlib.Path(tmp) / "user-bin" / "antigravity-cli"
      _make_executable(ide_bin)
      _make_executable(real_bin)
      env = {
          "XDG_CONFIG_HOME": tmp,
          "PATH": f"{ide_bin.parent}:{real_bin.parent}",
      }
      with mock.patch("pathlib.Path.home", return_value=pathlib.Path(tmp)):
        cfg = load_config(env=env)
        resolved = resolve_cli_bin(cfg, env=env)
    self.assertEqual(resolved, str(real_bin))

  def test_resolve_env_precedence(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      cfg_file = pathlib.Path(tmp) / "config.json"
      cfg_file.write_text(
          json.dumps({
              "default_flavor": "alt",
              "flavors": {
                  "alt": {
                      "env_bin": "ALT_CLI_BIN",
                      "candidate_bins": ["missing-bin"],
                  }
              },
          }),
          encoding="utf-8",
      )
      cfg = load_config(config_path=cfg_file)
      self.assertEqual(
          resolve_cli_bin(cfg, env={"ALT_CLI_BIN": "/from/flavor"}),
          "/from/flavor",
      )
      self.assertEqual(
          resolve_cli_bin(
              cfg,
              env={
                  "CODE_HARNESS_BIN": "/from/global",
                  "ALT_CLI_BIN": "/from/flavor",
              },
          ),
          "/from/global",
      )

  def test_missing_binary_raises(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      env = {"XDG_CONFIG_HOME": tmp, "PATH": tmp}
      with mock.patch("pathlib.Path.home", return_value=pathlib.Path(tmp)):
        cfg = load_config(env=env)
        with self.assertRaises(CliNotFoundError):
          resolve_cli_bin(cfg, env=env)

  def test_unknown_default_flavor_raises(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      cfg_file = pathlib.Path(tmp) / "config.json"
      cfg_file.write_text(
          json.dumps({"default_flavor": "nope"}), encoding="utf-8"
      )
      with self.assertRaises(ConfigError):
        load_config(config_path=cfg_file)


class CliTests(unittest.TestCase):

  def test_run_forwards_all_args_verbatim(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      fake_bin = pathlib.Path(tmp) / "agy"
      _make_executable(fake_bin)
      env = {"XDG_CONFIG_HOME": tmp, "PATH": tmp}
      with (
          mock.patch.dict(os.environ, env, clear=True),
          mock.patch("pathlib.Path.home", return_value=pathlib.Path(tmp)),
          mock.patch("os.execvp") as execvp,
      ):
        cli.main(["run", "--help", "-m", "model-x", "--", "pos"])
      execvp.assert_called_once_with(
          str(fake_bin),
          [str(fake_bin), "--help", "-m", "model-x", "--", "pos"],
      )

  def test_path_subcommand_override_and_builtin_bypass(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      plugin = pathlib.Path(tmp) / "code-harness-run"
      fake_bin = pathlib.Path(tmp) / "agy"
      _make_executable(plugin)
      _make_executable(fake_bin)
      env = {"XDG_CONFIG_HOME": tmp, "PATH": tmp}
      with (
          mock.patch.dict(os.environ, env, clear=True),
          mock.patch("pathlib.Path.home", return_value=pathlib.Path(tmp)),
          mock.patch("os.execvp") as execvp,
      ):
        cli.main(["run", "--flag"])
        execvp.assert_called_once_with(
            str(plugin), [str(plugin), "--flag"]
        )

      with (
          mock.patch.dict(os.environ, env, clear=True),
          mock.patch("pathlib.Path.home", return_value=pathlib.Path(tmp)),
          mock.patch("os.execvp") as execvp,
      ):
        cli.main(["--builtin", "run", "--flag"])
        execvp.assert_called_once_with(
            str(fake_bin), [str(fake_bin), "--flag"]
        )

  def test_help_lists_public_commands_only(self) -> None:
    stdout = io.StringIO()
    with mock.patch("sys.stdout", stdout):
      rc = cli.main(["--help"])
    self.assertEqual(rc, 0)
    out = stdout.getvalue()
    self.assertIn("run", out)
    self.assertIn("--flavor", out)
    self.assertIn("--builtin", out)


if __name__ == "__main__":
  unittest.main()
