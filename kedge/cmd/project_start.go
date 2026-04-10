package cmd

import (
	"bufio"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/pelletier/go-toml/v2"
	"github.com/spf13/cobra"
)

type KedgeConfig struct {
	Description string `toml:"description"`
}

var projectStartCmd = &cobra.Command{
	Use:   "start",
	Short: "Start a new project interactively",
	RunE: func(cmd *cobra.Command, args []string) error {
		reader := bufio.NewReader(os.Stdin)

		fmt.Print("Project name: ")
		name, _ := reader.ReadString('\n')
		name = strings.TrimSpace(name)

		if name == "" {
			return fmt.Errorf("project name cannot be empty")
		}

		// Alphanumeric + dash/underscore validation
		matched, _ := regexp.MatchString("^[a-zA-Z0-9_-]+$", name)
		if !matched {
			return fmt.Errorf("invalid project name: only alphanumeric, dashes, and underscores are allowed")
		}

		fmt.Print("Description: ")
		desc, _ := reader.ReadString('\n')
		desc = strings.TrimSpace(desc)

		home, err := os.UserHomeDir()
		if err != nil {
			return err
		}

		projectDir := filepath.Join(home, "projects", name)
		if _, errStat := os.Stat(projectDir); errStat == nil {
			return fmt.Errorf("project directory %s already exists", projectDir)
		}

		if errMkdir := os.MkdirAll(projectDir, 0755); errMkdir != nil {
			return fmt.Errorf("failed to create project directory: %w", errMkdir)
		}

		config := KedgeConfig{Description: desc}
		configPath := filepath.Join(projectDir, "kedge.toml")

		f, err := os.Create(configPath)
		if err != nil {
			return fmt.Errorf("failed to create kedge.toml: %w", err)
		}
		defer f.Close()

		if err := toml.NewEncoder(f).Encode(config); err != nil {
			return fmt.Errorf("failed to encode kedge.toml: %w", err)
		}

		fmt.Printf("Project %s started at %s\n", name, projectDir)
		return nil
	},
}

func init() {
	projectCmd.AddCommand(projectStartCmd)
}
