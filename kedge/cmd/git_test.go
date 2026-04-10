package cmd

import (
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

func setupTestRepo(t *testing.T, mainBranch string) string {
	repoDir := t.TempDir()
	
	runGit := func(args ...string) {
		cmd := exec.Command("git", args...)
		cmd.Dir = repoDir
		if out, err := cmd.CombinedOutput(); err != nil {
			t.Fatalf("git %v failed: %v\nOutput: %s", args, err, out)
		}
	}

	runGit("init")
	runGit("config", "user.email", "test@example.com")
	runGit("config", "user.name", "Test User")
	runGit("config", "commit.gpgsign", "false")
	
	// Create initial commit on main/master
	err := os.WriteFile(filepath.Join(repoDir, "README"), []byte("initial"), 0600)
	if err != nil {
		t.Fatal(err)
	}
	runGit("add", "README")
	runGit("commit", "-m", "initial")
	
	if mainBranch != "" {
		current, _ := gitCurrentBranch(repoDir)
		if current != mainBranch {
			runGit("branch", "-m", mainBranch)
		}
	}

	return repoDir
}

func TestGitHelpers(t *testing.T) {
	repoDir := setupTestRepo(t, "main")

	// Test gitDefaultBranch
	if got := gitDefaultBranch(repoDir); got != "main" { //nolint:goconst // string used in tests
		t.Errorf("gitDefaultBranch() = %v, want main", got)
	}

	// Test gitCurrentBranch
	if got, err := gitCurrentBranch(repoDir); err != nil || got != "main" {
		t.Errorf("gitCurrentBranch() = %v, %v; want main, nil", got, err)
	}

	// Test gitWorktreeList (only main worktree)
	wt, err := gitWorktreeList(repoDir)
	if err != nil {
		t.Fatal(err)
	}
	if len(wt) != 1 {
		t.Errorf("gitWorktreeList() len = %d, want 1", len(wt))
	}

	// Test gitCheckoutNew
	if err := gitCheckoutNew(repoDir, "feature"); err != nil {
		t.Errorf("gitCheckoutNew() failed: %v", err)
	}
	if got, _ := gitCurrentBranch(repoDir); got != "feature" {
		t.Errorf("gitCurrentBranch() after checkout = %v, want feature", got)
	}

	// Test gitCheckout
	if err := gitCheckout(repoDir, "main"); err != nil {
		t.Errorf("gitCheckout() failed: %v", err)
	}
	if got, _ := gitCurrentBranch(repoDir); got != "main" {
		t.Errorf("gitCurrentBranch() after checkout = %v, want main", got)
	}
}

func TestRunUseGitRepo(t *testing.T) {
	// We need to mock $HOME for this test because runUseGitRepo uses os.UserHomeDir()
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
	
	// Setup real git repo in the mock home
	setupGitInDir := func(dir string) {
		if err := os.MkdirAll(dir, 0755); err != nil {
			t.Fatal(err)
		}
		runGit := func(args ...string) {
			cmd := exec.Command("git", args...)
			cmd.Dir = dir
			if out, err := cmd.CombinedOutput(); err != nil {
				t.Fatalf("git %v failed: %v\nOutput: %s", args, err, out)
			}
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
	}
	setupGitInDir(repoDir)

	projectName := "test-project"
	if err := os.MkdirAll(filepath.Join(projectsDir, projectName), 0755); err != nil {
		t.Fatal(err)
	}

	// Case 1: Simple worktree add
	err := runUseGitRepo(projectName, repoName)
	if err != nil {
		t.Fatalf("runUseGitRepo failed: %v", err)
	}

	targetDir := filepath.Join(projectsDir, projectName, "r", repoName)
	if _, errStat := os.Stat(targetDir); errStat != nil {
		t.Errorf("targetDir %s was not created", targetDir)
	}

	// Case 2: Project branch is already checked out in main repo
	projectName2 := "other-project"
	if errMkdir2 := os.MkdirAll(filepath.Join(projectsDir, projectName2), 0755); errMkdir2 != nil {
		t.Fatal(errMkdir2)
	}
	
	// Checkout other-project in main repo
	if out, errRun := exec.Command("git", "-C", repoDir, "checkout", "-b", projectName2).CombinedOutput(); errRun != nil {
		t.Fatalf("git checkout -b failed: %v, output: %s", errRun, out)
	}

	err = runUseGitRepo(projectName2, repoName)
	if err != nil {
		t.Fatalf("runUseGitRepo failed when branch checked out: %v", err)
	}
	
	// Main repo should have been moved to 'main'
	current, _ := gitCurrentBranch(repoDir)
	if current != "main" {
		t.Errorf("main repo branch = %v, want main", current)
	}

	// Case 3: Project branch checked out, and main is ALSO checked out in another worktree
	projectName3 := "third-project"
	if errMkdir3 := os.MkdirAll(filepath.Join(projectsDir, projectName3), 0755); errMkdir3 != nil {
		t.Fatal(errMkdir3)
	}

	// Let's checkout third-project in repoDir
	if out, errRun := exec.Command("git", "-C", repoDir, "checkout", "-b", projectName3).CombinedOutput(); errRun != nil {
		t.Fatalf("git checkout -b failed: %v, output: %s", errRun, out)
	}
	
	// Let's make main checked out in a worktree.
	mainWT := t.TempDir()
	if out, errRun := exec.Command("git", "-C", repoDir, "worktree", "add", mainWT, "main").CombinedOutput(); errRun != nil {
		t.Fatalf("git worktree add failed: %v, output: %s", errRun, out)
	}
	
	err = runUseGitRepo(projectName3, repoName)
	if err != nil {
		t.Fatalf("runUseGitRepo failed when main checked out elsewhere: %v", err)
	}
	
	// repoDir should have been moved to third-project-kedgebk
	current, _ = gitCurrentBranch(repoDir)
	if current != projectName3+"-kedgebk" {
		t.Errorf("main repo branch = %v, want %s", current, projectName3+"-kedgebk")
	}
}
