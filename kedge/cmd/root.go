package cmd

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"

	"github.com/spf13/cobra"
)

var rootCmd = &cobra.Command{
	Use:   "kedge",
	Short: "Kedge is a git-like CLI tool for project management.",
	Long:  `Kedge is a git-like CLI tool for project management, supporting external subcommands found in $PATH.`,
}

// Execute adds all child commands to the root command and sets flags appropriately.
// This is called by main.main(). It only needs to happen once to the rootCmd.
func Execute() {
	if err := rootCmd.Execute(); err != nil {
		fmt.Fprintf(os.Stderr, "Error: %v\n", err)
		os.Exit(1)
	}
}

func init() {
	// Dynamically register external kedge-* commands from PATH
	registerExternalCommands()
}

func registerExternalCommands() {
	path := os.Getenv("PATH")
	dirs := filepath.SplitList(path)
	registered := make(map[string]bool)

	for _, dir := range dirs {
		files, err := os.ReadDir(dir)
		if err != nil {
			continue
		} 

		for _, f := range files {
			if f.IsDir() {
				continue
			}

			name := f.Name()
			if strings.HasPrefix(name, "kedge-") {
				cmdName := strings.TrimPrefix(name, "kedge-")
				if registered[cmdName] {
					continue
				}

				// Check if we already have a built-in command with this name
				for _, c := range rootCmd.Commands() {
					if c.Name() == cmdName {
						registered[cmdName] = true
						break
					}
				}
				if registered[cmdName] {
					continue
				}

				fullPath := filepath.Join(dir, name)
				if isExecutable(fullPath) {
					addExternalCommand(cmdName, fullPath)
					registered[cmdName] = true
				}
			}
		}
	}
}

func isExecutable(path string) bool {
	info, err := os.Stat(path) //nolint:gosec // path is from walking PATH entries
	if err != nil {
		return false
	}
	// Check if file mode is executable by user
	return info.Mode()&0111 != 0
}

func addExternalCommand(name, fullPath string) {
	externalCmd := &cobra.Command{
		Use:                name,
		Short:              fmt.Sprintf("External command: %s", name),
		DisableFlagParsing: true, // Let the external command handle its own flags
		RunE: func(cmd *cobra.Command, args []string) error {
			execCmd := exec.Command(fullPath, args...) //nolint:gosec // fullPath is from walking PATH entries
			execCmd.Stdin = os.Stdin
			execCmd.Stdout = os.Stdout
			execCmd.Stderr = os.Stderr

			err := execCmd.Run()
			if err != nil {
				var execErr *exec.Error
				if errors.As(err, &execErr) {
					return fmt.Errorf("failed to execute external command: %w", execErr)
				}
				// Forward the exit code if it's an ExitError
				var exitError *exec.ExitError
				if errors.As(err, &exitError) {
					if status, ok := exitError.Sys().(syscall.WaitStatus); ok {
						os.Exit(status.ExitStatus())
					}
					return err
				}
				return err
			}
			return nil
		},
	}
	rootCmd.AddCommand(externalCmd)
}
