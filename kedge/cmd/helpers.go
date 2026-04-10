package cmd

import (
	"context"
	"errors"
	"fmt"
	"kedge/internal/fzf"
	"kedge/internal/kedge"
	"kedge/internal/tmux"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"github.com/spf13/cobra"
)

func resolveProjectName(cmd *cobra.Command) (string, error) {
	project, _ := cmd.Flags().GetString("project")

	if project == "" {
		project = os.Getenv("PROJECT")
	}

	if project == "" {
		cwd, err := os.Getwd()
		if err == nil {
			home, err := os.UserHomeDir()
			if err == nil {
				projectsDir := filepath.Join(home, "projects")
				if strings.HasPrefix(cwd, projectsDir) {
					rel, err := filepath.Rel(projectsDir, cwd)
					if err == nil && rel != "." && !strings.HasPrefix(rel, "..") {
						// The first component of the relative path is the project name
						parts := strings.Split(rel, string(filepath.Separator))
						project = parts[0]
					}
				}
			}
		}
	}

	if project == "" {
		return "", fmt.Errorf("could not determine project. Use --project flag, set $PROJECT, or run from within a project directory")
	}

	return project, nil
}

func gitDefaultBranch(repoDir string) string {
	cmd := exec.Command("git", "rev-parse", "--verify", mainBranch)
	cmd.Dir = repoDir
	if err := cmd.Run(); err == nil {
		return mainBranch
	}
	return masterBranch
}

func gitCurrentBranch(dir string) (string, error) {
	cmd := exec.Command("git", "branch", "--show-current")
	cmd.Dir = dir
	out, err := cmd.Output()
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(string(out)), nil
}

func gitWorktreeList(repoDir string) (map[string]string, error) {
	cmd := exec.Command("git", "worktree", "list", "--porcelain")
	cmd.Dir = repoDir
	out, err := cmd.CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("git worktree list failed in %s: %w (output: %s)", repoDir, err, out)
	}

	worktrees := make(map[string]string)
	lines := strings.Split(string(out), "\n")
	var currentPath string
	for _, line := range lines {
		if strings.HasPrefix(line, "worktree ") {
			currentPath = strings.TrimPrefix(line, "worktree ")
		} else if strings.HasPrefix(line, "branch ") {
			branch := strings.TrimPrefix(line, "branch refs/heads/")
			worktrees[currentPath] = branch
		}
	}
	return worktrees, nil
}

func gitCheckout(dir, branch string) error {
	kedge.Log.Info("executing git checkout", "dir", dir, "branch", branch)
	cmd := exec.Command("git", "checkout", branch)
	cmd.Dir = dir
	if out, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("git checkout %s failed: %w (output: %s)", branch, err, out)
	}
	return nil
}

func gitCheckoutNew(dir, newBranch string) error {
	kedge.Log.Info("executing git checkout -b", "dir", dir, "branch", newBranch)
	cmd := exec.Command("git", "checkout", "-b", newBranch)
	cmd.Dir = dir
	if out, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("git checkout -b %s failed: %w (output: %s)", newBranch, err, out)
	}
	return nil
}

func gitBranchExists(repoDir, branch string) bool {
	//nolint:gosec // branch and repoDir are controlled internally
	cmd := exec.Command("git", "show-ref", "--verify", "--quiet", "refs/heads/"+branch)
	cmd.Dir = repoDir
	return cmd.Run() == nil
}

func gitWorktreeAdd(repoDir, path, branch string, create bool) error {
	kedge.Log.Info("executing git worktree add", "repo", repoDir, "path", path, "branch", branch, "create", create)
	var args []string
	if create {
		args = []string{"worktree", "add", "-b", branch, path}
	} else {
		args = []string{"worktree", "add", path, branch}
	}
	cmd := exec.Command("git", args...)
	cmd.Dir = repoDir
	if out, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("git %v failed: %w (output: %s)", args, err, out)
	}
	return nil
}

func findGitRepoPath(repoName string) (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	repoPath := filepath.Join(home, "repos", repoName)
	return kedge.FindGitRepoPath(repoPath)
}

