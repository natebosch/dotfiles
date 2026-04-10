package cmd

import (
	"os"
	"path/filepath"
	"testing"
)

func TestRegisterExternalCommands(t *testing.T) {
	tempDir := t.TempDir()

	pluginPath := filepath.Join(tempDir, "kedge-testplugin")
	err := os.WriteFile(pluginPath, []byte("#!/bin/sh\necho test\n"), 0755) //nolint:gosec // test plugin needs execution bits
	if err != nil {
		t.Fatalf("failed to write test plugin: %v", err)
	}

	// registerExternalCommands reads PATH at call time.
	// We use t.Setenv to mock the PATH for this test.
	t.Setenv("PATH", tempDir)

	registerExternalCommands()

	found := false
	for _, c := range rootCmd.Commands() {
		if c.Name() == "testplugin" {
			found = true
			break
		}
	}

	if !found {
		t.Errorf("expected 'testplugin' to be registered as an external command, but it was not found")
	}
}
