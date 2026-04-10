package kedge

import (
	"context"
	"fmt"
	"iter"
	"regexp"
	"strings"
)

type KedgeType string

const (
	TypeRepo    KedgeType = "git_repo"
	TypeProject KedgeType = "project"
	TypeSession KedgeType = "session"
)

var nameRegex = regexp.MustCompile(`^[a-zA-Z0-9\-_]+$`)

type KedgeID struct {
	Type KedgeType
	Name string
}

func (k KedgeID) ValidateName() error {
	if k.Name == "" {
		return fmt.Errorf("kedge name cannot be empty")
	}
	if !nameRegex.MatchString(k.Name) {
		return fmt.Errorf("kedge name contains invalid characters (only alphanumerics, hyphen, and underscore allowed): %s", k.Name)
	}
	return nil
}

func (k KedgeID) String() string {
	var t rune
	switch k.Type {
	case TypeRepo:
		t = 'R'
	case TypeProject:
		t = 'P'
	case TypeSession:
		t = 'S'
	default:
		t = '?'
	}
	return fmt.Sprintf("%c_%s", t, k.Name)
}

func (k KedgeID) TmuxSessionName() string {
	if k.Type == TypeSession {
		return k.Name
	}
	return k.String()
}

func ParseKedgeID(s string) (KedgeID, error) {
	parts := strings.SplitN(s, "_", 2)
	if len(parts) != 2 {
		ki := KedgeID{Type: TypeSession, Name: s}
		return ki, ki.ValidateName()
	}

	var kt KedgeType
	switch parts[0] {
	case "R", "G":
		kt = TypeRepo
	case "P":
		kt = TypeProject
	case "S":
		kt = TypeSession
	default:
		ki := KedgeID{Type: TypeSession, Name: s}
		return ki, ki.ValidateName()
	}

	ki := KedgeID{Type: kt, Name: parts[1]}
	if err := ki.ValidateName(); err != nil {
		return KedgeID{}, err
	}
	return ki, nil
}

type KedgeSource interface {
	ReadSummary(ctx context.Context, name string) (string, error)
	Discover() iter.Seq[KedgeID]
	TmuxInfo(name string) (workingDir string, cmd string, env map[string]string)
}
