package cmd

import (
	"bufio"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/pelletier/go-toml/v2"
	"github.com/spf13/cobra"
	"kedge/internal/kedge"
)

type KedgeConfig struct {
	Description string `toml:"description"`
}

const (
	mainBranch   = "main"
	masterBranch = "master"
)

var projectStartCmd = &cobra.Command{
	Use:   "start",
	Short: "Start a new project interactively",
	RunE: func(cmd *cobra.Command, args []string) error {
		_, err := runProjectStart()
		return err
	},
}

func runProjectStart() (string, error) {
	reader := bufio.NewReader(os.Stdin)

	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}

	cwd, err := os.Getwd()
	if err != nil {
		return "", err
	}

	var defaultName string
	var repoName string
	reposDir := filepath.Join(home, "repos")
	if strings.HasPrefix(cwd, reposDir) {
		relPath, errRel := filepath.Rel(reposDir, cwd)
		if errRel == nil && relPath != "." && !strings.HasPrefix(relPath, "..") {
			parts := strings.Split(relPath, string(filepath.Separator))
			repoName = parts[0]
			repoPath := filepath.Join(reposDir, repoName)
			currentBranch, errCurr := gitCurrentBranch(repoPath)
			if errCurr == nil && currentBranch != mainBranch && currentBranch != masterBranch {
				defaultName = currentBranch
			}
		}
	}

	prompt := "Project name"
	if defaultName != "" {
		prompt = fmt.Sprintf("%s [%s]", prompt, defaultName)
	}
	fmt.Printf("%s: ", prompt)

	name, _ := reader.ReadString('\n')
	name = strings.TrimSpace(name)

	if name == "" {
		if defaultName == "" {
			return "", fmt.Errorf("project name cannot be empty")
		}
		name = defaultName
	}

	// Alphanumeric + dash/underscore validation
	matched, _ := regexp.MatchString("^[a-zA-Z0-9_-]+$", name)
	if !matched {
		return "", fmt.Errorf("invalid project name: only alphanumeric, dashes, and underscores are allowed")
	}

	fmt.Print("Description: ")
	desc, _ := reader.ReadString('\n')
	desc = strings.TrimSpace(desc)

	projectDir := filepath.Join(home, "projects", name)
	if _, errStat := os.Stat(projectDir); errStat == nil {
		return "", fmt.Errorf("project directory %s already exists", projectDir)
	}

	kedge.Log.Info("creating project directory", "path", projectDir)
	if errMkdir := os.MkdirAll(projectDir, 0755); errMkdir != nil {
		return "", fmt.Errorf("failed to create project directory: %w", errMkdir)
	}

	config := KedgeConfig{Description: desc}
	configPath := filepath.Join(projectDir, "kedge.toml")

	kedge.Log.Info("writing project configuration", "path", configPath)
	f, err := os.Create(configPath)
	if err != nil {
		return "", fmt.Errorf("failed to create kedge.toml: %w", err)
	}
	defer f.Close()

	if err := toml.NewEncoder(f).Encode(config); err != nil {
		return "", fmt.Errorf("failed to encode kedge.toml: %w", err)
	}

	fmt.Printf("Project %s started at %s\n", name, projectDir)

	if repoName != "" {
		var doUseGitRepo bool
		if name == defaultName {
			doUseGitRepo = true
		} else {
			fmt.Printf("Create worktree for repo '%s'? [Y/n]: ", repoName)
			ans, _ := reader.ReadString('\n')
			ans = strings.TrimSpace(strings.ToLower(ans))
			if ans == "" || ans == "y" || ans == "yes" {
				doUseGitRepo = true
			}
		}

		if doUseGitRepo {
			if err := runUseGitRepo(name, repoName); err != nil {
				return "", err
			}
		}
	}

	return kedge.KedgeID{Type: kedge.TypeProject, Name: name}.String(), nil
}

func init() {
	projectCmd.AddCommand(projectStartCmd)
	startAlias := *projectStartCmd
	startAlias.Use = "start"
	startAlias.Hidden = true // Keep it as an alias/hidden top-level to avoid cluttering help
	rootCmd.AddCommand(&startAlias)
}
