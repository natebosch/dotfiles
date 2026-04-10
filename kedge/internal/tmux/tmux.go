package tmux

import (
	"context"
	"os"
	"os/exec"
	"strings"
	"syscall"
)

type Tmux interface {
	ListSessions(ctx context.Context) ([]string, error)
	HasSession(ctx context.Context, name string) (bool, error)
	NewSession(ctx context.Context, name, workingDir, command string, env map[string]string) error
	SwitchClient(ctx context.Context, name string) error
	AttachSession(ctx context.Context, name string) error
}

type RealTmux struct{}

func (t RealTmux) ListSessions(ctx context.Context) ([]string, error) {
	cmd := exec.CommandContext(ctx, "tmux", "ls", "-F", "#{session_name}")
	out, err := cmd.Output()
	if err != nil {
		// If no sessions, tmux ls exits with error
		return nil, nil
	}
	lines := strings.Split(strings.TrimSpace(string(out)), "\n")
	var sessions []string
	for _, l := range lines {
		if l != "" {
			sessions = append(sessions, l)
		}
	}
	return sessions, nil
}

func (t RealTmux) HasSession(ctx context.Context, name string) (bool, error) {
	cmd := exec.CommandContext(ctx, "tmux", "has-session", "-t", name)
	err := cmd.Run()
	return err == nil, nil
}

func (t RealTmux) NewSession(ctx context.Context, name, workingDir, command string, env map[string]string) error {
	args := []string{"new-session", "-d", "-s", name}
	if workingDir != "" {
		args = append(args, "-c", workingDir)
	}
	for k, v := range env {
		args = append(args, "-e", k+"="+v)
	}
	if command != "" {
		args = append(args, command)
	}

	cmd := exec.CommandContext(ctx, "tmux", args...)
	// Ensure TMUX env var is empty to prevent nested sessions if desired,
	// though sometimes we want nested. session_finder used TMUX=""
	cmd.Env = os.Environ()
	for i, e := range cmd.Env {
		if strings.HasPrefix(e, "TMUX=") {
			cmd.Env[i] = "TMUX="
		}
	}
	return cmd.Run()
}

func (t RealTmux) SwitchClient(ctx context.Context, name string) error {
	cmd := exec.CommandContext(ctx, "tmux", "switch-client", "-t", name)
	return cmd.Run()
}

func (t RealTmux) AttachSession(ctx context.Context, name string) error {
	tmuxPath, err := exec.LookPath("tmux")
	if err != nil {
		return err
	}
	args := []string{"tmux", "-2", "a", "-t", name, "-d"}
	return syscall.Exec(tmuxPath, args, os.Environ())
}
