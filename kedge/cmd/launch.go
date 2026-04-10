package cmd

import (
	"github.com/spf13/cobra"
)

var launchCmd = &cobra.Command{
	Use:   "launch [kedge-id]",
	Short: "Launch or attach to a tmux session for a given kedge.",
	Args:  cobra.ExactArgs(1),
	ValidArgsFunction: validKedgeIDArgs,
	RunE: func(cmd *cobra.Command, args []string) error {
		return runLaunch(cmd.Context(), args[0])
	},
}

func init() {
	rootCmd.AddCommand(launchCmd)
}
