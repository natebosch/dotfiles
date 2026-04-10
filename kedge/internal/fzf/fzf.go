package fzf

import (
	"context"
	"errors"
	"io"
	"os"
	"os/exec"
	"strings"
)

type Fzf interface {
	Run(ctx context.Context, input []string, args ...string) (string, error)
}

type RealFzf struct{}

func (f RealFzf) Run(ctx context.Context, input []string, args ...string) (string, error) {
	// session_finder uses: fzf -d $'\x01' --with-nth 3.. --ansi --no-sort --tiebreak begin,chunk
	cmd := exec.CommandContext(ctx, "fzf", args...)
	cmd.Stderr = os.Stderr
	stdin, err := cmd.StdinPipe()
	if err != nil {
		return "", err
	}

	go func() {
		defer stdin.Close()
		for _, line := range input {
			_, _ = io.WriteString(stdin, line+"\n")
		}
	}()

	out, err := cmd.Output()
	if err != nil {
		var exitErr *exec.ExitError
		if errors.As(err, &exitErr) && exitErr.ExitCode() == 130 {
			// User cancelled fzf
			return "", nil
		}
		return "", err
	}

	return strings.TrimSpace(string(out)), nil
}
