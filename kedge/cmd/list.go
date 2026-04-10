package cmd

import (
	"fmt"
	"kedge/internal/kedge"
	"kedge/internal/tmux"

	"github.com/spf13/cobra"
)

var listCmd = &cobra.Command{
	Use:   "list",
	Short: "Scan directories and list all available kedges.",
	RunE: func(cmd *cobra.Command, args []string) error {
		t := tmux.RealTmux{}
		sessions, _ := t.ListSessions(cmd.Context())
		activeSessions := make(map[string]bool)
		for _, s := range sessions {
			activeSessions[s] = true
		}

		repoSource := kedge.GitRepoSource{}
		projectSource := kedge.ProjectSource{}
		sessionSource := kedge.SessionSource{Tmux: t}

		// Print Repos
		for ki := range repoSource.Discover() {
			summary, _ := repoSource.ReadSummary(cmd.Context(), ki.Name)
			active := "_"
			if activeSessions[ki.TmuxSessionName()] {
				active = "*"
			}
			fmt.Printf("%s\t%s\t%s\n", ki.String(), summary, active)
		}

		// Print Projects
		for ki := range projectSource.Discover() {
			summary, _ := projectSource.ReadSummary(cmd.Context(), ki.Name)
			active := "_"
			if activeSessions[ki.TmuxSessionName()] {
				active = "*"
			}
			fmt.Printf("%s\t%s\t%s\n", ki.String(), summary, active)
		}

		// Print General Sessions that aren't repos or projects
		for ki := range sessionSource.Discover() {
			// Check if we already printed this (e.g. if it was R:name or P:name)
			if ki.Type == kedge.TypeSession {
				fmt.Printf("%s\t\t*\n", ki.String()) // Sessions are always active
			}
		}

		return nil
	},
}

func init() {
	rootCmd.AddCommand(listCmd)
}
