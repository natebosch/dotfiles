"""Full-screen tmux overview of sessions, ~/projects, ~/repos, and agent CLIs.

Combines project/repo/session switching with live coding-agent CLI state
inspection (`code_harness.snapshot`) and per-flavor context warning tiers
(`code_harness.context`).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Mapping

from code_harness import context as ctx_lib
from code_harness import snapshot as snap_lib
from code_harness.config import HarnessConfig

SEP = "\x1f"

# ANSI helpers (256-color + attributes).
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"


def fg256(code: int, bold: bool = False) -> str:
  prefix = "1;" if bold else ""
  return f"\033[{prefix}38;5;{code}m"


C_PERM = fg256(203, bold=True)   # coral red
C_ASK = fg256(220, bold=True)    # bright gold
C_PLAN = fg256(206, bold=True)   # magenta
C_DONE = fg256(78, bold=True)    # bright green
C_WORK = fg256(75, bold=False)   # sky blue
C_NEW = fg256(39, bold=False)    # cyan
C_MUTED = fg256(242, bold=False)
C_SUBTLE = fg256(246, bold=False)
C_TMUX_ON = fg256(33, bold=True)
C_PROJ = fg256(39, bold=False)
C_REPO = fg256(214, bold=False)
C_SESS = fg256(141, bold=False)
C_WARN = fg256(214, bold=True)   # amber (tier 1)
C_HIGH = fg256(196, bold=True)   # red (tier 2)
C_CUR = fg256(214, bold=True)

PRIO_NONE = 7
PRIO_PROJ_WORK = 8
PRIO_PROJ_DONE = 9
PRIO_UNSTARTED = 10

FILTER_MODES = ("all", "attention", "active")

PROJECT_STATUS_RE = re.compile(r"^Status:\s*(.+?)\s*$")
PROJECT_NEXT_RE = re.compile(r"^Next:\s*(.+?)\s*$")
PROJECT_CONV_RE = re.compile(
    r"^-\s*Conversation:\s*([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})(?:[,\s]+(.*))?$"
)


def find_fzf_bin() -> str:
  nix_fzf = os.path.expanduser("~/.nix-profile/bin/fzf")
  if os.access(nix_fzf, os.X_OK):
    return nix_fzf
  return shutil.which("fzf") or "fzf"


def read_git_branch(dir_path: str) -> str:
  """Reads git branch directly from `.git/HEAD` (including linked worktrees)."""
  git_entry = os.path.join(dir_path, ".git")
  git_dir = None
  if os.path.isdir(git_entry):
    git_dir = git_entry
  elif os.path.isfile(git_entry):
    try:
      with open(git_entry, encoding="utf-8", errors="replace") as f:
        first_line = f.readline().strip()
      if first_line.startswith("gitdir:"):
        raw_target = first_line[len("gitdir:") :].strip()
        git_dir = (
            raw_target
            if os.path.isabs(raw_target)
            else os.path.normpath(os.path.join(dir_path, raw_target))
        )
    except OSError:
      return ""
  if not git_dir:
    return ""

  head_path = os.path.join(git_dir, "HEAD")
  try:
    with open(head_path, encoding="utf-8", errors="replace") as f:
      head = f.readline().strip()
  except OSError:
    return ""
  if head.startswith("ref: refs/heads/"):
    return head[len("ref: refs/heads/") :]
  if head.startswith("ref: "):
    return head[len("ref: ") :]
  if len(head) >= 7 and all(c in "0123456789abcdefABCDEF" for c in head):
    return head[:8]
  return ""


def parse_project_md(md_path: str) -> dict[str, Any]:
  """Extracts status, next action, and linked conversation IDs from PROJECT.md."""
  info: dict[str, Any] = {
      "status": "",
      "next": "",
      "conversations": [],
      "body": "",
  }
  if not os.path.isfile(md_path):
    return info
  try:
    with open(md_path, encoding="utf-8", errors="replace") as f:
      body = f.read()
  except OSError:
    return info
  info["body"] = body

  lines = body.splitlines()
  i = 0
  while i < len(lines):
    line = lines[i]
    m_st = PROJECT_STATUS_RE.match(line)
    if m_st and not info["status"]:
      info["status"] = m_st.group(1).strip()
      i += 1
      continue

    m_nx = PROJECT_NEXT_RE.match(line)
    if m_nx and not info["next"]:
      parts = [m_nx.group(1).strip()]
      i += 1
      while i < len(lines):
        cont = lines[i].strip()
        if (
            not cont
            or cont.startswith(("#", "- "))
            or re.match(r"^[A-Z][A-Za-z0-9_-]*:\s", cont)
        ):
          break
        parts.append(cont)
        i += 1
      info["next"] = " ".join(parts)
      continue

    m_cv = PROJECT_CONV_RE.match(line.strip())
    if m_cv:
      cid = m_cv.group(1)
      note_parts = [m_cv.group(2).strip()] if m_cv.group(2) else []
      i += 1
      while i < len(lines):
        raw_next = lines[i]
        if not raw_next.startswith("  ") or raw_next.lstrip().startswith("- "):
          break
        cont = raw_next.strip()
        if not cont:
          break
        note_parts.append(cont)
        i += 1
      info["conversations"].append({
          "id": cid,
          "note": " ".join(p for p in note_parts if p),
      })
      continue

    i += 1
  return info


def discover_projects(projects_dir: str) -> dict[str, dict[str, Any]]:
  """Returns `{project_name: project_dict}` for `~/projects/*`."""
  out: dict[str, dict[str, Any]] = {}
  if not os.path.isdir(projects_dir):
    return out
  try:
    entries = sorted(os.listdir(projects_dir))
  except OSError:
    return out

  for name in entries:
    if name.startswith("."):
      continue
    pdir = os.path.join(projects_dir, name)
    if not os.path.isdir(pdir):
      continue
    md = parse_project_md(os.path.join(pdir, "PROJECT.md"))
    worktrees: list[dict[str, str]] = []
    direct_br = read_git_branch(pdir)
    if direct_br:
      worktrees.append({"name": name, "path": pdir, "branch": direct_br})
    try:
      sub_entries = sorted(os.listdir(pdir))
    except OSError:
      sub_entries = []
    for sub in sub_entries:
      if sub.startswith("."):
        continue
      sdir = os.path.join(pdir, sub)
      if not os.path.isdir(sdir):
        continue
      br = read_git_branch(sdir)
      if br:
        worktrees.append({"name": sub, "path": sdir, "branch": br})

    out[name] = {
        "name": name,
        "path": pdir,
        "launch_dir": pdir,
        "status": md["status"],
        "next": md["next"],
        "conversations": md["conversations"],
        "project_md": md["body"],
        "worktrees": worktrees,
    }
  return out


def discover_repos(repos_dir: str) -> dict[str, dict[str, Any]]:
  """Returns `{repo_name: repo_dict}` for `~/repos/*`."""
  out: dict[str, dict[str, Any]] = {}
  if not os.path.isdir(repos_dir):
    return out
  try:
    entries = sorted(os.listdir(repos_dir))
  except OSError:
    return out

  for name in entries:
    if name.startswith("."):
      continue
    rdir = os.path.join(repos_dir, name)
    if not os.path.isdir(rdir):
      continue
    launch_dir = rdir
    branch = read_git_branch(rdir)
    if not branch and not os.path.exists(os.path.join(rdir, ".git")):
      try:
        subdirs = [
            d
            for d in os.listdir(rdir)
            if not d.startswith(".")
            and os.path.isdir(os.path.join(rdir, d))
        ]
      except OSError:
        subdirs = []
      if len(subdirs) == 1:
        nested = os.path.join(rdir, subdirs[0])
        nested_br = read_git_branch(nested)
        if nested_br or os.path.exists(os.path.join(nested, ".git")):
          launch_dir = nested
          branch = nested_br
    out[name] = {
        "name": name,
        "path": rdir,
        "launch_dir": launch_dir,
        "branch": branch,
    }
  return out


def format_worktree_branches(worktrees: list[Mapping[str, str]]) -> str:
  if not worktrees:
    return ""
  if len(worktrees) == 1:
    wt = worktrees[0]
    return f"({wt['name']}:{wt['branch']})"
  shown = [f"{w['name']}:{w['branch']}" for w in worktrees[:2]]
  extra = len(worktrees) - 2
  if extra > 0:
    shown.append(f"+{extra}")
  return "(" + ", ".join(shown) + ")"


def query_tmux_zoom_and_client(
    tmux: snap_lib.TmuxRunner,
    client_tty: str = "",
) -> tuple[dict[tuple[str, int], tuple[bool, int]], str]:
  """Returns `({(sname, widx): (zoomed, num_panes)}, current_session_name)`."""
  win_meta: dict[tuple[str, int], tuple[bool, int]] = {}
  w_res = tmux.run(
      "list-windows",
      "-a",
      "-F",
      f"#{{session_name}}{SEP}#{{window_index}}{SEP}#{{window_zoomed_flag}}{SEP}#{{window_panes}}",
      check=False,
  )
  if w_res.returncode == 0:
    for line in w_res.stdout.splitlines():
      parts = line.split(SEP)
      if len(parts) != 4:
        continue
      sname, widx_s, zflag, npanes_s = parts
      try:
        win_meta[(sname, int(widx_s))] = (
            zflag == "1",
            int(npanes_s or "1"),
        )
      except ValueError:
        continue

  cur_session = ""
  if client_tty:
    d_res = tmux.run(
        "display-message",
        "-t",
        client_tty,
        "-p",
        "#{session_name}",
        check=False,
    )
    if d_res.returncode == 0 and d_res.stdout.strip():
      cur_session = d_res.stdout.strip()

  if not cur_session:
    my_pane = os.environ.get("TMUX_PANE") or ""
    if my_pane:
      d_res = tmux.run(
          "display-message",
          "-t",
          my_pane,
          "-p",
          "#{session_name}",
          check=False,
      )
      if d_res.returncode == 0 and d_res.stdout.strip():
        cur_session = d_res.stdout.strip()

  if not cur_session:
    c_res = tmux.run(
        "list-clients",
        "-F",
        f"#{{client_session}}{SEP}#{{client_activity}}",
        check=False,
    )
    if c_res.returncode == 0 and c_res.stdout.strip():
      best = (-1, "")
      for line in c_res.stdout.splitlines():
        parts = line.split(SEP)
        if len(parts) == 2:
          try:
            act = int(parts[1] or "0")
          except ValueError:
            act = 0
          if act > best[0]:
            best = (act, parts[0])
      cur_session = best[1]

  return win_meta, cur_session


def strip_self_overview_window(
    sessions: list[dict[str, Any]],
    procs: Mapping[int, dict[str, Any]],
) -> None:
  """Drops our own transient `new-window code-harness overview` window."""
  my_pane = os.environ.get("TMUX_PANE") or ""
  if not my_pane:
    return
  my_pid = os.getpid()
  for sess in sessions:
    drop_indices: list[int] = []
    for widx in sess["window_order"]:
      win = sess["windows"][widx]
      if len(win["panes"]) != 1:
        continue
      pane = win["panes"][0]
      if pane["pane_id"] != my_pane:
        continue
      ppid = pane["pane_pid"]
      pinfo = procs.get(ppid)
      if ppid == my_pid or (
          pinfo and not snap_lib.is_interactive_shell_proc(pinfo)
      ):
        drop_indices.append(widx)
    for widx in drop_indices:
      sess["window_order"].remove(widx)
      del sess["windows"][widx]


def classify_ui_state(agent_dict: Mapping[str, Any] | None) -> tuple[str, int]:
  """Maps `pane['agent']` into `(ui_state, priority_rank)`."""
  if not agent_dict:
    return "none", PRIO_NONE
  st = agent_dict["state"]
  reason = agent_dict.get("reason") or ""
  cid = agent_dict.get("conversation_id")
  cat = snap_lib.classify_ui_category(st, reason, cid)
  prio_map = {
      "perm": 1,
      "ask": 2,
      "plan": 3,
      "done": 4,
      "work": 5,
      "new": 6,
  }
  return cat, prio_map[cat]


def collect_overview_data(
    config: HarnessConfig,
    tmux: snap_lib.TmuxRunner,
    client_tty: str = "",
    flavor: str | None = None,
) -> dict[str, Any]:
  """Scans tmux, ~/projects, ~/repos, titles, and context limits."""
  home = os.path.expanduser("~")
  projects_dir = os.environ.get("CODE_HARNESS_PROJECTS_DIR") or os.path.join(
      home, "projects"
  )
  repos_dir = os.environ.get("CODE_HARNESS_REPOS_DIR") or os.path.join(
      home, "repos"
  )
  data_dir = snap_lib.default_data_dir(config=config)

  default_limits = ctx_lib.load_context_limits(config=config, flavor=flavor)

  projects = discover_projects(projects_dir)
  repos = discover_repos(repos_dir)

  procs, children_map = snap_lib.load_process_table(config=config)
  sessions = snap_lib.scan_tmux(
      tmux, procs, children_map, data_dir, config=config
  )
  strip_self_overview_window(sessions, procs)
  win_meta, cur_session = query_tmux_zoom_and_client(tmux, client_tty)

  # Match active sessions to ~/projects or ~/repos.
  claimed_projects: set[str] = set()
  claimed_repos: set[str] = set()
  session_origin: dict[str, tuple[str, str]] = {}

  for sess in sessions:
    sname = sess["name"]
    penv = sess.get("project_env") or ""
    matched: tuple[str, str] | None = None
    if penv and penv in projects and penv not in claimed_projects:
      matched = ("P", penv)
      claimed_projects.add(penv)
    else:
      for pname in projects:
        if pname not in claimed_projects and pname.replace(".", "_") == sname:
          matched = ("P", pname)
          claimed_projects.add(pname)
          break
      if matched is None:
        for rname in repos:
          if rname not in claimed_repos and rname.replace(".", "_") == sname:
            matched = ("R", rname)
            claimed_repos.add(rname)
            break
    session_origin[sname] = matched or ("S", sname)

  # Gather all conversation IDs (live CLIs + inactive projects' primary conv)
  # so we batch-query titles once.
  conv_ids_needed: list[str] = []
  for sess in sessions:
    for widx in sess["window_order"]:
      for p in sess["windows"][widx]["panes"]:
        if p["agent"] and p["agent"]["conversation_id"]:
          conv_ids_needed.append(p["agent"]["conversation_id"])
  for pinfo in projects.values():
    if pinfo["conversations"]:
      conv_ids_needed.append(pinfo["conversations"][0]["id"])

  titles_by_id = snap_lib.conversation_labels(
      data_dir, conv_ids_needed, max_len=160, config=config
  )

  def eval_ctx(
      cid: str,
      pane_data_dir: str | None = None,
      pane_flavor: str | None = None,
  ) -> dict[str, Any]:
    eff_flavor = flavor if flavor is not None else pane_flavor
    row_limits = (
        ctx_lib.load_context_limits(config=config, flavor=eff_flavor)
        if eff_flavor is not None
        else default_limits
    )
    if not cid:
      return ctx_lib.evaluate_context_size(0, limits=row_limits, config=config)
    return ctx_lib.inspect_conversation_context(
        cid,
        data_dir=pane_data_dir or data_dir,
        limits=row_limits,
        config=config,
        flavor=eff_flavor,
    )

  rows: list[dict[str, Any]] = []
  row_seq = 0

  def next_key() -> str:
    nonlocal row_seq
    row_seq += 1
    return f"r{row_seq}"

  def build_session_windows_summary(sess: Mapping[str, Any]) -> list[dict]:
    out_wins: list[dict[str, Any]] = []
    for widx in sess["window_order"]:
      win = sess["windows"][widx]
      zoomed, npanes = win_meta.get(
          (sess["name"], widx), (False, len(win["panes"]))
      )
      pane_items: list[dict[str, Any]] = []
      for p in win["panes"]:
        ag = p["agent"]
        if ag is not None:
          ui_st, _ = classify_ui_state(ag)
          cid = ag["conversation_id"]
          pane_items.append({
              "index": p["index"],
              "pane_id": p["pane_id"],
              "cwd": snap_lib.short_home(p["cwd"]),
              "kind": "agent",
              "ui_state": ui_st,
              "reason": ag["reason"],
              "conversation_id": cid or "",
              "title": titles_by_id.get(cid or "", ag.get("label") or ""),
          })
        else:
          pane_items.append({
              "index": p["index"],
              "pane_id": p["pane_id"],
              "cwd": snap_lib.short_home(p["cwd"]),
              "kind": "shell",
              "busy": p["busy_non_agent"],
          })
      out_wins.append({
          "index": widx,
          "name": win["name"],
          "active": win["active"],
          "zoomed": zoomed,
          "num_panes": npanes,
          "panes": pane_items,
      })
    return out_wins

  # 1. Active tmux sessions.
  for sess in sessions:
    sname = sess["name"]
    kind, origin_name = session_origin[sname]
    proj_info = projects.get(origin_name) if kind == "P" else None
    repo_info = repos.get(origin_name) if kind == "R" else None
    is_cur = sname == cur_session

    branch_str = ""
    launch_dir = home
    if proj_info:
      branch_str = format_worktree_branches(proj_info["worktrees"])
      launch_dir = proj_info["launch_dir"]
    elif repo_info:
      branch_str = f"({repo_info['branch']})" if repo_info["branch"] else ""
      launch_dir = repo_info["launch_dir"]
    else:
      # Check first pane's git branch for ad-hoc sessions.
      if sess["window_order"]:
        first_win = sess["windows"][sess["window_order"][0]]
        if first_win["panes"]:
          launch_dir = first_win["panes"][0]["cwd"]
          b = read_git_branch(launch_dir)
          if b:
            branch_str = f"({b})"

    agent_panes = [
        p
        for widx in sess["window_order"]
        for p in sess["windows"][widx]["panes"]
        if p["agent"] is not None
    ]
    win_summaries = build_session_windows_summary(sess)

    if not agent_panes:
      n_wins = len(sess["window_order"])
      busy_cmds = [
          cmd.split(" (pid")[0]
          for widx in sess["window_order"]
          for p in sess["windows"][widx]["panes"]
          for cmd in p["busy_non_agent"]
      ]
      linked_cid = ""
      linked_title = ""
      ctx_info = eval_ctx("")
      if proj_info and proj_info["conversations"]:
        linked_cid = proj_info["conversations"][0]["id"]
        linked_title = (
            titles_by_id.get(linked_cid)
            or proj_info["conversations"][0]["note"]
        )
        ctx_info = eval_ctx(linked_cid)

      if proj_info and proj_info["next"]:
        detail_main = proj_info["next"]
      elif busy_cmds:
        uniq = list(dict.fromkeys(busy_cmds))
        detail_main = f"{n_wins} win · {', '.join(uniq[:3])}"
      elif n_wins > 0:
        w_label = "window" if n_wins == 1 else "windows"
        detail_main = f"{n_wins} {w_label}"
      else:
        detail_main = ""

      rows.append({
          "key": next_key(),
          "priority": PRIO_NONE,
          "ui_state": "none",
          "tmux_active": True,
          "kind": kind,
          "name": origin_name if kind in ("P", "R") else sname,
          "session_name": sname,
          "target_display": sname,
          "is_current_session": is_cur,
          "window_index": None,
          "window_zoomed": False,
          "window_num_panes": 1,
          "pane_id": None,
          "launch_dir": launch_dir,
          "conversation_id": linked_cid,
          "conversation_live": False,
          "conversation_title": linked_title,
          "state_reason": "no active agent CLI in session",
          "ctx": ctx_info,
          "detail_main": detail_main,
          "branch_str": branch_str,
          "project": proj_info,
          "windows": win_summaries,
      })
    else:
      multi_cli = len(agent_panes) > 1
      win_counts: dict[int, int] = {}
      for ap in agent_panes:
        win_counts[ap["window_index"]] = (
            win_counts.get(ap["window_index"], 0) + 1
        )

      for ap in agent_panes:
        ag = ap["agent"]
        ui_st, prio = classify_ui_state(ag)
        cid = ag["conversation_id"] or ""
        title = (
            titles_by_id.get(cid)
            or ag.get("label")
            or ("(new prompt)" if not cid else cid[:8])
        )
        pane_data_dir = ag.get("data_dir") or data_dir
        pane_flavor = ag.get("flavor")
        ctx_info = eval_ctx(
            cid, pane_data_dir=pane_data_dir, pane_flavor=pane_flavor
        )
        widx = ap["window_index"]
        zoomed, npanes = win_meta.get((sname, widx), (False, 1))
        if not multi_cli:
          target_disp = sname
        elif win_counts.get(widx, 0) > 1:
          target_disp = f"{sname} :{widx}.{ap['index']}"
        else:
          target_disp = f"{sname} :{widx}"

        # Pane-specific project & git branch if inside a specific worktree.
        pane_proj = proj_info
        if pane_proj is None:
          pane_proj = next(
              (
                  pinfo
                  for pinfo in projects.values()
                  if ap["cwd"] == pinfo["path"]
                  or ap["cwd"].startswith(pinfo["path"] + "/")
              ),
              None,
          )
        pane_branch = read_git_branch(ap["cwd"])
        if pane_proj is not None and proj_info is None:
          eff_branch = (
              f"({pane_proj['name']}:{pane_branch})"
              if pane_branch
              else f"({pane_proj['name']})"
          )
        else:
          eff_branch = f"({pane_branch})" if pane_branch else branch_str

        rows.append({
            "key": next_key(),
            "priority": prio,
            "ui_state": ui_st,
            "tmux_active": True,
            "kind": kind,
            "name": origin_name if kind in ("P", "R") else sname,
            "session_name": sname,
            "target_display": target_disp,
            "is_current_session": is_cur,
            "window_index": widx,
            "window_zoomed": zoomed,
            "window_num_panes": npanes,
            "pane_id": ap["pane_id"],
            "launch_dir": launch_dir,
            "conversation_id": cid,
            "data_dir": pane_data_dir,
            "conversation_live": True,
            "conversation_title": title,
            "state_reason": ag["reason"],
            "ctx": ctx_info,
            "detail_main": snap_lib._squash_line(title, 72),
            "branch_str": eff_branch,
            "project": pane_proj,
            "windows": win_summaries,
        })

  # Fallback catch-all session "0" when no tmux server/sessions are running.
  if not sessions:
    rows.append({
        "key": next_key(),
        "priority": PRIO_NONE,
        "ui_state": "none",
        "tmux_active": False,
        "kind": "S",
        "name": "0",
        "session_name": "0",
        "target_display": "0",
        "is_current_session": False,
        "window_index": None,
        "window_zoomed": False,
        "window_num_panes": 1,
        "pane_id": None,
        "launch_dir": home,
        "conversation_id": "",
        "conversation_live": False,
        "conversation_title": "",
        "state_reason": "",
        "ctx": eval_ctx(""),
        "detail_main": "default home session",
        "branch_str": "",
        "project": None,
        "windows": [],
    })

  # 2. Inactive ~/projects/*.
  for pname, pinfo in projects.items():
    if pname in claimed_projects:
      continue
    st_word = (pinfo["status"].split() or [""])[0].lower()
    prio = PRIO_PROJ_DONE if st_word == "done" else PRIO_PROJ_WORK
    linked_cid = ""
    linked_title = ""
    if pinfo["conversations"]:
      linked_cid = pinfo["conversations"][0]["id"]
      linked_title = (
          titles_by_id.get(linked_cid) or pinfo["conversations"][0]["note"]
      )
    ctx_info = eval_ctx(linked_cid)
    detail_main = snap_lib._squash_line(linked_title or pinfo["next"], 64)
    rows.append({
        "key": next_key(),
        "priority": prio,
        "ui_state": "none",
        "tmux_active": False,
        "kind": "P",
        "name": pname,
        "session_name": pname.replace(".", "_"),
        "target_display": pname,
        "is_current_session": False,
        "window_index": None,
        "window_zoomed": False,
        "window_num_panes": 1,
        "pane_id": None,
        "launch_dir": pinfo["launch_dir"],
        "conversation_id": linked_cid,
        "conversation_live": False,
        "conversation_title": linked_title,
        "state_reason": "no tmux session running",
        "ctx": ctx_info,
        "detail_main": f"[offline] {detail_main}" if detail_main else "",
        "branch_str": format_worktree_branches(pinfo["worktrees"]),
        "project": pinfo,
        "windows": [],
    })

  # 3. Inactive ~/repos/*.
  for rname, rinfo in repos.items():
    if rname in claimed_repos:
      continue
    rows.append({
        "key": next_key(),
        "priority": PRIO_UNSTARTED,
        "ui_state": "none",
        "tmux_active": False,
        "kind": "R",
        "name": rname,
        "session_name": rname.replace(".", "_"),
        "target_display": rname,
        "is_current_session": False,
        "window_index": None,
        "window_zoomed": False,
        "window_num_panes": 1,
        "pane_id": None,
        "launch_dir": rinfo["launch_dir"],
        "conversation_id": "",
        "conversation_live": False,
        "conversation_title": "",
        "state_reason": "no tmux session running",
        "ctx": eval_ctx(""),
        "detail_main": "",
        "branch_str": f"({rinfo['branch']})" if rinfo["branch"] else "",
        "project": None,
        "windows": [],
    })

  rows.sort(
      key=lambda r: (
          r["priority"],
          r["target_display"].lower(),
          r["window_index"] if r["window_index"] is not None else -1,
      )
  )
  for idx, r in enumerate(rows, start=1):
    r["key"] = f"r{idx}"

  limits_enabled = default_limits is not None or any(
      r["ctx"].get("enabled") for r in rows
  )

  return {
      "limits": default_limits,
      "limits_enabled": limits_enabled,
      "current_session": cur_session,
      "data_dir": data_dir,
      "rows": rows,
  }


def format_state_col(ui_state: str) -> str:
  if ui_state == "perm":
    return f"{C_PERM}! PERM{RESET}"
  if ui_state == "ask":
    return f"{C_ASK}? ASK {RESET}"
  if ui_state == "plan":
    return f"{C_PLAN}◆ PLAN{RESET}"
  if ui_state == "done":
    return f"{C_DONE}● DONE{RESET}"
  if ui_state == "work":
    return f"{C_WORK}◐ WORK{RESET}"
  if ui_state == "new":
    return f"{C_NEW}○ NEW {RESET}"
  return f"{C_MUTED}  —   {RESET}"


def format_tmux_col(tmux_active: bool, kind: str) -> str:
  dot = f"{C_TMUX_ON}●{RESET}" if tmux_active else f"{C_MUTED}○{RESET}"
  if kind == "P":
    k_col = f"{C_PROJ}P{RESET}"
  elif kind == "R":
    k_col = f"{C_REPO}R{RESET}"
  else:
    k_col = f"{C_SESS}S{RESET}"
  return f"{dot} {k_col} "


def format_ctx_col(ctx: Mapping[str, Any], live: bool) -> str:
  """6-char visible column when context limits are enabled."""
  if not ctx.get("enabled"):
    return f"{C_MUTED}   —  {RESET}"
  sev = ctx.get("severity") or "ok"
  fmt = ctx.get("formatted") or ""
  if not fmt:
    return f"{C_MUTED}   —  {RESET}"
  if sev == "high":
    txt = f"! {fmt:>4}"
    return f"{C_HIGH}{txt}{RESET}"
  if sev == "warn":
    txt = f"▲ {fmt:>4}"
    return f"{C_WARN}{txt}{RESET}"
  if not live:
    return f"{C_MUTED}  {fmt:>4}{RESET}"
  return f"{C_SUBTLE}  {fmt:>4}{RESET}"


def filter_rows(
    rows: list[dict[str, Any]], filter_mode: str
) -> list[dict[str, Any]]:
  if filter_mode == "attention":
    return [
        r for r in rows if r["ui_state"] in ("perm", "ask", "plan", "done")
    ]
  if filter_mode == "active":
    return [r for r in rows if r["conversation_live"]]
  return rows


def render_table_lines(
    data: Mapping[str, Any], filter_mode: str = "all"
) -> list[str]:
  """Renders line 1 (frozen header) + data lines with `\x1f` key prefix."""
  all_rows = data["rows"]
  limits_enabled = data["limits_enabled"]
  shown = filter_rows(all_rows, filter_mode)

  counts: dict[str, int] = {
      "perm": 0,
      "ask": 0,
      "plan": 0,
      "done": 0,
      "work": 0,
  }
  active_sessions: set[str] = set()
  for r in all_rows:
    if r["tmux_active"]:
      active_sessions.add(r["session_name"])
    if r["ui_state"] in counts:
      counts[r["ui_state"]] += 1

  summary_parts: list[str] = []
  if counts["perm"]:
    summary_parts.append(f"{C_PERM}{counts['perm']} perm{RESET}")
  if counts["ask"]:
    summary_parts.append(f"{C_ASK}{counts['ask']} ask{RESET}")
  if counts["plan"]:
    summary_parts.append(f"{C_PLAN}{counts['plan']} plan{RESET}")
  if counts["done"]:
    summary_parts.append(f"{C_DONE}{counts['done']} done{RESET}")
  if counts["work"]:
    summary_parts.append(f"{C_WORK}{counts['work']} work{RESET}")
  summary_parts.append(f"{len(active_sessions)} tmux")
  if filter_mode != "all":
    summary_parts.append(f"{C_CUR}[filter: {filter_mode}]{RESET}")
  summary_badge = " · ".join(summary_parts)

  max_target = max((len(r["target_display"]) for r in shown), default=16)
  target_w = min(max(max_target + 2, 18), 32)

  ctx_hdr = "CTX     " if limits_enabled else ""
  hdr_vis = (
      f"{BOLD}STATE   TMUX  {ctx_hdr}{'TARGET':<{target_w}}  DETAIL{RESET}"
      f"   {DIM}({RESET}{summary_badge}{DIM}){RESET}"
  )
  lines = [f"header{SEP}{hdr_vis}"]

  for r in shown:
    st_s = format_state_col(r["ui_state"])
    tm_s = format_tmux_col(r["tmux_active"], r["kind"])
    ctx_s = (
        format_ctx_col(r["ctx"], r["conversation_live"]) + "  "
        if limits_enabled
        else ""
    )

    t_raw = r["target_display"]
    if len(t_raw) > target_w - 2:
      t_trunc = t_raw[: target_w - 3] + "…"
    else:
      t_trunc = t_raw
    cur_mark = f"{C_CUR}*{RESET}" if r["is_current_session"] else " "
    pad = " " * max(1, target_w - len(t_trunc) - 1)
    if r["tmux_active"]:
      target_col = f"{BOLD}{t_trunc}{RESET}{cur_mark}{pad}"
    else:
      target_col = f"{t_trunc}{cur_mark}{pad}"

    detail_parts: list[str] = []
    dm = r["detail_main"]
    if dm:
      if not r["tmux_active"]:
        detail_parts.append(f"{C_SUBTLE}{dm}{RESET}")
      elif r["ui_state"] == "new":
        detail_parts.append(f"{C_SUBTLE}{dm}{RESET}")
      else:
        detail_parts.append(dm)
    if r["branch_str"]:
      detail_parts.append(f"{C_MUTED}{r['branch_str']}{RESET}")

    detail_col = "  ".join(detail_parts)
    vis = f"{st_s}  {tm_s}  {ctx_s}{target_col}  {detail_col}"
    lines.append(f"{r['key']}{SEP}{vis}")

  return lines


def extract_latest_snippet(
    data_dir: str | None,
    conv_id: str,
    max_lines: int = 10,
    config: HarnessConfig | None = None,
) -> str:
  """Extracts a brief preview of the latest assistant reply or pending prompt."""
  if not conv_id:
    return ""
  entries = snap_lib.read_last_transcript_entries(
      data_dir, conv_id, config=config
  )
  if not entries:
    return ""

  for entry in reversed(entries):
    if snap_lib._is_trailing_sdk_hook_entry(entry):
      continue
    tp = entry.get("type") or ""
    if tp == "PLANNER_RESPONSE":
      chunks: list[str] = []
      text = (entry.get("content") or "").strip()
      if text:
        chunks.append(text)
      for tc in entry.get("tool_calls") or []:
        tname = tc.get("name") or ""
        args = tc.get("args") or {}
        if tname == "ask_question":
          raw_q = args.get("questions") or ""
          chunks.append(f"[ask_question] {raw_q}")
        elif tname == "run_command":
          cmd_s = str(args.get("CommandLine") or "").strip('"')
          chunks.append(f"[run_command] {cmd_s}")
        elif tname:
          chunks.append(f"[tool: {tname}]")
      joined = "\n".join(chunks).strip()
      if not joined:
        continue
      out_lines = [ln.rstrip() for ln in joined.splitlines() if ln.strip()]
      if len(out_lines) > max_lines:
        out_lines = out_lines[:max_lines] + ["…"]
      return "\n".join(out_lines)
  return ""


def render_preview(
    state_dir: str,
    row_key: str,
    config: HarnessConfig | None = None,
) -> str:
  cache_path = os.path.join(state_dir, "cache.json")
  if not os.path.isfile(cache_path):
    return ""
  try:
    with open(cache_path, encoding="utf-8") as f:
      cache = json.load(f)
  except (OSError, ValueError):
    return ""

  row = next((r for r in cache.get("rows", []) if r["key"] == row_key), None)
  if row is None:
    return ""

  out: list[str] = []
  kind_label = {"P": "Project", "R": "Repository", "S": "Tmux Session"}[
      row["kind"]
  ]
  tmux_badge = (
      f"{C_TMUX_ON}● active ({row['session_name']}){RESET}"
      if row["tmux_active"]
      else f"{C_MUTED}○ no tmux session{RESET}"
  )
  out.append(f"{BOLD}{kind_label}:{RESET} {row['name']}   {tmux_badge}")
  out.append(f"{C_SUBTLE}Path:{RESET} {snap_lib.short_home(row['launch_dir'])}")

  proj = row.get("project")
  if proj and proj.get("worktrees"):
    wt_strs = [f"{w['name']} ({w['branch']})" for w in proj["worktrees"]]
    out.append(f"{C_SUBTLE}Worktrees:{RESET} {', '.join(wt_strs)}")
  elif row.get("branch_str"):
    out.append(f"{C_SUBTLE}Branch:{RESET} {row['branch_str'].strip('()')}")

  # Agent conversation section.
  if row["conversation_live"] or row["conversation_id"]:
    out.append("")
    hdr = (
        "Active Agent CLI"
        if row["conversation_live"]
        else "Linked Conversation (offline)"
    )
    out.append(f"{BOLD}── {hdr} ──{RESET}")
    if row["conversation_live"]:
      out.append(
          f"State:   {format_state_col(row['ui_state']).strip()}  "
          f"{C_SUBTLE}({row['state_reason']}){RESET}"
      )
      if row["window_index"] is not None:
        out.append(
            f"Pane:    {row['session_name']}:{row['window_index']} "
            f"({row['pane_id']})"
        )
    if row["conversation_id"]:
      out.append(f"ID:      {row['conversation_id']}")
    if row["conversation_title"]:
      out.append(f"Title:   {row['conversation_title']}")

    ctx = row.get("ctx") or {}
    if ctx.get("enabled") and ctx.get("tokens", 0) > 0:
      lims = ctx.get("limits") or cache.get("limits") or {}
      sev = ctx.get("severity")
      detail = ctx["formatted"]
      child_count = int(ctx.get("child_count") or 0)
      if child_count > 0:
        self_fmt = ctx.get("self_formatted") or "0"
        child_fmt = ctx.get("child_formatted") or "0"
        if child_count == 1:
          ctype = (ctx.get("children") or [{}])[0].get("type")
          label = (
              "worker"
              if ctype == "worker"
              else ("swarm" if ctype == "swarm" else "child")
          )
          detail = f"{detail}: {self_fmt} main + {child_fmt} {label}"
        else:
          detail = (
              f"{detail}: {self_fmt} main + {child_fmt} "
              f"across {child_count} children"
          )
      compactions = int(ctx.get("compactions") or 0)
      if compactions > 0:
        comp_word = "compaction" if compactions == 1 else "compactions"
        detail = f"{detail}, {compactions} {comp_word}"
      tok_s = f"{ctx['tokens']:,} tokens ({detail})"
      if sev == "high":
        out.append(
            f"Context: {C_HIGH}! HIGH — {tok_s}{RESET} "
            f"(high >= {lims.get('high', 0):,})"
        )
      elif sev == "warn":
        out.append(
            f"Context: {C_WARN}▲ WARN — {tok_s}{RESET} "
            f"(warn >= {lims.get('warn', 0):,})"
        )
      else:
        out.append(f"Context: {tok_s} (ok)")

    snippet = extract_latest_snippet(
        row.get("data_dir") or cache.get("data_dir") or "",
        row["conversation_id"],
        config=config,
    )
    if snippet:
      out.append("")
      out.append(f"{BOLD}Latest output:{RESET}")
      for ln in snippet.splitlines():
        out.append(f"  {C_SUBTLE}{ln}{RESET}")

  # Open tmux windows in this session.
  if row.get("windows"):
    out.append("")
    out.append(f"{BOLD}── Tmux Windows ({row['session_name']}) ──{RESET}")
    for w in row["windows"]:
      act_mark = "*" if w["active"] else " "
      z_mark = " [zoomed]" if w["zoomed"] else ""
      out.append(f"  {w['index']}{act_mark} {w['name']}{z_mark}")
      for p in w["panes"]:
        if p["kind"] == "agent":
          st_b = format_state_col(p["ui_state"]).strip()
          ttl = f" — {p['title']}" if p["title"] else ""
          out.append(f"      .{p['index']} {st_b}{ttl}")
        else:
          busy = f" ({', '.join(p['busy'])})" if p["busy"] else " (idle shell)"
          out.append(f"      .{p['index']} {p['cwd']}{C_MUTED}{busy}{RESET}")

  # PROJECT.md body if linked to a ~/projects directory.
  if proj and proj.get("project_md"):
    out.append("")
    out.append(f"{BOLD}── PROJECT.md ──{RESET}")
    for ln in proj["project_md"].rstrip().splitlines():
      if ln.startswith("#"):
        out.append(f"{BOLD}{ln}{RESET}")
      elif ln.startswith(("Status:", "Next:", "Related:")):
        k, _, rest = ln.partition(":")
        out.append(f"{C_PROJ}{k}:{RESET}{rest}")
      else:
        out.append(ln)

  return "\n".join(out) + "\n"


def write_cache(state_dir: str, data: Mapping[str, Any]) -> None:
  tmp = os.path.join(state_dir, f"cache.json.tmp.{os.getpid()}")
  target = os.path.join(state_dir, "cache.json")
  with open(tmp, "w", encoding="utf-8") as f:
    json.dump(data, f)
  os.replace(tmp, target)


def read_filter_mode(state_dir: str) -> str:
  mode_path = os.path.join(state_dir, "mode")
  try:
    with open(mode_path, encoding="utf-8") as f:
      m = f.read().strip()
    if m in FILTER_MODES:
      return m
  except OSError:
    pass
  return "all"


def cycle_filter_mode(state_dir: str) -> str:
  cur = read_filter_mode(state_dir)
  nxt = FILTER_MODES[(FILTER_MODES.index(cur) + 1) % len(FILTER_MODES)]
  with open(os.path.join(state_dir, "mode"), "w", encoding="utf-8") as f:
    f.write(nxt)
  return nxt


def activate_row(
    tmux: snap_lib.TmuxRunner,
    tmux_bin: str,
    row: Mapping[str, Any],
    session_only: bool = False,
    client_tty: str = "",
) -> int:
  """Switches to (or creates) the selected session and focuses target pane."""
  sname = row["session_name"]
  exact_s = f"={sname}"
  switch_args = ["-c", client_tty] if client_tty else []
  in_tmux = bool(os.environ.get("TMUX") or client_tty)

  has_res = tmux.run("has-session", "-t", exact_s, check=False)
  created = False
  if has_res.returncode != 0:
    created = True
    launch_dir = row["launch_dir"]
    if not os.path.isdir(launch_dir):
      launch_dir = os.path.expanduser("~")
    new_cmd = ["-2", "new-session", "-d", "-s", sname, "-c", launch_dir]
    if row["kind"] == "P":
      new_cmd.extend(["-e", f"PROJECT={row['name']}"])
    env = os.environ.copy()
    env["TMUX"] = ""
    subprocess.run(
        [*tmux.base, *new_cmd],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=True,
    )

  if (
      not created
      and not session_only
      and row["pane_id"]
      and row["window_index"] is not None
  ):
    q_sep = "\x1f"
    d_res = tmux.run(
        "display-message",
        "-t",
        row["pane_id"],
        "-p",
        q_sep.join([
            "#{session_name}",
            "#{window_index}",
            "#{window_zoomed_flag}",
            "#{window_panes}",
        ]),
        check=False,
    )
    pane_alive = True
    live_sname = sname
    live_widx = row["window_index"]
    live_zoomed = bool(row.get("window_zoomed"))
    live_panes = int(row.get("window_num_panes") or 1)
    if d_res.returncode == 0 and d_res.stdout.strip():
      d_parts = d_res.stdout.rstrip("\r\n").split(q_sep)
      if len(d_parts) == 4:
        live_sname = d_parts[0] or sname
        live_widx = d_parts[1] if d_parts[1] != "" else live_widx
        live_zoomed = d_parts[2] == "1"
        try:
          live_panes = int(d_parts[3] or "1")
        except ValueError:
          live_panes = 1
    elif d_res.returncode != 0:
      pane_alive = False

    if pane_alive:
      live_exact_s = f"={live_sname}"
      target_win = f"={live_sname}:{live_widx}"
      cmd = ["select-window", "-t", target_win]
      if live_zoomed and live_panes > 1:
        cmd += [";", "resize-pane", "-Z", "-t", target_win]
      cmd += [";", "select-pane", "-t", row["pane_id"]]
      if in_tmux and (live_sname != sname or not row["is_current_session"]):
        cmd += [";", "switch-client", *switch_args, "-t", live_exact_s]
      tmux.run(*cmd, check=False)
      if not in_tmux:
        os.execvp(
            tmux_bin,
            [*tmux.base, "-2", "attach-session", "-t", live_exact_s, "-d"],
        )
      return 0

  if in_tmux:
    if created or not row["is_current_session"]:
      tmux.run("switch-client", *switch_args, "-t", exact_s, check=False)
  else:
    os.execvp(
        tmux_bin, [*tmux.base, "-2", "attach-session", "-t", exact_s, "-d"]
    )
  return 0


def direct_session_switch(
    tmux: snap_lib.TmuxRunner,
    tmux_bin: str,
    raw_name: str,
    extra_cmd: list[str],
    client_tty: str = "",
) -> int:
  """Handles `code-harness overview <name> [cmd...]`."""
  home = os.path.expanduser("~")
  projects_dir = os.environ.get("CODE_HARNESS_PROJECTS_DIR") or os.path.join(
      home, "projects"
  )
  repos_dir = os.environ.get("CODE_HARNESS_REPOS_DIR") or os.path.join(
      home, "repos"
  )

  projects = discover_projects(projects_dir)
  repos = discover_repos(repos_dir)
  launch_dir = os.getcwd()
  env_args: list[str] = []
  if raw_name in projects:
    launch_dir = projects[raw_name]["launch_dir"]
    env_args = ["-e", f"PROJECT={raw_name}"]
  elif raw_name in repos:
    launch_dir = repos[raw_name]["launch_dir"]
  elif raw_name == "0":
    launch_dir = home

  sname = raw_name.replace(".", "_")
  exact_s = f"={sname}"
  if tmux.run("has-session", "-t", exact_s, check=False).returncode != 0:
    env = os.environ.copy()
    env["TMUX"] = ""
    subprocess.run(
        [
            *tmux.base,
            "-2",
            "new-session",
            "-d",
            "-s",
            sname,
            "-c",
            launch_dir,
            *env_args,
            *extra_cmd,
        ],
        env=env,
        stdin=subprocess.DEVNULL,
        check=True,
    )

  switch_args = ["-c", client_tty] if client_tty else []
  if os.environ.get("TMUX") or client_tty:
    tmux.run("switch-client", *switch_args, "-t", exact_s, check=False)
  else:
    os.execvp(
        tmux_bin, [*tmux.base, "-2", "attach-session", "-t", exact_s, "-d"]
    )
  return 0


def make_state_dir() -> str:
  runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
  base = runtime_dir if os.path.isdir(runtime_dir) else None
  return tempfile.mkdtemp(prefix="code-harness-overview-", dir=base)


def _self_overview_cmd(
    flavor: str | None,
    tmux_socket: str | None,
    client: str,
) -> list[str]:
  argv0 = sys.argv[0] if sys.argv else ""
  if (
      argv0
      and os.path.basename(argv0) in ("code-harness", ".code-harness-wrapped")
      and os.access(argv0, os.X_OK)
  ):
    cmd = [os.path.abspath(argv0)]
  else:
    exe = shutil.which("code-harness")
    cmd = [exe] if exe else [sys.executable, "-m", "code_harness.cli"]
  if flavor:
    cmd.extend(["--flavor", flavor])
  cmd.extend(["--builtin", "overview"])
  if tmux_socket:
    cmd.extend(["--tmux-socket", tmux_socket])
  if client:
    cmd.extend(["--client", client])
  return cmd


def parse_args(argv: list[str]) -> argparse.Namespace:
  p = argparse.ArgumentParser(
      prog="code-harness overview",
      description=(
          "Fuzzy overview of tmux sessions, ~/projects, ~/repos, and active "
          "coding-agent CLI conversations."
      ),
  )
  p.add_argument(
      "--list",
      action="store_true",
      help="Print formatted table lines without launching fzf.",
  )
  p.add_argument(
      "--json",
      action="store_true",
      help="Print collected overview JSON state and exit.",
  )
  p.add_argument(
      "--select-key",
      metavar="KEY",
      help="Non-interactively activate row KEY from a fresh scan (for tests).",
  )
  p.add_argument(
      "--session-only",
      action="store_true",
      help="With --select-key, switch session without focusing agent pane.",
  )
  p.add_argument(
      "--client",
      default="",
      metavar="TTY",
      help="Target tmux client tty.",
  )
  p.add_argument(
      "--tmux-socket",
      metavar="PATH",
      help="Connect to tmux socket at PATH (tmux -S PATH).",
  )
  # Internal fzf callback flags:
  p.add_argument("--state-dir", help=argparse.SUPPRESS)
  p.add_argument("--render-rows", action="store_true", help=argparse.SUPPRESS)
  p.add_argument("--rescan", action="store_true", help=argparse.SUPPRESS)
  p.add_argument("--cycle-filter", action="store_true", help=argparse.SUPPRESS)
  p.add_argument("--preview", metavar="KEY", help=argparse.SUPPRESS)
  p.add_argument(
      "session",
      nargs="?",
      help="Optional session/project/repo name to open directly.",
  )
  p.add_argument(
      "command",
      nargs=argparse.REMAINDER,
      help="Optional command when creating a new session directly.",
  )
  return p.parse_args(argv)


def cmd_overview(
    config: HarnessConfig,
    flavor: str | None,
    argv: list[str],
) -> int:
  if flavor is not None:
    config.get_flavor(flavor=flavor)

  args = parse_args(argv)
  tmux_bin = snap_lib.find_tmux_bin()
  tmux = snap_lib.TmuxRunner(tmux_bin, args.tmux_socket)

  if args.preview is not None:
    if args.state_dir:
      sys.stdout.write(render_preview(args.state_dir, args.preview, config))
    return 0

  if args.render_rows and args.state_dir:
    if args.cycle_filter:
      mode = cycle_filter_mode(args.state_dir)
    else:
      mode = read_filter_mode(args.state_dir)
    cache_path = os.path.join(args.state_dir, "cache.json")
    if args.rescan or not os.path.isfile(cache_path):
      data = collect_overview_data(config, tmux, args.client, flavor=flavor)
      write_cache(args.state_dir, data)
    else:
      with open(cache_path, encoding="utf-8") as f:
        data = json.load(f)
    sys.stdout.write("\n".join(render_table_lines(data, mode)) + "\n")
    return 0

  if args.session:
    return direct_session_switch(
        tmux, tmux_bin, args.session, args.command, args.client
    )

  data = collect_overview_data(config, tmux, args.client, flavor=flavor)

  if args.json:
    print(json.dumps(data, indent=2))
    return 0

  if args.list:
    sys.stdout.write("\n".join(render_table_lines(data, "all")) + "\n")
    return 0

  if args.select_key:
    row = next((r for r in data["rows"] if r["key"] == args.select_key), None)
    if row is None:
      print(
          f"code-harness overview: unknown row key {args.select_key!r}",
          file=sys.stderr,
      )
      return 1
    return activate_row(
        tmux,
        tmux_bin,
        row,
        session_only=args.session_only,
        client_tty=args.client,
    )

  state_dir = make_state_dir()
  try:
    write_cache(state_dir, data)
    initial_lines = render_table_lines(data, "all")

    self_cmd = _self_overview_cmd(flavor, args.tmux_socket, args.client)
    self_q = " ".join(shlex.quote(x) for x in self_cmd)
    sdir_q = shlex.quote(state_dir)

    preview_cmd = f"{self_q} --state-dir {sdir_q} --preview {{1}}"
    reload_cmd = f"{self_q} --state-dir {sdir_q} --render-rows --rescan"
    filter_cmd = f"{self_q} --state-dir {sdir_q} --render-rows --cycle-filter"

    header_hint = (
        "Enter: focus  │  C-o: session only  │  C-a: filter  │  "
        "C-r: reload  │  C-d/C-u: scroll preview"
    )
    fzf_cmd = [
        find_fzf_bin(),
        "+e",
        "--ansi",
        "--reverse",
        "--tiebreak=begin,chunk,index",
        "-d",
        SEP,
        "--with-nth=2",
        "--header-lines=1",
        "--header-first",
        f"--header={header_hint}",
        "--expect=ctrl-o",
        f"--preview={preview_cmd}",
        "--preview-window=right:45%:wrap",
        "--bind=ctrl-d:preview-half-page-down",
        "--bind=ctrl-u:preview-half-page-up",
        f"--bind=ctrl-r:reload({reload_cmd})",
        f"--bind=ctrl-a:reload({filter_cmd})",
    ]
    res = subprocess.run(
        fzf_cmd,
        input="\n".join(initial_lines) + "\n",
        text=True,
        stdout=subprocess.PIPE,
        check=False,
    )
    if res.returncode not in (0,):
      return 0

    out_lines = res.stdout.splitlines()
    if len(out_lines) < 2:
      return 0
    pressed_key = out_lines[0].strip()
    chosen_line = out_lines[1]
    chosen_key = chosen_line.split(SEP, 1)[0]
    if chosen_key == "header":
      return 0

    # Reload latest cache in case Ctrl-R rescan ran inside fzf.
    with open(os.path.join(state_dir, "cache.json"), encoding="utf-8") as f:
      latest_data = json.load(f)
    row = next(
        (r for r in latest_data["rows"] if r["key"] == chosen_key), None
    )
    if row is None:
      return 0
    return activate_row(
        tmux,
        tmux_bin,
        row,
        session_only=(pressed_key == "ctrl-o"),
        client_tty=args.client,
    )
  finally:
    shutil.rmtree(state_dir, ignore_errors=True)
