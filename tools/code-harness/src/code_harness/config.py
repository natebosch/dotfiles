"""Configuration loading and flavor resolution for code-harness."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import pathlib
import shutil
from typing import Any, Mapping


class ConfigError(RuntimeError):
  """Raised when code-harness configuration is invalid."""


class CliNotFoundError(RuntimeError):
  """Raised when no executable CLI binary can be found for a flavor."""


@dataclass(frozen=True)
class FlavorConfig:
  """Declarative description of a coding-agent CLI flavor."""

  name: str
  env_bin: str = ""
  candidate_bins: tuple[str, ...] = ()
  exclude_path_substrings: tuple[str, ...] = ()
  data_dir_name: str = ""
  match_basenames: tuple[str, ...] = ()
  match_path_substrings: tuple[str, ...] = ()
  match_app_data_basenames: tuple[str, ...] = ()


@dataclass(frozen=True)
class HarnessConfig:
  """Top-level merged configuration for code-harness."""

  default_flavor: str = "agy"
  env_bin: str = "CODE_HARNESS_BIN"
  env_data_dir: str = "CODE_HARNESS_DATA_DIR"
  flavors: Mapping[str, FlavorConfig] = field(default_factory=dict)
  non_tui_subcommands: frozenset[str] = frozenset()
  cli_takes_value: frozenset[str] = frozenset()

  def get_flavor(
      self,
      flavor: str | None = None,
      env: Mapping[str, str] | None = None,
  ) -> FlavorConfig:
    env_map = os.environ if env is None else env
    selected = flavor or env_map.get("CODE_HARNESS_FLAVOR") or self.default_flavor
    if selected not in self.flavors:
      known = ", ".join(sorted(self.flavors))
      raise ConfigError(
          f"Unknown flavor {selected!r}; configured flavors: {known}"
      )
    return self.flavors[selected]


BUILTIN_AGY_FLAVOR = FlavorConfig(
    name="agy",
    env_bin="AGY_CLI_BIN",
    candidate_bins=("~/.local/bin/agy", "agy", "antigravity-cli"),
    exclude_path_substrings=("antigravity.app/", "/opt/antigravity/"),
    data_dir_name="antigravity-cli",
    match_basenames=("agy", "antigravity-cli"),
    match_path_substrings=(),
    match_app_data_basenames=("antigravity-cli",),
)

BUILTIN_NON_TUI_SUBCOMMANDS: frozenset[str] = frozenset({
    "acp",
    "agent",
    "agents",
    "agentapi",
    "attach",
    "changelog",
    "help",
    "hub",
    "install",
    "mcp",
    "mic-serve",
    "models",
    "multicall",
    "plugin",
    "plugincli",
    "plugins",
    "remote-control",
    "swarm",
    "update",
    "web",
})

BUILTIN_CLI_TAKES_VALUE: frozenset[str] = frozenset({
    "--add-dir",
    "--agent",
    "--analytics_server_url",
    "--api_server_url",
    "--app_data_dir",
    "--cdp_port",
    "--cli_log_file",
    "--conversation",
    "--conversation_path",
    "--csrf_token",
    "--effort",
    "--extension_server_csrf_token",
    "--extension_server_port",
    "--file_watch_max_dir_count",
    "--fs_error_probability",
    "--fs_latency_ms",
    "--gemini_dir",
    "--host_bridge_url",
    "--http_server_port",
    "--https_server_port",
    "--hub-port",
    "-i",
    "--prompt-interactive",
    "--inference_api_server_url",
    "--input-format",
    "--json-schema",
    "--limit_go_max_procs",
    "--local_chrome_user_data_dir",
    "--log-file",
    "--lsp_port",
    "--mock_fixture",
    "--mode",
    "--model",
    "--model_api_client_type",
    "--output-format",
    "--override_business_oauth_client_id",
    "--override_business_oauth_client_secret",
    "--override_ide_name",
    "--override_ide_version",
    "--override_logo_for_test",
    "--override_oauth_client_id",
    "--override_oauth_client_secret",
    "--override_user_agent_name",
    "-p",
    "--print",
    "--prompt",
    "--print-timeout",
    "--parent_pipe_path",
    "--profile",
    "--project",
    "--read_dir_latency_ms",
    "--read_file_latency_ms",
    "--release_base_url",
    "--remote-control-name",
    "--request_criticality",
    "--settings",
    "--stat_latency_ms",
    "--workspace_id",
})

_FLAVOR_LIST_FIELDS = (
    "candidate_bins",
    "exclude_path_substrings",
    "match_basenames",
    "match_path_substrings",
    "match_app_data_basenames",
)

_FLAVOR_SCALAR_FIELDS = ("env_bin", "data_dir_name")


def _dedupe(items: tuple[str, ...]) -> tuple[str, ...]:
  seen: set[str] = set()
  out: list[str] = []
  for item in items:
    if item not in seen:
      seen.add(item)
      out.append(item)
  return tuple(out)


def _as_str_tuple(raw: Any, field_name: str) -> tuple[str, ...]:
  if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
    raise ConfigError(f"{field_name!r} must be a list of strings")
  return tuple(raw)


def _merge_flavor(
    name: str,
    base: FlavorConfig | None,
    override: Mapping[str, Any],
) -> FlavorConfig:
  if not isinstance(override, Mapping):
    raise ConfigError(f"Flavor {name!r} must be a JSON object")

  values: dict[str, Any] = {
      "name": name,
      "env_bin": base.env_bin if base else "",
      "candidate_bins": base.candidate_bins if base else (),
      "exclude_path_substrings": base.exclude_path_substrings if base else (),
      "data_dir_name": base.data_dir_name if base else "",
      "match_basenames": base.match_basenames if base else (),
      "match_path_substrings": base.match_path_substrings if base else (),
      "match_app_data_basenames": (
          base.match_app_data_basenames if base else ()
      ),
  }

  for key in _FLAVOR_SCALAR_FIELDS:
    if key in override:
      val = override[key]
      if not isinstance(val, str):
        raise ConfigError(f"Flavor {name!r} field {key!r} must be a string")
      values[key] = val

  for key in _FLAVOR_LIST_FIELDS:
    if key in override:
      values[key] = _dedupe(
          _as_str_tuple(override[key], f"flavors.{name}.{key}")
      )
    extra_key = f"extra_{key}"
    if extra_key in override:
      extra = _as_str_tuple(override[extra_key], f"flavors.{name}.{extra_key}")
      values[key] = _dedupe(values[key] + extra)

  return FlavorConfig(**values)


def default_config_paths(
    env: Mapping[str, str] | None = None,
) -> list[pathlib.Path]:
  """Returns candidate config file paths in priority order."""
  env_map = os.environ if env is None else env
  explicit = env_map.get("CODE_HARNESS_CONFIG")
  if explicit:
    return [pathlib.Path(explicit).expanduser()]

  xdg_home = env_map.get("XDG_CONFIG_HOME")
  xdg_base = (
      pathlib.Path(xdg_home).expanduser()
      if xdg_home
      else pathlib.Path.home() / ".config"
  )
  return [
      xdg_base / "code-harness" / "config.json",
      pathlib.Path.home() / ".code-harness" / "config.json",
  ]


def load_config(
    config_path: pathlib.Path | str | None = None,
    env: Mapping[str, str] | None = None,
) -> HarnessConfig:
  """Loads and merges user config on top of built-in agy defaults."""
  flavors: dict[str, FlavorConfig] = {"agy": BUILTIN_AGY_FLAVOR}
  default_flavor = "agy"
  env_bin = "CODE_HARNESS_BIN"
  env_data_dir = "CODE_HARNESS_DATA_DIR"
  non_tui: set[str] = set(BUILTIN_NON_TUI_SUBCOMMANDS)
  takes_value: set[str] = set(BUILTIN_CLI_TAKES_VALUE)

  selected_path: pathlib.Path | None = None
  if config_path is not None:
    selected_path = pathlib.Path(config_path).expanduser()
    if not selected_path.is_file():
      raise ConfigError(f"Config file not found: {selected_path}")
  else:
    for candidate in default_config_paths(env=env):
      if candidate.is_file():
        selected_path = candidate
        break

  if selected_path is not None:
    try:
      raw = json.loads(selected_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
      raise ConfigError(f"Failed to read {selected_path}: {exc}") from exc
    if not isinstance(raw, Mapping):
      raise ConfigError(f"{selected_path} must contain a JSON object")

    raw_flavors = raw.get("flavors")
    if raw_flavors is not None:
      if not isinstance(raw_flavors, Mapping):
        raise ConfigError("'flavors' must be a JSON object")
      for name, spec in raw_flavors.items():
        if not isinstance(name, str) or not name:
          raise ConfigError("Flavor names must be non-empty strings")
        flavors[name] = _merge_flavor(name, flavors.get(name), spec)

    if "default_flavor" in raw:
      val = raw["default_flavor"]
      if not isinstance(val, str) or not val:
        raise ConfigError("'default_flavor' must be a non-empty string")
      default_flavor = val

    if "env_bin" in raw:
      val = raw["env_bin"]
      if not isinstance(val, str):
        raise ConfigError("'env_bin' must be a string")
      env_bin = val

    if "env_data_dir" in raw:
      val = raw["env_data_dir"]
      if not isinstance(val, str):
        raise ConfigError("'env_data_dir' must be a string")
      env_data_dir = val

    if "non_tui_subcommands" in raw:
      non_tui = set(
          _as_str_tuple(raw["non_tui_subcommands"], "non_tui_subcommands")
      )
    if "extra_non_tui_subcommands" in raw:
      non_tui.update(
          _as_str_tuple(
              raw["extra_non_tui_subcommands"], "extra_non_tui_subcommands"
          )
      )

    if "cli_takes_value" in raw:
      takes_value = set(
          _as_str_tuple(raw["cli_takes_value"], "cli_takes_value")
      )
    if "extra_cli_takes_value" in raw:
      takes_value.update(
          _as_str_tuple(raw["extra_cli_takes_value"], "extra_cli_takes_value")
      )

  if default_flavor not in flavors:
    known = ", ".join(sorted(flavors))
    raise ConfigError(
        f"default_flavor {default_flavor!r} is not defined; known: {known}"
    )

  return HarnessConfig(
      default_flavor=default_flavor,
      env_bin=env_bin,
      env_data_dir=env_data_dir,
      flavors=flavors,
      non_tui_subcommands=frozenset(non_tui),
      cli_takes_value=frozenset(takes_value),
  )


def is_excluded_path(path: str, exclude_substrings: tuple[str, ...]) -> bool:
  if not path or not exclude_substrings:
    return False
  paths = [path]
  try:
    resolved = os.path.realpath(path)
    if resolved != path:
      paths.append(resolved)
  except OSError:
    pass
  for candidate in paths:
    low = candidate.lower()
    if any(sub.lower() in low for sub in exclude_substrings):
      return True
  return False


def ordered_flavors(config: HarnessConfig) -> tuple[FlavorConfig, ...]:
  """Returns configured flavors with `default_flavor` first."""
  out: list[FlavorConfig] = []
  if config.default_flavor in config.flavors:
    out.append(config.flavors[config.default_flavor])
  for name, flavor_cfg in config.flavors.items():
    if name != config.default_flavor:
      out.append(flavor_cfg)
  return tuple(out)


def default_app_data_dirs(config: HarnessConfig) -> tuple[str, ...]:
  """Returns default `~/.gemini/<data_dir_name>` paths for configured flavors."""
  dirs: list[str] = []
  for flavor_cfg in ordered_flavors(config):
    if flavor_cfg.data_dir_name:
      candidate = os.path.expanduser(f"~/.gemini/{flavor_cfg.data_dir_name}")
      if candidate not in dirs:
        dirs.append(candidate)
  if not dirs:
    dirs.append(os.path.expanduser("~/.gemini/antigravity-cli"))
  return tuple(dirs)


def resolve_cli_bin(
    config: HarnessConfig,
    flavor: str | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
  """Resolves the executable path for the selected flavor."""
  env_map = os.environ if env is None else env
  if config.env_bin:
    global_override = env_map.get(config.env_bin)
    if global_override:
      return global_override

  flavor_cfg = config.get_flavor(flavor=flavor, env=env_map)
  if flavor_cfg.env_bin:
    flavor_override = env_map.get(flavor_cfg.env_bin)
    if flavor_override:
      return flavor_override

  path_env = env_map.get("PATH")
  for candidate in flavor_cfg.candidate_bins:
    if "/" in candidate or candidate.startswith("~"):
      expanded = os.path.expanduser(candidate)
      if os.path.isfile(expanded) and os.access(expanded, os.X_OK):
        if not is_excluded_path(expanded, flavor_cfg.exclude_path_substrings):
          return expanded
    else:
      found = shutil.which(candidate, path=path_env)
      if found and not is_excluded_path(
          found, flavor_cfg.exclude_path_substrings
      ):
        return found

  tried = ", ".join(flavor_cfg.candidate_bins) or "(none)"
  raise CliNotFoundError(
      f"No executable found for flavor {flavor_cfg.name!r} (tried: {tried})"
  )
