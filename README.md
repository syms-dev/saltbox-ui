# saltbox-ui

An unofficial installer and web dashboard for [Saltbox](https://github.com/saltyorg/Saltbox).

It sits on top of an unmodified Saltbox install. It reads Saltbox's own
playbook, schemas and role defaults, and installs, updates and backups all go
through Saltbox's `sb` command, so Saltbox's own checks and locking still
apply. It never calls Ansible itself. In Saltbox's own checkout it only writes
the config files and the inventory you edit through it.

`sb` has no command for a few things, so those are done directly, and only
when you ask:

- starting, stopping, restarting and removing containers with Docker;
- deleting an uninstalled app's folder, and restarting the server;
- settings made through the apps' own APIs (Connect apps, seeding limits, disc
  images, and Prowlarr's app list when you uninstall something);
- Recyclarr's two config files, a removed app's section in Unpackerr's config,
  and an empty anchor file for Autoscan;
- for the web app itself, a Traefik route and a systemd unit.

**This is not part of Saltbox, and the Saltbox team does not support it.** If
something here breaks, open an issue in this repository, not in Saltbox's
Discord or issue tracker.

I wanted to install and look after my own Saltbox server without living in a
terminal, so I built this for fun. I wrote it with AI assistance; [How I built
it](#how-i-built-it) explains how.

## What it does

### Terminal installer

`saltbox-ui` is a wizard that takes a fresh Ubuntu server through preflight
checks, account and domain, DNS and certificates, choosing apps, storage
(rclone remotes, including headless sign-in), linking Plex, and the install
itself. It saves progress after every step, so it picks up where it left off
after a reboot or a dropped SSH session.

### Web app

It runs at `https://saltbox.<your domain>`, behind Traefik and Authelia.

Home starts with whatever needs you, each item with the button that fixes it:
restart an unhealthy app, start a stopped one, apply updates, open a config
file with errors, retry a failed job, start the VPN, or restart the server
when Ubuntu's updates need it (it tells you which package asked). Below that
is media at a glance: the VPN (public IP, forwarded port, what goes through
it), downloads in qBittorrent and SABnzbd (and how much is still seeding after
it finished), what's playing on Plex, and what Sonarr and Radarr expect this
week. Then a launcher for your apps, and updates, storage and activity.

Apps lists what's installed, with health, update and restart buttons, and a
Discover tab for installing anything in the Saltbox and Sandbox catalogs.
Saltbox's own core (Traefik, Authelia) is listed too, marked Core, so you don't
miss its updates. You can uninstall an app you no longer use: its containers
go, and its settings folder too if you tick the box. Other apps that still
point at it get updated as well: its section in Unpackerr's config (Saltbox
writes that file once and never touches it again) and its entry in Prowlarr's
app list. Each app has its own page with its containers (restart, stop, start,
CPU and memory), live logs, inventory overrides and role defaults, docs and
links.

Activity shows every job with a live log. A failed job says which task failed
and why, and you can run it again.

System shows CPU and memory, with every container sortable by name, CPU or
memory, and each drive and pool once. With Scrutiny installed you also get
every drive's health, temperature and age, and for a failing drive, which
attribute it fails on (that one also goes to the top of Home). There are
mounts and Saltbox services, Tailscale (including opening this app over it),
backups (`sb install backup` on demand), and Saltbox's own version with
`sb update`. That update shows its announcements alongside the job and waits
for your yes or no before running migrations.

System also lists errors that keep coming back in the apps' logs, one card
each. Known ones come with what they mean and advice that depends on whether
the server has cloud storage, plus a fix button where the fix is safe. You can
mark an issue fixed, which hides it until the app logs it again (then it comes
back and says so; using a fix button marks it for you). An issue that hasn't
shown up for six hours moves out of the way by itself. Log and drive issues
can also be dismissed for good, though a dismissed drive comes back if another
attribute starts failing. With Claude Code installed on the server you can ask
Claude to explain any log issue; see [Explanations by
Claude](#explanations-by-claude).

Automation sends alerts to your phone through Apprise (the notification tool
Saltbox already installs; `apprise:` in accounts.yml is the default address)
when an app turns unhealthy or stops, a job fails, a disk passes 90%, updates
arrive or the VPN has a problem. Nightly maintenance updates the apps that
have new images (Traefik and Authelia included, minus any you exclude) and
sends you one summary. It leaves Saltbox itself alone, because `sb update` is
meant to be run by a person.

Connect apps sets up quality profiles through Recyclarr (TRaSH Guides
profiles, written to `/opt/recyclarr` with API keys read from the apps). It
counts how many shows and films use each profile and has a button to move
them all onto a synced one, since a sync creates profiles but assigns nothing.
It also finds films Radarr has as disc images (.iso, .img), which Plex and
Bazarr can't read, and can have Radarr find proper copies, either keeping each
image until a replacement arrives or deleting them first. After that comes the
post-install wiring the Saltbox app docs describe: download clients, root
folders, Prowlarr sync, FlareSolverr, Autoscan, Plex libraries and settings.
A Seeding card shows finished torrents still seeding in qBittorrent and sets
the ratio and time limits that stop them, which is what lets Sonarr and
Radarr remove them. The steps you have to do by hand in Bazarr and Seerr are
checked from those apps' settings files.

Config is the YAML files `sb` reads, with the same validation as
`sb validate-config`.

Ctrl+K (⌘K) or `/` opens a search for any app, page or action.

When the server stops answering (a reboot, or Traefik or Authelia being
recreated), the page waits, reconnects by itself, signs you in again if it has
to, and after a boot shows the containers coming back.

### Command line

`sbx` is a JSON command line over the same engine, for scripting.

## Status: what has and hasn't been checked

Version 0.2.5. Treat it as a beta.

| Check | Result |
| --- | --- |
| Python tests (pytest) | 413 tests; pass on Python 3.12 and 3.14 |
| Web unit tests (Vitest) | 100 tests pass |
| End-to-end tests (Playwright, Chromium) | 66 tests pass: every flow on desktop, every page at phone and tablet width, failing on any console error |
| Package | CI builds the wheel, installs it in a clean environment and loads the web app from it |
| Real servers | **One**: Ubuntu 26.04, Python 3.14, local drives pooled with mergerfs, Plex, qBittorrent behind Gluetun (ProtonVPN) |

The end-to-end tests run the real daemon and web app against a throwaway box
where only `sb`, `docker`, `tailscale`, `apprise`, the image registry and the
apps' HTTP APIs are faked (`tests/e2e_box.py`). That shows the UI and daemon
work together. It can't show they match every real server, and on my server
these bugs got past the tests and turned up in use:

- installs from the web app failed because systemd starts services without
  `HOME`, and because git refuses a repository owned by another user;
- qBittorrent 5.1 answers a successful login with 204, not "Ok.";
- Plex ignores writes to a library's `/prefs` endpoint;
- a VPN warning fired because Docker records `container:<id>`, not
  `container:<name>`, for apps inside Gluetun's network;
- Autoheal was missing from the app list because it keeps no folder in `/opt`;
- `sb update` refuses to run without a terminal, shows announcements in a
  full-screen viewer that waits for a key, and asks whether to run migrations,
  none of which the stand-in for `sb` did;
- new images for Traefik and for the Redis that Authelia runs were counted on
  Home until the apps list loaded and then dropped, because no listed app owned
  those containers.

Each one is fixed and has a test now. Expect more like them on setups that
don't look like mine. These have not run on a real server yet:

- an `sb update` that brings announcements or asks about migrations. A plain
  `sb update` has run from the web app on my server; the announcement and
  migration handling follows sb's source code (`cmd/update.go`,
  `saltbox/announcements.go`) and is tested against a stand-in that behaves the
  same way at the terminal;
- uninstalling an app, and restarting the server from the web app; the
  clean-up of Unpackerr's config and Prowlarr's apps on uninstall (tested
  against Saltbox's own Unpackerr template and a stand-in for Prowlarr);
- finding and replacing disc images (written from Radarr's source and tested
  against a stand-in for its API);
- reconnecting after a real reboot or a real Traefik restart (tested by making
  the daemon unreachable in a browser test, not by restarting anything);
- the fix that creates Autoscan's anchor file;
- explanations by Claude (the command line and its flags come from Claude
  Code's documentation, and it's tested against a stand-in, not the real CLI);
- the Saltbox UI update button (tested against a local git repository and a
  stand-in for npm; it hasn't updated a real install);
- moving shows and films onto a synced quality profile (the editor calls come
  from Sonarr's and Radarr's source; tested against stand-ins);
- drive health from Scrutiny (written from Scrutiny's source and tested against
  a stand-in for its API);
- the log problem finder. Its two app-specific explanations (Autoscan's anchor
  file, Bazarr and disc images) come from log lines on my server and are
  tested against those lines, but it hasn't run there;
- the seeding limits (written from qBittorrent's API documentation and
  Sonarr's source, and tested against a stand-in for qBittorrent);
- installing this package from a release wheel (my server runs it from a
  source checkout);
- the admin check behind a real Traefik and Authelia (tested with the headers
  they send, and against Traefik's source);
- cloud storage through the rclone wizard;
- Ubuntu 22.04 and 24.04;
- Jellyfin or Emby in place of Plex, and authentik in place of Authelia;
- more than one instance of an app (`sonarr4k` and the like);
- nightly maintenance, Back up now, and access over Tailscale.

If you try any of these, I'd like an issue saying how it went, good or bad.

## Install

You need a server that has run Saltbox's own dependency script, so that `sb`
and `/srv/git/saltbox` exist; the install section of the
[Saltbox docs](https://docs.saltbox.dev) covers it. On a new server, stop after
that script and let the wizard do the rest. A server that already runs Saltbox
needs nothing else.

Install the wheel from this repository's Releases page with pipx:

```
sudo apt install -y pipx
V=0.2.5
sudo PIPX_HOME=/usr/local/pipx PIPX_BIN_DIR=/usr/local/bin pipx install \
  "https://github.com/syms-dev/saltbox-ui/releases/download/v$V/saltbox_ui-$V-py3-none-any.whl"
```

`PIPX_HOME` and `PIPX_BIN_DIR` put the code in `/usr/local/pipx` and the
commands in `/usr/local/bin`, which is on sudo's path. Both belong to root, and
they need to: the daemon runs as root, so whoever can change its code is
effectively root. Don't use `/opt`, which Saltbox gives to the Saltbox user.
The wheel includes the built web app, so the server doesn't need Node. Each
release also has a `SHA256SUMS` file to check the download against.

Then, on a new server, run the installer:

```
sudo saltbox-ui
```

On a server that already runs Saltbox, switch on the web app:

```
sudo sbx daemon enable
```

That writes a Traefik route (`/opt/traefik/saltbox-ui.yml`) and a systemd unit
(`saltbox-ui.service`), then starts it. The app answers at
`https://saltbox.<your domain>` once Traefik has a certificate.

To update, use the button on the System page when it says a newer Saltbox UI
is out. It installs the newest release wheel (or does a `git pull` and rebuild
when running from a source checkout) and restarts the daemon, and the page
reloads itself on the new version. By hand, install the newer wheel with
`pipx install --force` and run `sudo systemctl restart saltbox-ui`. Saltbox
itself still updates with `sb update`, from the terminal or the System page.

To remove it, `sudo sbx daemon disable` stops the service and removes its
systemd unit, Traefik route, the VPN port-sync timer and the Tailscale address.
Add `--purge` to also delete `/var/lib/saltbox-ui` (API token, job logs, alert
and maintenance settings). Then run `sudo PIPX_HOME=/usr/local/pipx PIPX_BIN_DIR=/usr/local/bin pipx uninstall saltbox-ui`.
Saltbox, its config and your apps stay as they are.

## Security model

The daemon runs as root, because `sb` does, so the whole question is who can
reach it and what it believes. To report a problem, see
[SECURITY.md](SECURITY.md) and please do it privately, not in a public issue.

### Who is root-equivalent by design

The web app lets its one user edit Saltbox's configuration and run `sb`, which
is Ansible as root. So the signed-in Saltbox admin can do anything root can,
and there's no lesser role. The same goes for the Saltbox user on the server
(in the `docker` group, and normally with `sudo`), and for whoever owns the
folder the app's code lives in. Install it where only root can write (the
commands above do), not under `/opt`, which belongs to the Saltbox user.

### Who gets in

The daemon listens on 127.0.0.1, on the Docker bridge address Traefik uses to
reach it, and on two Unix sockets. Which one a connection arrived on decides
what it has to show, on every `/api` request, HTTP or WebSocket.

On 127.0.0.1 it needs a bearer token kept in
`/var/lib/saltbox-ui/api-token`, which only root can read. That token is for
tools on the server itself and is never handed to anything else.

On the Docker bridge address it needs a second token, which Traefik adds after
single sign-on, so a browser never holds it. That token isn't much of a
secret, because Traefik shows its configuration, this header included, to
every container on its network. So the daemon only accepts it on that address,
only from the address Docker gave the Traefik container, and never without a
signed-in admin. It doesn't work on 127.0.0.1.

That still isn't enough on its own. A container can read the token out of
Traefik, and a Traefik router an app defines in its own labels can reach the
daemon's service and set the `Remote-User` and `Remote-Groups` headers itself,
skipping single sign-on. So the daemon doesn't trust those headers. On a
proxied request it takes the request's own session cookie to the sign-on's
verify endpoint (Authelia, over the Docker network, never by name or DNS) and
believes only the user it answers with. A label router, or a container that
forges Traefik's address, cannot produce the admin's session cookie, so it
cannot get in. Only the user from accounts.yml or a member of the `admins`
group is let in; other single sign-on users are refused, and so is any request
a bypass rule let through without signing in.

If you use authentik instead, set `SALTBOX_UI_SSO=authentik` for both the
daemon and `sbx daemon enable`. If the sign-on's container isn't named
`authelia` or `authentik`, name it with `SALTBOX_UI_SSO_CONTAINER`.
`SALTBOX_UI_VERIFY_SESSION=off` goes back to trusting the headers (only if the
daemon can't reach the sign-on), `SALTBOX_UI_ADMIN_CHECK=off` turns the admin
check off and `SALTBOX_UI_PROXY_PEER=off` accepts Traefik's token from any
address. I don't recommend any of these.

Over Tailscale (optional), `tailscale serve` proxies port 8443 of the server's
tailnet address to one of the Unix sockets, and the daemon only lets in the
Tailscale account that owns the server, refusing other tailnet users and
tagged devices. It needs MagicDNS and HTTPS certificates turned on in the
Tailscale admin console.

### Other web pages

A request that changes anything is refused when the browser says another site
started it, so a page on a sibling subdomain (which shares the sign-on cookie)
can't start jobs. The page can't be put in a frame, loads scripts, styles and
images only from the server itself, and API answers are never cached. App
icons are bundled with the web app rather than fetched from each app, because
the browser would send the sign-on cookie along with such a request.

### Containers aren't trusted

An app's container controls what goes in its log, what answers on its port
and what's in its folder under `/opt`, so the daemon treats all of that with
suspicion.

- Files in app folders are opened without following symbolic links, must be
  ordinary files of a sane size, and are written through the open folder with
  the owner set on the file itself. The daemon won't read or write through a
  link a container left for it.
- Requests to an app go to that container's address on the Docker network and
  nowhere else. A redirect is followed only to the same host and port, answers
  are limited in size and time, and a container's address comes from
  `/etc/hosts` or Docker, never from DNS.
- Git runs in Saltbox's checkouts as their owner, not as root, because a
  repository's own configuration can name commands for git to run.
- Whatever a container prints is shown as plain text, in the browser and in
  the terminal, with no markup and no escape sequences.

### Secrets

Config files are shown with placeholders such as `‹hidden:user.pass›`, which
are put back when you save. Known passwords, tokens and API keys are replaced
in job logs, container logs, setup instructions, and anything sent to Claude.
"Show secrets" reveals them when you ask. There are two limits. A secret
written in the YAML as a block (`key: |`) or an inline map (`{key: value}`) is
hidden from logs but shown as-is in the config editor. And a secret the app
doesn't know about (a tracker passkey inside a torrent's address, say) isn't
recognised anywhere. Job records, logs and settings are readable by root only.

### Updates

The update button only installs this project's own release wheel from GitHub,
over HTTPS, and only when it matches the checksum GitHub publishes for it. pip
then fetches any changed dependencies from PyPI. There's no signature beyond
that, so whoever can publish a release of this repository can run code as root
on servers that press the button. From a source checkout, the update pulls as
the checkout's owner and then builds as root, which is why that owner counts
as root above.

### Explanations by Claude

Each issue found in the logs gets an "Ask Claude" button once
[Claude Code](https://code.claude.com/docs/en/setup) is installed on the server
and signed in for the Saltbox user (`claude auth login`, over SSH). Pressing it
shows you what will be sent and asks first.

What's sent is the error and about 25 log lines around it, to Anthropic, along
with Saltbox's own docs pages for the apps involved: the app that logged it and
any app its log mentions. A copy of the written part of Saltbox's docs (about
450 KB, built by `scripts/build_docs.py`) ships with the app, and a question
gets at most 24 KB of it. Passwords and keys the app knows about are masked
first; file names, addresses and anything else in those lines are sent as
they are.

Claude can only answer. It runs with every tool switched off, as the Saltbox
user rather than root, in an empty folder. Its answer is shown as text and
nothing in it gets run.

It uses whatever Claude Code on the server is signed in with, a Claude plan or
an API key. This app doesn't sign you in and never sees a credential.
Anthropic's Agent SDK documentation says that, unless approved, third-party
developers may not offer claude.ai login or its rate limits for their
products, and should use API keys instead. If you're unsure how that applies to
you, sign Claude Code in with an API key.

### Security review

Before the first public release I had the code reviewed and attacked as a
whole, with AI-assisted reviews of four areas (the HTTP surface, commands and
files handled as root, requests to other apps, and the web app, installer and
release process), fuzzing of every route on a running daemon, and dependency
and static-analysis scans. It found real holes. The worst let any container on
the Docker network use the API without the token. Each one is fixed and has a
test in `tests/test_security.py` that fails if it comes back. A second round
of review then went after the fixes themselves; it found that the token could
be read out of Traefik (hence the two tokens above) and that some fixes were
incomplete or had broken things that used to work.

This wasn't an independent audit, and the software has run on one server. The
review lowers the odds of a serious hole without ruling one out.

## Development

```
scripts/fetch-saltbox.sh        # the pinned Saltbox commit the tests read, into tests/.saltbox
uv venv .venv && uv pip install --python .venv/bin/python -e ".[dev,docker]"
.venv/bin/ruff check . && .venv/bin/pytest

cd web
npm ci
npm test                        # unit tests
npm run e2e                     # builds the app, starts tests/e2e_box.py, runs e2e/*.spec.ts
```

`python -m tests.e2e_box --port 8790 --token dev` (from the repository root)
starts the same throwaway box to click around in, after `npm run build`.

To point the engine at a Saltbox checkout instead of a live install, set
`SALTBOX_REPO`, `SALTBOX_APPDATA`, `SALTBOX_UI_STATE` and `SB_BINARY`:

```
SALTBOX_REPO=$PWD/tests/.saltbox .venv/bin/sbx catalog apps | head
```

```
saltbox_ui/
  layout.py        paths, mirroring sb's layout, overridable by environment
  yamlio.py        comment-preserving YAML load and save
  schema/          port of sb's YAML schema validator
  config/          the config files and the inventory
  catalog/         playbook roles and tags, role defaults parser, app catalog
  runner.py        sb command builder and streaming job runner with a lock
  state/           containers, image and Saltbox updates, host, Tailscale, backups
  installer/       install plan (resumable), preflight checks, session
  rclone/          rclone's non-interactive config API as a question loop
  appsetup/        post-install wiring of the apps through their own APIs
  tui/             the Textual installer
  daemon/          FastAPI app, routes, jobs, alerts and maintenance, systemd, Traefik
  secrets.py       masking of secrets in what the daemon sends
  media.py         VPN, downloads, Plex sessions, calendar
  recyclarr.py     Recyclarr config from chosen profiles
  cli.py           sbx
web/               React and TypeScript app (Vite); e2e/ holds the Playwright suite
tests/             pytest suite; e2e_box.py is the throwaway box
scripts/           fetch-saltbox.sh; build_catalog.py (app metadata) and build_docs.py (docs for Claude), from the Saltbox docs
```

New pages get a case in `web/e2e/functional.spec.ts` and an entry in the page
list of `web/e2e/layout.spec.ts`.

To release, bump `version` in `pyproject.toml`, then either push a tag
`v<version>` or run the Release workflow by hand from `main` (it creates the
tag). It builds the wheel with the web app inside and attaches it, with its
SHA-256, to a GitHub release.

## How I built it

I built this with spec-driven development and an AI coding assistant. I
decided what it should do and how it should behave, one feature at a time,
and the assistant wrote the code and tests against that. I steered every
change, ran it on my own server, and fed back whatever broke; the bugs listed
under Status are the ones that turned up that way.

Because so much of the code was machine-written, I've leaned on checks rather
than trust: tests at three levels that run in CI on every push, a security
review whose findings were all fixed and given regression tests, every bug
from the real server reproduced in a test before it was fixed, and the list
above of what hasn't been tried yet. Read the Status section before you rely
on it.

## License

GPL-3.0-only, the same license as Saltbox. The app catalog
(`saltbox_ui/catalog/data/apps.json`) and the docs bundled for explanations
(`saltbox_ui/catalog/data/docs.json`) are generated from the Saltbox
documentation. Saltbox is the work of the saltyorg contributors; this project
only drives it.

The app icons in `web/public/icons` come from
[dashboard-icons](https://github.com/homarr-labs/dashboard-icons) (Apache-2.0,
license alongside them), resized by `scripts/build_icons.py`. Each logo is a
trademark of its own project and is used only to show which app is which.
