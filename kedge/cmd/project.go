package cmd

import (
	"github.com/spf13/cobra"
)

var projectCmd = &cobra.Command{
	Use:   "project",
	Short: "Manage projects in ~/projects/",
	Long:  `Manage projects, including starting new ones and accessing notes.`,
}

func init() {
	rootCmd.AddCommand(projectCmd)
}
