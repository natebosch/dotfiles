"""Unit tests for code-harness overview and batched conversation_labels."""

from __future__ import annotations

import io
import json
import os
import pathlib
import sqlite3
import subprocess
import tempfile
import unittest
from unittest import mock

from code_harness import cli
from code_harness import overview
from code_harness import snapshot
from code_harness.config import ContextLimits, FlavorConfig, HarnessConfig


class _FakeTmux:

  def __init__(self, responses: dict[str, tuple[int, str]] | None = None):
    self.base = ["tmux"]
    self.responses = responses or {}
    self.calls: list[tuple[str, ...]] = []

  def run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    self.calls.append(args)
    key = args[0] if args else ""
    rc, out = self.responses.get(key, (0, ""))
    return subprocess.CompletedProcess(
        [*self.base, *args], rc, stdout=out, stderr=""
    )


class OverviewHelpersTests(unittest.TestCase):

  def test_read_git_branch_regular_detached_and_worktree(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      root = pathlib.Path(tmp)

      # Regular repo
      repo = root / "repo"
      (repo / ".git").mkdir(parents=True)
      (repo / ".git" / "HEAD").write_text(
          "ref: refs/heads/feature/test\n", encoding="utf-8"
      )
      self.assertEqual(overview.read_git_branch(str(repo)), "feature/test")

      # Detached HEAD
      det = root / "detached"
      (det / ".git").mkdir(parents=True)
      (det / ".git" / "HEAD").write_text(
          "0123456789abcdef0123456789abcdef01234567\n", encoding="utf-8"
      )
      self.assertEqual(overview.read_git_branch(str(det)), "01234567")

      # Linked worktree
      wt_gitdir = root / "bare.git" / "worktrees" / "wt1"
      wt_gitdir.mkdir(parents=True)
      (wt_gitdir / "HEAD").write_text("ref: refs/heads/wt-br\n", encoding="utf-8")
      wt = root / "wt1"
      wt.mkdir()
      (wt / ".git").write_text(f"gitdir: {wt_gitdir}\n", encoding="utf-8")
      self.assertEqual(overview.read_git_branch(str(wt)), "wt-br")

  def test_parse_project_md_and_discovery(self) -> None:
    cid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    with tempfile.TemporaryDirectory() as tmp:
      root = pathlib.Path(tmp)
      pdir = root / "projects" / "my-proj"
      wt_dir = pdir / "subrepo"
      (wt_dir / ".git").mkdir(parents=True)
      (wt_dir / ".git" / "HEAD").write_text(
          "ref: refs/heads/fix-1\n", encoding="utf-8"
      )
      (pdir / "PROJECT.md").write_text(
          "# my-proj\n"
          "Status: active\n"
          "Next: finish unit tests\n"
          "  and clean up\n"
          f"- Conversation: {cid}, initial design\n"
          "  with follow-up\n",
          encoding="utf-8",
      )

      projects = overview.discover_projects(str(root / "projects"))
      self.assertIn("my-proj", projects)
      proj = projects["my-proj"]
      self.assertEqual(proj["status"], "active")
      self.assertEqual(proj["next"], "finish unit tests and clean up")
      self.assertEqual(len(proj["conversations"]), 1)
      self.assertEqual(proj["conversations"][0]["id"], cid)
      self.assertEqual(
          proj["conversations"][0]["note"], "initial design with follow-up"
      )
      self.assertEqual(
          overview.format_worktree_branches(proj["worktrees"]),
          "(subrepo:fix-1)",
      )

      # Nested single-subdir repo discovery
      rdir = root / "repos" / "wrapped-repo" / "sdk"
      (rdir / ".git").mkdir(parents=True)
      (rdir / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
      repos = overview.discover_repos(str(root / "repos"))
      self.assertEqual(repos["wrapped-repo"]["launch_dir"], str(rdir))
      self.assertEqual(repos["wrapped-repo"]["branch"], "main")

  def test_classify_ui_state(self) -> None:
    self.assertEqual(overview.classify_ui_state(None), ("none", overview.PRIO_NONE))
    self.assertEqual(
        overview.classify_ui_state({"state": "permission"}), ("perm", 1)
    )
    self.assertEqual(
        overview.classify_ui_state({"state": "question"}), ("ask", 2)
    )
    self.assertEqual(
        overview.classify_ui_state({
            "state": "plan",
            "conversation_id": "11111111-1111-1111-1111-111111111111",
            "reason": "waiting for plan approval",
        }),
        ("plan", 3),
    )
    self.assertEqual(
        overview.classify_ui_state({
            "state": "settled",
            "conversation_id": "11111111-1111-1111-1111-111111111111",
            "reason": "done",
        }),
        ("done", 4),
    )
    self.assertEqual(
        overview.classify_ui_state({"state": "working"}), ("work", 5)
    )
    self.assertEqual(
        overview.classify_ui_state({
            "state": "settled",
            "conversation_id": None,
            "reason": "empty prompt (no conversation yet)",
        }),
        ("new", 6),
    )
    self.assertGreater(overview.PRIO_PROJ_WORK, overview.PRIO_NONE)
    self.assertGreater(overview.PRIO_PROJ_DONE, overview.PRIO_PROJ_WORK)
    self.assertGreater(overview.PRIO_UNSTARTED, overview.PRIO_PROJ_DONE)

  def test_format_state_col_filter_and_summary_for_plan(self) -> None:
    col = overview.format_state_col("plan")
    self.assertIn(overview.C_PLAN, col)
    self.assertIn("◆ PLAN", col)

    rows = [
        {
            "key": "1",
            "priority": 1,
            "ui_state": "perm",
            "tmux_active": True,
            "kind": "S",
            "session_name": "s1",
            "target_display": "s1",
            "is_current_session": False,
            "ctx": {},
            "conversation_live": True,
            "detail_main": "p",
            "branch_str": "",
        },
        {
            "key": "2",
            "priority": 2,
            "ui_state": "ask",
            "tmux_active": True,
            "kind": "S",
            "session_name": "s2",
            "target_display": "s2",
            "is_current_session": False,
            "ctx": {},
            "conversation_live": True,
            "detail_main": "a",
            "branch_str": "",
        },
        {
            "key": "3",
            "priority": 3,
            "ui_state": "plan",
            "tmux_active": True,
            "kind": "S",
            "session_name": "s3",
            "target_display": "s3",
            "is_current_session": False,
            "ctx": {},
            "conversation_live": True,
            "detail_main": "pl",
            "branch_str": "",
        },
        {
            "key": "4",
            "priority": 4,
            "ui_state": "done",
            "tmux_active": True,
            "kind": "S",
            "session_name": "s4",
            "target_display": "s4",
            "is_current_session": False,
            "ctx": {},
            "conversation_live": True,
            "detail_main": "d",
            "branch_str": "",
        },
        {
            "key": "5",
            "priority": 5,
            "ui_state": "work",
            "tmux_active": True,
            "kind": "S",
            "session_name": "s5",
            "target_display": "s5",
            "is_current_session": False,
            "ctx": {},
            "conversation_live": True,
            "detail_main": "w",
            "branch_str": "",
        },
    ]
    attn = overview.filter_rows(rows, "attention")
    self.assertEqual(
        [r["ui_state"] for r in attn], ["perm", "ask", "plan", "done"]
    )

    lines = overview.render_table_lines({"rows": rows, "limits_enabled": False})
    header = lines[0]
    self.assertIn(f"{overview.C_PLAN}1 plan{overview.RESET}", header)
    ask_idx = header.index("1 ask")
    plan_idx = header.index("1 plan")
    done_idx = header.index("1 done")
    self.assertLess(ask_idx, plan_idx)
    self.assertLess(plan_idx, done_idx)

  def test_filter_mode_cycle(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      self.assertEqual(overview.read_filter_mode(tmp), "all")
      self.assertEqual(overview.cycle_filter_mode(tmp), "attention")
      self.assertEqual(overview.cycle_filter_mode(tmp), "active")
      self.assertEqual(overview.cycle_filter_mode(tmp), "all")


class OverviewCollectAndRenderTests(unittest.TestCase):

  def _make_config(
      self, context_limits: ContextLimits | None = None
  ) -> HarnessConfig:
    return HarnessConfig(
        default_flavor="agy",
        flavors={
            "agy": FlavorConfig(
                name="agy",
                data_dir_name="antigravity-cli",
                context_limits=context_limits,
            )
        },
    )

  def test_collect_ranks_rows_and_toggles_ctx_column(self) -> None:
    cid_perm = "11111111-1111-1111-1111-111111111111"
    cid_work = "22222222-2222-2222-2222-222222222222"
    with tempfile.TemporaryDirectory() as tmp:
      root = pathlib.Path(tmp)
      pdir = root / "projects" / "idle-proj"
      pdir.mkdir(parents=True)
      (pdir / "PROJECT.md").write_text("Status: active\nNext: test\n", encoding="utf-8")
      rdir = root / "repos" / "idle-repo"
      rdir.mkdir(parents=True)

      fake_sessions = [
          {
              "name": "multi",
              "project_env": "",
              "window_order": [0, 1],
              "windows": {
                  0: {
                      "name": "w0",
                      "active": True,
                      "panes": [
                          {
                              "index": 0,
                              "window_index": 0,
                              "pane_id": "%1",
                              "pane_pid": 100,
                              "cwd": str(root),
                              "agent": {
                                  "flavor": "agy",
                                  "data_dir": str(root / "data"),
                                  "conversation_id": cid_work,
                                  "state": "working",
                                  "reason": "working",
                                  "label": "Work conv",
                              },
                              "busy_non_agent": [],
                          }
                      ],
                  },
                  1: {
                      "name": "w1",
                      "active": False,
                      "panes": [
                          {
                              "index": 0,
                              "window_index": 1,
                              "pane_id": "%2",
                              "pane_pid": 101,
                              "cwd": str(root),
                              "agent": {
                                  "flavor": "agy",
                                  "data_dir": str(root / "data"),
                                  "conversation_id": cid_perm,
                                  "state": "permission",
                                  "reason": "waiting on permission",
                                  "label": "Perm conv",
                              },
                              "busy_non_agent": [],
                          }
                      ],
                  },
              },
          }
      ]
      fake_tmux = _FakeTmux({
          "list-windows": (0, f"multi\x1f0\x1f0\x1f1\nmulti\x1f1\x1f0\x1f1\n"),
          "display-message": (0, "multi\n"),
      })

      env = {
          "CODE_HARNESS_PROJECTS_DIR": str(root / "projects"),
          "CODE_HARNESS_REPOS_DIR": str(root / "repos"),
      }

      # Without context limits: CTX column is suppressed.
      cfg_no_limits = self._make_config(context_limits=None)
      with (
          mock.patch.dict(os.environ, env, clear=False),
          mock.patch.object(
              snapshot, "load_process_table", return_value=({}, {})
          ),
          mock.patch.object(snapshot, "scan_tmux", return_value=fake_sessions),
          mock.patch.object(snapshot, "conversation_labels", return_value={}),
      ):
        data_no = overview.collect_overview_data(cfg_no_limits, fake_tmux)
      self.assertFalse(data_no["limits_enabled"])
      self.assertEqual(
          [r["ui_state"] for r in data_no["rows"]],
          ["perm", "work", "none", "none"],
      )
      self.assertEqual(data_no["rows"][0]["target_display"], "multi :1")
      self.assertEqual(data_no["rows"][1]["target_display"], "multi :0")
      lines_no = overview.render_table_lines(data_no, "all")
      self.assertNotIn("CTX", lines_no[0])

      # With context limits: CTX column is included and preview renders it.
      cfg_limits = self._make_config(
          context_limits=ContextLimits(warn=150000, high=250000)
      )
      with (
          mock.patch.dict(os.environ, env, clear=False),
          mock.patch.object(
              snapshot, "load_process_table", return_value=({}, {})
          ),
          mock.patch.object(snapshot, "scan_tmux", return_value=fake_sessions),
          mock.patch.object(snapshot, "conversation_labels", return_value={}),
      ):
        data_yes = overview.collect_overview_data(cfg_limits, fake_tmux)
      self.assertTrue(data_yes["limits_enabled"])
      lines_yes = overview.render_table_lines(data_yes, "all")
      self.assertIn("CTX", lines_yes[0])

      # Attention filter keeps only perm/ask/done.
      attn_lines = overview.render_table_lines(data_yes, "attention")
      self.assertEqual(len(attn_lines), 2)  # header + 1 perm row

      # Preview rendering from cached state_dir.
      state_dir = root / "state"
      state_dir.mkdir()
      overview.write_cache(str(state_dir), data_yes)
      prev = overview.render_preview(str(state_dir), "r1", cfg_limits)
      self.assertIn("Active Agent CLI", prev)
      self.assertIn("Perm conv", prev)

  def test_help_lists_overview_as_public_command(self) -> None:
    stdout = io.StringIO()
    with mock.patch("sys.stdout", stdout):
      rc = cli.main(["--help"])
    self.assertEqual(rc, 0)
    self.assertIn("overview", stdout.getvalue())

  def test_is_self_script_proc_recognizes_overview(self) -> None:
    self.assertTrue(
        snapshot.is_self_script_proc({
            "argv": ["/usr/bin/code-harness", "overview", "--list"],
        })
    )


class ConversationLabelsTests(unittest.TestCase):

  def test_batched_labels_pbtxt_json_db_and_transcript(self) -> None:
    cid_pb = "11111111-1111-1111-1111-111111111111"
    cid_json = "22222222-2222-2222-2222-222222222222"
    cid_db = "33333333-3333-3333-3333-333333333333"
    cid_tx = "44444444-4444-4444-4444-444444444444"
    with tempfile.TemporaryDirectory() as tmp:
      data_dir = pathlib.Path(tmp)
      ann_dir = data_dir / "annotations"
      ann_dir.mkdir(parents=True)
      # Prototext with octal UTF-8 em-dash (\342\200\224) and adjacent literal.
      (ann_dir / f"{cid_pb}.pbtxt").write_text(
          'title: "Alpha \\342\\200\\224 "\n"Beta"\n', encoding="utf-8"
      )
      (ann_dir / f"{cid_json}.json").write_text(
          json.dumps({"title": "JSON Title"}), encoding="utf-8"
      )

      con = sqlite3.connect(str(data_dir / "conversation_summaries.db"))
      try:
        con.execute("CREATE TABLE summaries (conversation_id TEXT, title TEXT)")
        con.execute(
            "INSERT INTO summaries VALUES (?, ?)", (cid_db, "DB Summary Title")
        )
        con.commit()
      finally:
        con.close()

      tx_path = (
          data_dir
          / "brain"
          / cid_tx
          / ".system_generated"
          / "logs"
          / "transcript.jsonl"
      )
      tx_path.parent.mkdir(parents=True)
      tx_path.write_text(
          json.dumps({
              "type": "USER_INPUT",
              "content": "<USER_REQUEST>First prompt text</USER_REQUEST>",
          })
          + "\n",
          encoding="utf-8",
      )

      labels = snapshot.conversation_labels(
          str(data_dir), [cid_pb, cid_json, cid_db, cid_tx]
      )
      self.assertEqual(labels[cid_pb], "Alpha \u2014 Beta")
      self.assertEqual(labels[cid_json], "JSON Title")
      self.assertEqual(labels[cid_db], "DB Summary Title")
      self.assertEqual(labels[cid_tx], "First prompt text")


class OverviewPreviewContextTests(unittest.TestCase):

  def _render_ctx_line(
      self, ctx: dict, limits: dict[str, int] | None = None
  ) -> str:
    limits = limits or {"warn": 250_000, "high": 400_000}
    row = {
        "key": "r1",
        "kind": "S",
        "name": "s1",
        "tmux_active": True,
        "session_name": "s1",
        "launch_dir": "/tmp",
        "branch_str": "",
        "project": None,
        "conversation_live": True,
        "ui_state": "done",
        "state_reason": "settled",
        "window_index": 0,
        "pane_id": "%0",
        "conversation_id": "11111111-1111-1111-1111-111111111111",
        "conversation_title": "demo",
        "ctx": {"enabled": True, "limits": limits, **ctx},
        "windows": [],
    }
    cache = {
        "limits_enabled": True,
        "limits": limits,
        "data_dir": "/tmp/nonexistent",
        "rows": [row],
    }
    with tempfile.TemporaryDirectory() as tmp:
      with open(os.path.join(tmp, "cache.json"), "w", encoding="utf-8") as f:
        json.dump(cache, f)
      text = overview.render_preview(tmp, "r1")
    for line in text.splitlines():
      if line.startswith("Context:"):
        return line
    self.fail(f"Context line not found in preview:\n{text}")

  def test_preview_context_breakdown_and_compactions(self) -> None:
    single_worker = self._render_ctx_line({
        "tokens": 320_000,
        "formatted": "320K",
        "severity": "warn",
        "self_tokens": 115_000,
        "self_formatted": "115K",
        "child_tokens": 205_000,
        "child_formatted": "205K",
        "child_count": 1,
        "compactions": 1,
        "children": [{"type": "worker"}],
    })
    self.assertIn(
        "320,000 tokens (320K: 115K main + 205K worker, 1 compaction)",
        single_worker,
    )

    multi_children = self._render_ctx_line({
        "tokens": 450_000,
        "formatted": "450K",
        "severity": "high",
        "self_tokens": 100_000,
        "self_formatted": "100K",
        "child_tokens": 350_000,
        "child_formatted": "350K",
        "child_count": 3,
        "compactions": 2,
        "children": [{"type": "swarm"}, {"type": "swarm"}, {"type": "worker"}],
    })
    self.assertIn(
        "450,000 tokens (450K: 100K main + 350K across 3 children, "
        "2 compactions)",
        multi_children,
    )

    single_swarm = self._render_ctx_line({
        "tokens": 200_000,
        "formatted": "200K",
        "severity": "ok",
        "self_tokens": 120_000,
        "self_formatted": "120K",
        "child_tokens": 80_000,
        "child_formatted": "80K",
        "child_count": 1,
        "compactions": 0,
        "children": [{"type": "swarm"}],
    })
    self.assertIn("200,000 tokens (200K: 120K main + 80K swarm)", single_swarm)

    compacted_single = self._render_ctx_line({
        "tokens": 48_000,
        "formatted": "48K",
        "severity": "ok",
        "self_tokens": 48_000,
        "self_formatted": "48K",
        "child_tokens": 0,
        "child_formatted": "",
        "child_count": 0,
        "compactions": 1,
        "children": [],
    })
    self.assertIn("48,000 tokens (48K, 1 compaction)", compacted_single)


if __name__ == "__main__":
  unittest.main()
