package kedge

import (
	"context"
	"fmt"
	"iter"
	"kedge/internal/tmux"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"github.com/pelletier/go-toml/v2"
)

// GitRepoSource handles kedges in $HOME/repos
type GitRepoSource struct{}

// FindGitRepoPath resolves the actual path to a git repository, handling nested repos.
func FindGitRepoPath(repoPath string) (string, error) {
	if _, err := os.Stat(repoPath); os.IsNotExist(err) {
		return "", fmt.Errorf("repository directory %s does not exist", repoPath)
	}

	if _, err := os.Stat(filepath.Join(repoPath, ".git")); err == nil {
		return repoPath, nil
	}

	entries, err := os.ReadDir(repoPath)
	if err == nil {
		for _, e := range entries {
			if e.IsDir() {
				if _, err := os.Stat(filepath.Join(repoPath, e.Name(), ".git")); err == nil {
					return filepath.Join(repoPath, e.Name()), nil
				}
			}
		}
	}

	return "", fmt.Errorf("directory %s is not a git repository (no .git directory found)", repoPath)
}

func (s GitRepoSource) ReadSummary(ctx context.Context, name string) (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	repoPath := filepath.Join(home, "repos", name)
	
	resolvedPath, errFind := FindGitRepoPath(repoPath)
	if errFind != nil {
		Log.Warn("failed to resolve git repo path for summary", "repo", name, "error", errFind)
		return "", nil
	}

	cmd := exec.CommandContext(ctx, "git-summary")
	cmd.Dir = resolvedPath
	out, errCmd := cmd.Output()
	if errCmd != nil {
		Log.Warn("git-summary execution failed", "dir", resolvedPath, "error", errCmd)
		return "", nil
	}
	return strings.TrimSpace(string(out)), nil
}

func (s GitRepoSource) Discover() iter.Seq[KedgeID] {
	return func(yield func(KedgeID) bool) {
		home, err := os.UserHomeDir()
		if err != nil {
			return
		}
		reposDir := filepath.Join(home, "repos")
		entries, err := os.ReadDir(reposDir)
		if err != nil {
			return
		}
		for _, e := range entries {
			if e.IsDir() {
				if !yield(KedgeID{Type: TypeRepo, Name: e.Name()}) {
					return
				}
			}
		}
	}
}

func (s GitRepoSource) TmuxInfo(name string) (string, string, map[string]string) {
	home, _ := os.UserHomeDir()
	repoPath := filepath.Join(home, "repos", name)
	
	if resolvedPath, err := FindGitRepoPath(repoPath); err == nil {
		repoPath = resolvedPath
	}

	return repoPath, "vim", nil
}

// ProjectSource handles kedges in $HOME/projects
type ProjectSource struct{}

func (s ProjectSource) ReadSummary(ctx context.Context, name string) (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	configPath := filepath.Join(home, "projects", name, "kedge.toml")

	f, err := os.Open(configPath)
	if err != nil {
		return "", nil // Silence if file doesn't exist
	}
	defer f.Close()

	var config struct {
		Description string `toml:"description"`
	}
	if err := toml.NewDecoder(f).Decode(&config); err != nil {
		return "", nil // Silence if invalid TOML
	}
	return config.Description, nil
}

func (s ProjectSource) Discover() iter.Seq[KedgeID] {
	return func(yield func(KedgeID) bool) {
		home, err := os.UserHomeDir()
		if err != nil {
			return
		}
		projectsDir := filepath.Join(home, "projects")
		entries, err := os.ReadDir(projectsDir)
		if err != nil {
			return
		}
		for _, e := range entries {
			if e.IsDir() {
				if !yield(KedgeID{Type: TypeProject, Name: e.Name()}) {
					return
				}
			}
		}
	}
}

func (s ProjectSource) TmuxInfo(name string) (string, string, map[string]string) {
	home, _ := os.UserHomeDir()
	return filepath.Join(home, "projects", name), "", map[string]string{"PROJECT": name}
}

// SessionSource handles pure tmux sessions
type SessionSource struct {
	Tmux tmux.Tmux
}

func (s SessionSource) ReadSummary(ctx context.Context, name string) (string, error) {
	return "", nil
}

func (s SessionSource) Discover() iter.Seq[KedgeID] {
	return func(yield func(KedgeID) bool) {
		sessions, err := s.Tmux.ListSessions(context.Background())
		if err != nil {
			return
		}
		for _, name := range sessions {
			// We only yield if it looks like a KedgeID but it might not.
			// However, the instructions say: "we'll use the whole foratted kedge ID as the session name."
			ki, err := ParseKedgeID(name)
			if err == nil {
				if !yield(ki) {
					return
				}
			} else {
				// Fallback for sessions not created by kedge? 
				// The prompt says "treat the rest as general sessions"
				if !yield(KedgeID{Type: TypeSession, Name: name}) {
					return
				}
			}
		}
	}
}

func (s SessionSource) TmuxInfo(name string) (string, string, map[string]string) {
	return "", "", nil
}
