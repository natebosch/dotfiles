package cmd

import (
	"fmt"
	"kedge/internal/kedge"
	"kedge/internal/tmux"

	"github.com/spf13/cobra"
)

var summaryCmd = &cobra.Command{
	Use:   "summary [kedge-id]",
	Short: "Print information about a kedge in structured TSV format.",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		ki, err := kedge.ParseKedgeID(args[0])
		if err != nil {
			return err
		}

		t := tmux.RealTmux{}
		source := getSource(ki.Type, t)

		summary, err := source.ReadSummary(cmd.Context(), ki.Name)
		if err != nil {
			return err
		}

		active := "_"
		sessions, _ := t.ListSessions(cmd.Context())
		for _, s := range sessions {
			if s == ki.TmuxSessionName() {
				active = "*"
				break
			}
		}

		fmt.Printf("%s\t%s\t%s\n", ki.String(), summary, active)
		return nil
	},
}

func init() {
	rootCmd.AddCommand(summaryCmd)
}
