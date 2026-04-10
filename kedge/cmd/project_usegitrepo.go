package cmd

import (
	"fmt"
	"os"
	"path/filepath"

	"github.com/spf13/cobra"
)

var projectUseGitRepoCmd = &cobra.Command{
	Use:   "usegitrepo <repo_name>",
	Short: "Add a git worktree for a repo to the current project",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		repoName := args[0]
		project, err := resolveProjectName(cmd)
		if err != nil {
			return err
		}

		return runUseGitRepo(project, repoName)
	},
}

func runUseGitRepo(project, repoName string) error {
	repoDir, err := findGitRepoPath(repoName)
	if err != nil {
		return err
	}

	home, err := os.UserHomeDir()
	if err != nil {
		return err
	}

	targetDir := filepath.Join(home, "projects", project, "r", repoName)
	if _, errStatTarget := os.Stat(targetDir); errStatTarget == nil {
		return fmt.Errorf("target directory %s already exists", targetDir)
	}

	mainBranch := gitDefaultBranch(repoDir)
	worktrees, err := gitWorktreeList(repoDir)
	if err != nil {
		return err // gitWorktreeList now has descriptive error message
	}

	// Check if branch matching project name is checked out anywhere
	var currentDir string
	for path, branch := range worktrees {
		if branch == project {
			currentDir = path
			break
		}
	}

	if currentDir != "" {
		// Project branch is checked out, need to move that checkout to main or kedgebk
		
		// Is main checked out anywhere?
		mainCheckedOut := false
		for _, branch := range worktrees {
			if branch == mainBranch {
				mainCheckedOut = true
				break
			}
		}

		if !mainCheckedOut {
			if err := gitCheckout(currentDir, mainBranch); err != nil {
				return fmt.Errorf("failed to checkout %s in %s: %w", mainBranch, currentDir, err)
			}
		} else {
			bkBranch := project + "-kedgebk"
			// Does bkBranch exist? (Check all worktrees and local branches)
			// gitCheckoutNew will fail if it exists, which is what we want.
			if err := gitCheckoutNew(currentDir, bkBranch); err != nil {
				return fmt.Errorf("failed to create backup branch %s (it may already exist): %w", bkBranch, err)
			}
		}
	}

	if err := os.MkdirAll(filepath.Dir(targetDir), 0755); err != nil {
		return fmt.Errorf("failed to create parent directory for worktree: %w", err)
	}

	createBranch := !gitBranchExists(repoDir, project)
	if err := gitWorktreeAdd(repoDir, targetDir, project, createBranch); err != nil {
		return fmt.Errorf("failed to add worktree: %w", err)
	}

	fmt.Printf("Added worktree for %s to project %s at %s\n", repoName, project, targetDir)
	return nil
}

func init() {
	projectUseGitRepoCmd.Flags().String("project", "", "Name of the project")
	projectCmd.AddCommand(projectUseGitRepoCmd)
}
