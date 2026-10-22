# TU·M·oo·DL
**TUM moodle downloader** - a command-line tool that keeps local copies of your TUM Moodle courses up to date.

- **logs in like a browser, without one**: TUM ID, password and the one-time code from your authenticator app, straight from the terminal. your password goes into the system keychain, and the session is reused until it expires.
- **syncs without losing anything**: new files are added and changed files are updated. a file you edited is never overwritten; Moodle's newer version is saved next to it as `name (moodle).ext`. nothing is ever deleted.
- downloads each course through its Moodle **Download Center**, the same zip you get from the button on the course page.

# install
```sh
uv tool install git+https://github.com/Intex32/tum-moodle-dl
```

# usage
```sh
tmdl login                       # asks for your TUM ID, password and a one-time code
tmdl courses                     # lists your current courses (--all: past and future too)
tmdl courses add 12345 analysis  # syncs these courses from now on: an ID or part of the name
tmdl courses remove analysis     # stops syncing a course; its folder stays
tmdl sync                        # downloads new and changed files of the courses you added
tmdl sync 12345                  # syncs one course, added or not
tmdl logout                      # --forget also removes your login details from the keychain
```
`tmdl courses add` also changes a course's settings: `--dir` sets its folder, and `--original-filenames` / `--activity-names` choose how files are named.

# configuration
`tmdl courses add/remove` keep `~/.config/tum-moodle-dl/config.toml` up to date (or the file given with `--config` / `TMDL_CONFIG`). you can also edit it by hand; tmdl keeps your comments.
```toml
root = "~/Moodle"            # where course folders go (default: ~/Moodle)
original_filenames = false   # name files as uploaded, not after the activity (default: false)

[[courses]]
id = 12345
dir = "analysis"             # optional: folder, relative to root (default: the course's short name)
original_filenames = true    # optional: per-course setting
```

# unattended runs
when the session has expired, `tmdl sync` logs in again by itself, but it needs a one-time code for that. to run it from cron, store your authenticator's secret (the text behind the QR code you scan when setting up an authenticator app, or the `otpauth://` link):
```sh
tmdl login --totp-secret
```
this keeps your password and your second factor on the same machine, so only do it on a machine you trust.

# troubleshooting
- **login stops at an unexpected page**: `tmdl login --debug` saves every login page (secrets removed) to `~/.local/state/tum-moodle-dl/debug/`.
- **you only use a passkey**: tmdl needs an authenticator app as your second factor; add one to your TUM account.
- **coming from the old Selenium version**: add your courses with `tmdl courses add` and delete `config.json`, since it holds your password in plain text. the `replace` update policy is gone: sync never deletes files.

# development
uses [devenv](https://devenv.sh): it provides Python and uv, and keeps the dependencies in sync.
```sh
devenv shell
devenv shell -- pytest
devenv shell -- tmdl --help
```
