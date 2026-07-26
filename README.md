# git-dr-mirror

![CI](https://github.com/NWGKGIT/git-dr-mirror/actions/workflows/ci.yml/badge.svg)

**One-way disaster-recovery mirror of your GitHub repositories to GitLab.**

Every run discovers all of your GitHub repositories (public and private) and
backs them up in two places:

- **Discover.** Lists every repository on your account through the GitHub API.
- **Cache.** Maintains a local cache of bare mirrors (default
  `~/github-backup/<repo>.git`) with the complete history, all branches, and
  all tags.
- **Push.** Pushes each mirror to a project in a GitLab group
  (`gitlab.com/<your-group>/<repo>`), creating projects as needed.

If GitHub disappears tomorrow, you have two full copies of everything: one on
disk, one on GitLab.

## Quick start

> One command sets up everything. Clone the repository and run the installer:

```bash
git clone https://github.com/NWGKGIT/git-dr-mirror
cd git-dr-mirror
./install.sh
```

`install.sh` creates the Python environment, installs the systemd units for
scheduled runs, and launches an interactive setup wizard. The wizard:

1. Checks that your machine can run the tool (OS, Python, git).
2. Creates your `.env` and prompts for your tokens and GitLab group,
   validating each answer as you type it.
3. Verifies the tokens and group access with read-only API calls; nothing is
   created. If a check fails, the wizard explains the problem, lets you
   correct the affected values, and checks again until everything passes.
4. Optionally previews what would be backed up (a dry run).

`install.sh` is safe to rerun at any time and never overwrites an existing
`.env`. To remove the scheduled units, run `./install.sh --uninstall`; the
code, `.env`, and mirrors are left in place.

To rerun the wizard or re-verify your credentials later, from the repository
directory:

```bash
./.venv/bin/git-dr-mirror setup    # interactive setup again
./.venv/bin/git-dr-mirror check    # verify credentials and group access, then exit
```

## Requirements

- A Unix-like OS: Linux, macOS, or Windows via WSL. (The run lock uses
  `flock`, so native Windows is not supported.)
- Python 3.9 or newer, with [uv](https://docs.astral.sh/uv/) or plain
  `python3` + pip/venv. The installer auto-detects and uses what you have.
- `git` on `PATH`.
- A GitHub token and a GitLab token. The wizard states the exact scopes, or
  see [Creating the tokens](#creating-the-tokens).
- systemd for scheduled runs (optional; a cron alternative is documented).

## Manual setup

The wizard is optional. To configure everything by hand:

```bash
git clone https://github.com/NWGKGIT/git-dr-mirror
cd git-dr-mirror
./install.sh </dev/null     # non-interactive: sets up the venv and units only

cp .env.example .env
chmod 600 .env              # the file holds tokens; keep it private
$EDITOR .env                # fill in GITHUB_TOKEN, GITLAB_TOKEN, GITLAB_GROUP

# Verify the credentials and group access:
./.venv/bin/git-dr-mirror check

# See what would happen, without changing anything:
./.venv/bin/git-dr-mirror --dry-run

# Test end-to-end with a single repository first:
./.venv/bin/git-dr-mirror --repo some-small-repo

# Full run:
./.venv/bin/git-dr-mirror
```

### Creating the tokens

> [!TIP]
> **Token Expiration Recommendation**
> To ensure your automated background backups run uninterrupted, it is highly recommended to set your token expiration dates to the **maximum allowed duration** (e.g., 1 year) or **No expiration** (if permitted by your organization).

**GitHub**:
- **Fine-grained token (Recommended)**: [Create here](https://github.com/settings/personal-access-tokens/new)
  - Repository access: _All repositories_
  - Permissions: **Contents: Read-only**, **Metadata: Read-only**
- **Classic token**: [Create here](https://github.com/settings/tokens/new)
  - Scope: **`repo`**

**GitLab**: [Create here](https://gitlab.com/-/user_settings/personal_access_tokens)
- **Fine-grained Token (New UI)**:
  - **Group and project access**: _"All groups and projects that I'm a member of"_
  - **Resource access**:
    - Under **Repository** -> **`Code`**: Select **Push** (or Read & Push)
    - Under **Repository** -> **`Repository`**: Select **Create / Read** (to create missing backup projects)
    - Under **Groups** -> **`Group`**: Select **Read** (to locate target group)
- **Classic Token (Legacy)**: Scopes: **`api`** and **`write_repository`**
- No delete or admin permissions are needed; the tool never deletes anything.

**GitLab group**: create a group once in the GitLab UI (for example
`my-github-backup`) and put its path in `GITLAB_GROUP`. Projects inside the
group are created automatically. The group itself is not, which keeps the
token's blast radius small.

#### Token Expiration & Renewal

Personal Access Tokens (PATs) cannot be renewed automatically. If a token expires, automated runs will exit with an error. 
To renew or rotate your tokens, you do **not** need to edit files manually. Simply run:
```bash
./.venv/bin/git-dr-mirror setup
```
This will launch the setup wizard again, allowing you to quickly update your tokens without interrupting your existing mirrors.

## How it works

A few principles explain everything the tool does:

- **One-way, append-only.** GitHub is the only source of truth. Nothing is
  ever pushed back to GitHub, and nothing is ever deleted from GitLab: the
  codebase contains no DELETE call, and a test enforces this. A repository
  deleted on GitHub simply stops being updated; its backup remains.
- **Idempotent.** Running twice changes nothing the second time. Safe to
  interrupt at any point (Ctrl-C, crash, power loss) and rerun.
- **Fault tolerant.** One repository failing never stops the others.
  Transient API errors are retried with backoff, git operations have hard
  timeouts and a retry, interrupted clones are detected and redone, and
  overlapping runs are prevented by a kernel-managed lock.
- **Secrets stay secret.** Tokens are read from the environment (or a
  `.env` file) and injected into git per-invocation. They never appear in
  command lines, `ps` output, git remote config, logs, or on disk.

### What is backed up

| Backed up           | Ignored                                        |
| ------------------- | ---------------------------------------------- |
| Full commit history | Issues, pull requests, discussions             |
| All branches        | Releases (the tags themselves _are_ backed up) |
| All tags            | GitHub Actions, stars, watchers, wikis         |

By default the scope is repositories **you own**, excluding forks (to save
space). Both are configurable (see [Configuration](#configuration)).

## Configuration

Every setting is an environment variable; a `.env` file in the working
directory is loaded automatically (real environment variables win).
`.env.example` is the fully commented reference.

| Variable             | Default                  | Purpose                                                             |
| -------------------- | ------------------------ | ------------------------------------------------------------------- |
| `GITHUB_TOKEN`       | **required**             | Read access to your repositories                                    |
| `GITLAB_TOKEN`       | **required**             | Create projects and push                                            |
| `GITLAB_GROUP`       | **required**             | Existing GitLab group for the backups (subgroups OK: `team/backup`) |
| `GITLAB_URL`         | `https://gitlab.com`     | Self-hosted GitLab supported                                        |
| `GITHUB_API_URL`     | `https://api.github.com` | GitHub Enterprise supported                                         |
| `MIRROR_DIR`         | `~/github-backup`        | Local bare-mirror cache                                             |
| `GITHUB_AFFILIATION` | `owner`                  | Widen scope: `owner,collaborator,organization_member`               |
| `INCLUDE_FORKS`      | `false`                  | Also mirror forks                                                   |
| `EXCLUDE_REPOS`      | _(empty)_                | Comma-separated name globs to skip, e.g. `scratch-*,tmp`            |
| `GITLAB_VISIBILITY`  | `private`                | Visibility of created projects                                      |
| `HTTP_TIMEOUT`       | `30`                     | Seconds per API request                                             |
| `HTTP_RETRIES`       | `3`                      | Retries (exponential backoff) for transient API errors              |
| `GIT_TIMEOUT`        | `3600`                   | Seconds per git operation; raise for very large repositories        |
| `LOG_LEVEL`          | `INFO`                   | `DEBUG`, `INFO`, `WARNING`, `ERROR`                                 |

### Commands and flags

The executable is installed inside the clone at `./.venv/bin/git-dr-mirror`.

```text
git-dr-mirror setup                                 interactive first-time setup
git-dr-mirror check                                 verify credentials and group access
git-dr-mirror [--dry-run] [--repo PATTERN] [--env-file PATH] [--version]
```

- `setup`: walk through configuration, write `.env`, and verify tokens.
- `check`: run the read-only credential and group checks, then exit (no
  prompts, CI-friendly).
- `--dry-run`: discover and report; change nothing anywhere.
- `--repo PATTERN`: process only repositories matching the glob.
- `--env-file PATH`: read configuration from a specific dotenv file.

Exit codes: `0` all good (also when another run already holds the lock),
`1` at least one repository failed (or a `check` failed), `2` configuration
error, `130` interrupted.

## Deployment

`./install.sh` configures a systemd **user** timer that runs the backup
every 6 hours, wherever you cloned the repository. The same setup works on a
laptop and on an always-on VPS; the only difference is one command (see
below).

What the installer configures:

- `~/.config/systemd/user/git-dr-mirror.{service,timer}`, generated with
  the absolute path of your clone. No editing needed.
- `Persistent=true` in the timer: if the machine was shut down or asleep
  when a run was due, systemd fires the missed run once, shortly after the
  next boot. Runs never pile up; a long-powered-off laptop quietly catches
  up.
- The service runs with `Nice=10` and idle I/O priority, so a backup never
  makes the machine feel slow.

```bash
systemctl --user list-timers git-dr-mirror.timer   # next/last run
systemctl --user start git-dr-mirror.service       # run once now
journalctl --user -u git-dr-mirror -f              # logs
```

To change the schedule (default `OnCalendar=00/6:17:00`, every six hours),
use a drop-in override. It survives reruns of `install.sh`, which regenerate
the base unit files:

```bash
systemctl --user edit git-dr-mirror.timer
```

and enter, for example for daily at 03:17:

```ini
[Timer]
OnCalendar=
OnCalendar=*-*-* 03:17:00
```

(The empty `OnCalendar=` line clears the default before setting yours.)

### Running on a VPS

The installation is identical: clone and run `./install.sh`. Additionally,
make sure user services keep running after you disconnect:

```bash
loginctl enable-linger $USER
```

(The installer reminds you if lingering is off.) For extra isolation you can
create a dedicated user account and install under it; nothing in the tool
requires your main account or root.

### Alternative: cron (no systemd)

```cron
# m  h        dom mon dow  command
17 */6 * * *  cd /path/to/git-dr-mirror && ./.venv/bin/git-dr-mirror >> ~/.local/state/git-dr-mirror.log 2>&1
```

Plain cron has no equivalent of `Persistent=true`: runs missed while the
machine was off are skipped until the next scheduled slot (`anacron` can fill
that gap). The tool itself does not care; any run always brings the backup
fully up to date, however long the machine was off.

## Failure handling and recovery

Scenarios this tool is designed to survive:

- **Machine powered off for days or weeks.** The next boot triggers one
  catch-up run (`Persistent=true`); the run transfers whatever changed in
  the meantime. Nothing special happens, by design.
- **Run interrupted mid-clone** (Ctrl-C, crash, power loss). The
  half-written directory fails a health check on the next run, is
  quarantined as `<repo>.git.corrupt` (never deleted), and the repository is
  recloned fresh.
- **One repository fails** (auth, network, GitLab hiccup). The failure is
  logged, the remaining repositories still run, and the process exits
  non-zero so systemd/cron mark the run failed. Check `journalctl` for the
  `FAILED` lines.
- **Two runs at once** (manual + timer). The second exits immediately and
  cleanly. A kernel `flock` guarantees a crashed holder cannot leave a stale
  lock behind.
- **Transient API failures and rate limits.** Retried with exponential
  backoff, honoring `Retry-After`.
- **Hung network.** Every API call and git operation has a hard timeout.

## Restoring from the backup

Each GitLab project is a complete mirror of the GitHub repository at the
last successful run: full history, all branches, all tags.

```bash
# Get a working copy of the code:
git clone https://gitlab.com/<group>/<repo>.git

# Recreate the repository on GitHub (or anywhere):
git clone --mirror https://gitlab.com/<group>/<repo>.git
cd <repo>.git
git push --mirror https://github.com/<you>/<new-repo>.git
```

The local cache (`MIRROR_DIR`) contains the same data as bare repositories
and works offline: `git clone ~/github-backup/<repo>.git`.

## FAQ

**Why doesn't it delete GitLab projects when I delete a GitHub repo?**
That is the point of disaster recovery: deletion, accidental or malicious,
is one of the disasters. Deleted repositories stop updating but their
backups survive. Clean them up manually if you truly want them gone.

**Are deleted _branches_ also kept?**
No. Branch and tag deletions on GitHub propagate to the mirror on the next
run. The mirror reflects the current state of the repository's refs; the
no-delete guarantee applies to repositories/projects, not individual refs.
(History reachable from remaining refs is always intact.)

**Why must the GitLab group already exist?**
Creating top-level groups is restricted on gitlab.com, and requiring an
existing group keeps the token's permissions minimal and the blast radius
small.

**Does it push GitHub pull-request refs (`refs/pull/*`)?**
No. Only branches and tags are pushed. GitLab rejects writes to hidden refs,
and PR refs are GitHub-internal. They _are_ kept in the local mirror cache.

**My repo name is valid on GitHub but the GitLab project has a suffix. Why?**
GitLab project paths are stricter than GitHub repo names: they cannot start
or end with `-`, `_` or `.`, and cannot end in `.git` or `.atom`. When a
name breaks those rules (e.g. `myrepo-`), the tool derives a valid path by
cleaning the name and appending a short hash of the original
(`myrepo-6bff2b2`) so two different GitHub repositories can never collide on
the same GitLab project. The project's _display name_ keeps the original
repository name.

**What about GitHub wikis?**
Not currently backed up (a wiki is a separate `<repo>.wiki.git` repository).
PRs welcome.

**Private repos on GitHub become what on GitLab?**
`private` projects (the default `GITLAB_VISIBILITY`). Public repositories
also become private on GitLab unless you change that; a backup has no reason
to be public.

**If I rename a GitLab group, do old backup URLs still work?**
Renaming a group changes its path, and therefore the URL of every project
under it. GitLab may redirect the old path for a while, but do not rely on
it. Update `GITLAB_GROUP` and any links you kept.

## Troubleshooting

- `Configuration error: Missing required configuration: ...`: set the
  variable in `.env` (same directory you run from) or the environment, or
  rerun `./.venv/bin/git-dr-mirror setup`.
- `GitLab group '...' does not exist`: create the group in the GitLab UI, or
  fix `GITLAB_GROUP`; also check the token can see the group.
  `./.venv/bin/git-dr-mirror check` pinpoints which credential is the
  problem.
- `git clone/push failed ... HTTP 401/403`: token expired or missing a
  scope (GitHub: Contents read; GitLab: `write_repository` + `api`).
- `Another backup run is in progress`: expected; the other run finishes the
  job. If you are sure nothing is running, note the lock cannot be stale (it
  dies with its process); look for a live process with
  `pgrep -af git-dr-mirror`.
- A repository keeps failing while others work: run it alone with
  `LOG_LEVEL=DEBUG ./.venv/bin/git-dr-mirror --repo <name>` and read the
  output.
- Timer did not fire after boot: check `systemctl --user list-timers` and
  that lingering is enabled (`loginctl enable-linger $USER`) if you were not
  logged in.

## Development

```bash
./install.sh                   # or: uv sync / pip install -e .
uv run pytest                  # run the test suite (or: ./.venv/bin/pytest)
uv run ruff check .            # lint
uv run ruff format --check .   # formatting
```

The test suite mocks all HTTP and git subprocess calls; it never touches
GitHub, GitLab, or your mirrors. CI runs the suite on Python 3.9 through
3.13 on Linux and macOS, plus ruff, shellcheck, and an installer smoke test.

Layout: `src/git_dr_mirror/`: `config` (env parsing), `github` (discovery),
`gitlab` (project ensure), `mirror` (git operations), `lock` (single-run
flock), `preflight` (read-only credential checks), `wizard` (interactive
setup), `runner` (orchestration), `cli` (entry point).

## License

MIT
