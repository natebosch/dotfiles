package cmd

import (
	"fmt"
	"github.com/spf13/cobra"
)

var fuzzypickCmd = &cobra.Command{
	Use:   "fuzzypick",
	Short: "Display a fuzzy picker to select a kedge.",
	RunE: func(cmd *cobra.Command, args []string) error {
		id, err := runFuzzyPick(cmd.Context())
		if err != nil {
			return err
		}
		if id != "" {
			fmt.Println(id)
		}
		return nil
	},
}

func init() {
	rootCmd.AddCommand(fuzzypickCmd)
}
