package cmd

import (
	"context"
	"fmt"
	"kedge/internal/fzf"
	"kedge/internal/kedge"
	"kedge/internal/tmux"
	"os"
	"strings"
)

func getSource(kt kedge.KedgeType, t tmux.Tmux) kedge.KedgeSource {
	switch kt {
	case kedge.TypeRepo:
		return kedge.GitRepoSource{}
	case kedge.TypeProject:
		return kedge.ProjectSource{}
	case kedge.TypeSession:
		return kedge.SessionSource{Tmux: t}
	default:
		return nil
	}
}

func runFuzzyPick(ctx context.Context) (string, error) {
	t := tmux.RealTmux{}
	sessions, _ := t.ListSessions(ctx)
	activeSessions := make(map[string]bool)
	for _, s := range sessions {
		activeSessions[s] = true
	}

	var lines []string
	repoSource := kedge.GitRepoSource{}
	projectSource := kedge.ProjectSource{}
	sessionSource := kedge.SessionSource{Tmux: t}

	addKedgeLines := func(source kedge.KedgeSource, prefix rune) {
		for ki := range source.Discover() {
			summary, _ := source.ReadSummary(ctx, ki.Name)
			active := ""
			if activeSessions[ki.TmuxSessionName()] {
				active = "*"
			}
			line := fmt.Sprintf("%c%s\t%s\t%s\t%s", prefix, active, ki.Name, summary, ki.String())
			lines = append(lines, line)
		}
	}

	addKedgeLines(repoSource, 'R')
	addKedgeLines(projectSource, 'P')
	for ki := range sessionSource.Discover() {
		if ki.Type == kedge.TypeSession {
			line := fmt.Sprintf("S*\t%s\t\t%s", ki.Name, ki.String())
			lines = append(lines, line)
		}
	}

	f := fzf.RealFzf{}
	choice, err := f.Run(ctx, lines, "-d", "\t", "--with-nth", "1..3", "--ansi", "--no-sort", "--tiebreak", "begin,chunk")
	if err != nil {
		return "", err
	}
	if choice == "" {
		return "", nil
	}

	fields := strings.Split(choice, "\t")
	if len(fields) > 0 {
		return fields[len(fields)-1], nil
	}
	return "", nil
}

func runLaunch(ctx context.Context, idStr string) error {
	ki, err := kedge.ParseKedgeID(idStr)
	if err != nil {
		return err
	}

	t := tmux.RealTmux{}
	source := getSource(ki.Type, t)
	workingDir, command, env := source.TmuxInfo(ki.Name)

	if env == nil {
		env = make(map[string]string)
	}
	env["KEDGE"] = ki.String()

	sessionName := ki.TmuxSessionName()

	has, err := t.HasSession(ctx, sessionName)
	if err != nil {
		return err
	}

	if !has {
		if err := t.NewSession(ctx, sessionName, workingDir, command, env); err != nil {
			return err
		}
	}

	if os.Getenv("TMUX") != "" {
		return t.SwitchClient(ctx, sessionName)
	}
	return t.AttachSession(ctx, sessionName)
}
