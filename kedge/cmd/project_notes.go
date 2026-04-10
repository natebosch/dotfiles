package cmd

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"

	"github.com/spf13/cobra"
)

var projectNotesCmd = &cobra.Command{
	Use:   "notes",
	Short: "Open NOTES.md for a project",
	RunE: func(cmd *cobra.Command, args []string) error {
		project, err := resolveProjectName(cmd)
		if err != nil {
			return err
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
