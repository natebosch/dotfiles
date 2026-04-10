## Directories and Files

- `~/repos/<repo>` : the "root" checkout of git repositories
- `~/projects/<project>` : per-project storage
- `~/projects/<project>/metadata.toml` : per-project metadata
- `~/projects/<project>/r/<repo>` : per-project worktrees
- `~/.project_archive/<project>`

## Features to Build

- Workflow to start a project.
  - Should every project require at least one repo? Yes
  - Should every project require a description? Yes
- Workflows while inside a project
  - Adopt a repo?
    - Make a branch for that project.
        - What if already a branch?
    - Create a work tree.
        - What if the base repo is dirty or similar?
  - Automatically associate branches with the project?
- `session_finder` integration
  - Is `tmux` new enough to handle the environment variable argument?
  - What directory should it drop you in if there is more than one repo?
    - If I use the project directory itself I might be able to use symlinks? Is
      the separate location for work trees important? I like that it gives a
      higher potential for integrating citc workspaces. `hg share` might let me
      get a view onto it, but would still need unique tooling. Maybe that's not
      so bad?
- TUI to see a project?
- Archive a project
- Allow configuring the directories like `~/repos` and configure them in
  `home.nix` instead of hardcoding. This should make it easier to do hermetic
  testing.
