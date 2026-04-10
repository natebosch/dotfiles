package kedge

import (
	"context"
	"iter"
	"kedge/internal/tmux"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

// GitRepoSource handles kedges in $HOME/repos
type GitRepoSource struct{}

func (s GitRepoSource) ReadSummary(ctx context.Context, name string) (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	repoPath := filepath.Join(home, "repos", name)
	
	// Check if .git exists, or if it's a subdir
	if _, errStat := os.Stat(filepath.Join(repoPath, ".git")); os.IsNotExist(errStat) {
		// Look for first subdir with .git
		entries, errRead := os.ReadDir(repoPath)
		if errRead == nil {
			for _, e := range entries {
				if e.IsDir() {
					if _, errSubStat := os.Stat(filepath.Join(repoPath, e.Name(), ".git")); errSubStat == nil {
						repoPath = filepath.Join(repoPath, e.Name())
						break
					}
				}
			}
		}
	}

	cmd := exec.CommandContext(ctx, "git-summary")
	cmd.Dir = repoPath
	out, err := cmd.Output()
	if err != nil {
		return "", nil // Silence errors for summary
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
	// Same logic as session_finder for nested git repos
	if _, err := os.Stat(filepath.Join(repoPath, ".git")); os.IsNotExist(err) {
		entries, err := os.ReadDir(repoPath)
		if err == nil {
			for _, e := range entries {
				if e.IsDir() {
					if _, err := os.Stat(filepath.Join(repoPath, e.Name(), ".git")); err == nil {
						repoPath = filepath.Join(repoPath, e.Name())
						break
					}
				}
			}
		}
	}
	return repoPath, "vim", nil
}

// ProjectSource handles kedges in $HOME/projects
type ProjectSource struct{}

func (s ProjectSource) ReadSummary(ctx context.Context, name string) (string, error) {
	// Project summary is blank for now as per instructions
	return "", nil
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
