package cmd

import (
	"fmt"
	"os"
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
	if shouldScanPath() {
		registerExternalCommands()
	}
	if err := rootCmd.Execute(); err != nil {
		fmt.Fprintf(os.Stderr, "Error: %v\n", err)
		os.Exit(1)
	}
}

func shouldScanPath() bool {
	if len(os.Args) <= 1 {
		return true
	}

	subcmd := os.Args[1]
	if subcmd == "help" || subcmd == "--help" || subcmd == "-h" || subcmd == "__complete" {
		return true
	}

	for _, c := range rootCmd.Commands() {
		if c.Name() == subcmd {
			return false
		}
		for _, alias := range c.Aliases {
			if alias == subcmd {
				return false
			}
		}
	}

	return true
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
			execArgs := append([]string{fullPath}, args...)
			if err := syscall.Exec(fullPath, execArgs, os.Environ()); err != nil {
				return fmt.Errorf("failed to execute external command %s: %w", fullPath, err)
			}
			return nil
		},
	}
	rootCmd.AddCommand(externalCmd)
}
