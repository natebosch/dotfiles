"""Unit tests for code_harness.tmux_notify hidden subcommand."""

from __future__ import annotations

import io
import subprocess
import tempfile
import unittest
from unittest import mock

from code_harness import cli, snapshot, tmux_notify
from code_harness.config import load_config


class _FakeTmux:

  def __init__(self, stdout: str = "", returncode: int = 0) -> None:
    self._stdout = stdout
    self._returncode = returncode

  def run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    del check
    return subprocess.CompletedProcess(
        ["tmux", *args], self._returncode, self._stdout, ""
    )


class TmuxNotifyHiddenCommandTests(unittest.TestCase):

  def test_tmux_notify_is_hidden_from_top_level_help(self) -> None:
    self.assertIn("tmux-notify", cli.HIDDEN_COMMANDS)
    self.assertNotIn("tmux-notify", cli.PUBLIC_COMMANDS)
    stdout = io.StringIO()
    with mock.patch("sys.stdout", stdout):
      rc = cli.main(["--help"])
    self.assertEqual(rc, 0)
    self.assertNotIn("tmux-notify", stdout.getvalue())

  def test_is_self_script_proc_ignores_tmux_notify(self) -> None:
    self.assertTrue(
        snapshot.is_self_script_proc(
            {"argv": ["/usr/bin/code-harness", "tmux-notify", "Title"]}
        )
    )

  def test_sanitization_and_osc777_formatting(self) -> None:
    self.assertEqual(
        tmux_notify.sanitize_osc_field(
            "a;b\n\t\x07\x1b]9;bad\x7f\x9bc", strip_semicolons=True
        ),
        "ab ]9badc",
    )
    self.assertEqual(
        tmux_notify.sanitize_osc_field(
            "x;y\r\n  z\x00", strip_semicolons=False
        ),
        "x;y z",
    )
    seq = tmux_notify.format_osc777_notify("T;1\x07", "B;2\x1b")
    self.assertEqual(seq, "\033]777;notify;T1;B;2\007")

    with tempfile.NamedTemporaryFile() as reg:
      self.assertFalse(tmux_notify.write_osc_to_tty(reg.name, seq))
      self.assertFalse(tmux_notify.write_osc_to_tty("/tmp/none", seq))

  def test_list_clients_and_focus_filtering(self) -> None:
    sep = "\x1f"
    tmux = _FakeTmux(
        stdout=(
            f"/dev/pts/1{sep}attached,utf8{sep}s1{sep}100\n"
            f"/dev/pts/2{sep}attached,focused{sep}s2{sep}200\n"
            "malformed\n"
        )
    )
    clients = tmux_notify.list_tmux_clients(tmux)
    self.assertEqual(len(clients), 2)
    self.assertFalse(clients[0]["focused"])
    self.assertTrue(clients[1]["focused"])
    self.assertTrue(tmux_notify.any_client_focused(clients))
    self.assertFalse(tmux_notify.any_client_focused([clients[0]]))

    with mock.patch.object(
        tmux_notify, "write_osc_to_tty", return_value=True
    ) as mock_write:
      sent = tmux_notify.send_tmux_notification(
          tmux, "T", "B", if_unfocused=True, clients=clients
      )
      self.assertEqual(sent, 0)
      mock_write.assert_not_called()

      sent = tmux_notify.send_tmux_notification(
          tmux, "T", "B", if_unfocused=True, clients=[clients[0]]
      )
      self.assertEqual(sent, 1)
      mock_write.assert_called_once_with(
          "/dev/pts/1", "\033]777;notify;T;B\007"
      )

  def test_cmd_tmux_notify_invokes_send(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      cfg = load_config(env={"XDG_CONFIG_HOME": tmp})
      with (
          mock.patch.object(
              tmux_notify, "TmuxRunner", return_value=_FakeTmux()
          ),
          mock.patch.object(
              tmux_notify, "send_tmux_notification", return_value=1
          ) as mock_send,
      ):
        rc = tmux_notify.cmd_tmux_notify(
            cfg, None, ["--if-unfocused", "Hello", "World"]
        )
      self.assertEqual(rc, 0)
      mock_send.assert_called_once()
      args, kwargs = mock_send.call_args
      self.assertEqual(args[1:], ("Hello", "World"))
      self.assertTrue(kwargs["if_unfocused"])


if __name__ == "__main__":
  unittest.main()
