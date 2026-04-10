package cmd

import (
	"github.com/spf13/cobra"
)

var fuzzylaunchCmd = &cobra.Command{
	Use:   "fuzzylaunch",
	Short: "Pick a kedge using fzf and launch/attach to its tmux session.",
	RunE: func(cmd *cobra.Command, args []string) error {
		id, err := runFuzzyPick(cmd.Context())
		if err != nil {
			return err
		}
		if id == "" {
			return nil
		}
		return runLaunch(cmd.Context(), id)
	},
}

func init() {
	rootCmd.AddCommand(fuzzylaunchCmd)
}