func getSource(kt kedge.KedgeType, t tmux.Tmux) kedge.KedgeSource {
	switch kt {
	case kedge.TypeRepo:
		return kedge.GitRepoSource{}
	case kedge.TypeProject:
		return kedge.ProjectSource{}
	case kedge.TypeSession:
		return kedge.SessionSource{Tmux: t}
	default:
		return nil
	}
}

func runFuzzyPick(ctx context.Context) (string, error) {
	t := tmux.RealTmux{}
	sessions, _ := t.ListSessions(ctx)
	activeSessions := make(map[string]bool)
	for _, s := range sessions {
		activeSessions[s] = true
	}

	var lines []string
	repoSource := kedge.GitRepoSource{}
	projectSource := kedge.ProjectSource{}
	sessionSource := kedge.SessionSource{Tmux: t}

	addKedgeLines := func(source kedge.KedgeSource, prefix rune) {
		for ki := range source.Discover() {
			summary, _ := source.ReadSummary(ctx, ki.Name)
			active := ""
			if activeSessions[ki.TmuxSessionName()] {
				active = "*"
			}
			line := fmt.Sprintf("%c%s\t%s\t%s\t%s", prefix, active, ki.Name, summary, ki.String())
			lines = append(lines, line)
		}
	}

	addKedgeLines(repoSource, 'R')
	addKedgeLines(projectSource, 'P')
	for ki := range sessionSource.Discover() {
		if ki.Type == kedge.TypeSession {
			line := fmt.Sprintf("S*\t%s\t\t%s", ki.Name, ki.String())
			lines = append(lines, line)
		}
	}

	f := fzf.RealFzf{}
	for {
		choice, err := f.Run(ctx, lines, "-d", "\t", "--with-nth", "1..3", "--ansi", "--no-sort", "--tiebreak", "begin,chunk", "--expect=ctrl-s", "--header=ctrl-s: start project")
		if err != nil {
			if errors.Is(err, fzf.ErrCancelled) {
				return "", nil
			}
			return "", err
		}
		if choice == "" {
			return "", nil
		}

		parts := strings.Split(choice, "\n")
		var key, selection string
		if len(parts) >= 1 {
			key = parts[0]
		}
		if len(parts) >= 2 {
			selection = parts[1]
		}

		if key == "ctrl-s" {
			newID, errStart := runProjectStart()
			if errStart != nil {
				fmt.Fprintf(os.Stderr, "Error creating project: %v\n", errStart)
				continue
			}
			return newID, nil
		}

		if selection == "" {
			return "", nil
		}

		fields := strings.Split(selection, "\t")
		if len(fields) > 0 {
			return fields[len(fields)-1], nil
		}
		return "", nil
	}
}

func runLaunch(ctx context.Context, idStr string) error {
	ki, err := kedge.ParseKedgeID(idStr)
	if err != nil {
		return err
	}

	t := tmux.RealTmux{}
	source := getSource(ki.Type, t)
	workingDir, command, env := source.TmuxInfo(ki.Name)

	if env == nil {
		env = make(map[string]string)
	}
	env["KEDGE"] = ki.String()

	sessionName := ki.TmuxSessionName()

	has, err := t.HasSession(ctx, sessionName)
	if err != nil {
		return err
	}

	if !has {
		if err := t.NewSession(ctx, sessionName, workingDir, command, env); err != nil {
			return err
		}
	}

	if os.Getenv("TMUX") != "" {
		return t.SwitchClient(ctx, sessionName)
	}
	return t.AttachSession(ctx, sessionName)
}

func validKedgeIDArgs(cmd *cobra.Command, args []string, toComplete string) ([]string, cobra.ShellCompDirective) {
	t := tmux.RealTmux{}
	repoSource := kedge.GitRepoSource{}
	projectSource := kedge.ProjectSource{}
	sessionSource := kedge.SessionSource{Tmux: t}

	var completions []string

	for ki := range repoSource.Discover() {
		completions = append(completions, ki.String())
	}
	for ki := range projectSource.Discover() {
		completions = append(completions, ki.String())
	}
	for ki := range sessionSource.Discover() {
		completions = append(completions, ki.String())
	}

	return completions, cobra.ShellCompDirectiveNoFileComp
}
