package cmd

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"github.com/spf13/cobra"
)

var projectNotesCmd = &cobra.Command{
	Use:   "notes",
	Short: "Open NOTES.md for a project",
	RunE: func(cmd *cobra.Command, args []string) error {
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
			return fmt.Errorf("could not determine project. Use --project flag, set $PROJECT, or run from within a project directory")
		}

		home, err := os.UserHomeDir()
		if err != nil {
			return err
		}
		notesPath := filepath.Join(home, "projects", project, "NOTES.md")

		// Ensure directory exists, but NOTES.md might not yet.
		projectDir := filepath.Join(home, "projects", project)
		if _, err := os.Stat(projectDir); os.IsNotExist(err) {
			return fmt.Errorf("project directory %s does not exist", projectDir)
		}

		editor := os.Getenv("EDITOR")
		if editor == "" {
			editor = "vim"
		}

		execCmd := exec.Command(editor, notesPath)
		execCmd.Stdin = os.Stdin
		execCmd.Stdout = os.Stdout
		execCmd.Stderr = os.Stderr

		return execCmd.Run()
	},
}

func init() {
	projectNotesCmd.Flags().String("project", "", "Name of the project")
	projectCmd.AddCommand(projectNotesCmd)
}
