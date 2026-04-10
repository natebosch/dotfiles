package cmd

import (
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

func TestProjectStart(t *testing.T) {
	home := t.TempDir()
	os.Setenv("HOME", home)

	reposDir := filepath.Join(home, "repos")
	projectsDir := filepath.Join(home, "projects")
	if err := os.MkdirAll(reposDir, 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(projectsDir, 0755); err != nil {
		t.Fatal(err)
	}

	repoName := "test-repo"
	repoDir := filepath.Join(reposDir, repoName)
	setupTestRepoWithDir := func(dir, branch string) {
		runGit := func(args ...string) {
			cmd := exec.Command("git", args...)
			cmd.Dir = dir
			if out, err := cmd.CombinedOutput(); err != nil {
				t.Fatalf("git %v failed: %v\nOutput: %s", args, err, out)
			}
		}
		if err := os.MkdirAll(dir, 0755); err != nil {
			t.Fatal(err)
		}
		runGit("init")
		runGit("config", "user.email", "test@example.com")
		runGit("config", "user.name", "Test User")
		runGit("config", "commit.gpgsign", "false")
		if err := os.WriteFile(filepath.Join(dir, "README"), []byte("initial"), 0600); err != nil {
			t.Fatal(err)
		}
		runGit("add", "README")
		runGit("commit", "-m", "initial")
		runGit("branch", "-m", "main")
		if branch != "" && branch != "main" {
			runGit("branch", branch)
			runGit("checkout", branch)
		}
	}

	// Case 1: In a repo on a non-main branch. Uses default name.
	featureBranch := "feature-x"
	setupTestRepoWithDir(repoDir, featureBranch)
	
	oldCwd, _ := os.Getwd()
	if err := os.Chdir(repoDir); err != nil {
		t.Fatal(err)
	}
	defer func() {
		if err := os.Chdir(oldCwd); err != nil {
			t.Logf("failed to restore cwd: %v", err)
		}
	}()

	t.Run("DefaultNameInRepo", func(t *testing.T) {
		// Inputs: 
		// 1. Enter (uses feature-x)
		// 2. "A description"
		input := "\n" + "A description\n"
		
		r, w, _ := os.Pipe()
		oldStdin := os.Stdin
		defer func() { os.Stdin = oldStdin }()
		os.Stdin = r
		if _, err := w.Write([]byte(input)); err != nil {
			t.Fatal(err)
		}
		w.Close()

		err := projectStartCmd.RunE(projectStartCmd, nil)
		if err != nil {
			t.Fatalf("RunE failed: %v", err)
		}

		// Verify project exists
		projectDir := filepath.Join(projectsDir, featureBranch)
		if _, err := os.Stat(projectDir); err != nil {
			t.Errorf("Project directory %s was not created", projectDir)
		}

		// Verify worktree exists
		targetDir := filepath.Join(projectDir, "r", repoName)
		if _, err := os.Stat(targetDir); err != nil {
			t.Errorf("Worktree %s was not created", targetDir)
		}
	})

	t.Run("OverrideNameInRepo", func(t *testing.T) {
		// New repo/branch
		repoName2 := "repo2"
		repoDir2 := filepath.Join(reposDir, repoName2)
		setupTestRepoWithDir(repoDir2, "feature2")
		if err := os.Chdir(repoDir2); err != nil {
			t.Fatal(err)
		}

		// Inputs: 
		// 1. "custom-name"
		// 2. "A description"
		// 3. "y" (for worktree)
		input := "custom-name\n" + "A description\n" + "y\n"
		
		r, w, _ := os.Pipe()
		oldStdin := os.Stdin
		defer func() { os.Stdin = oldStdin }()
		os.Stdin = r
		if _, err := w.Write([]byte(input)); err != nil {
			t.Fatal(err)
		}
		w.Close()

		err := projectStartCmd.RunE(projectStartCmd, nil)
		if err != nil {
			t.Fatalf("RunE failed: %v", err)
		}

		projectDir := filepath.Join(projectsDir, "custom-name")
		if _, err := os.Stat(projectDir); err != nil {
			t.Errorf("Project directory %s was not created", projectDir)
		}

		targetDir := filepath.Join(projectDir, "r", repoName2)
		if _, err := os.Stat(targetDir); err != nil {
			t.Errorf("Worktree %s was not created", targetDir)
		}
	})
}
