# Changelog

Notable changes to Saltbox UI. Versions match the GitHub releases; dates are UTC.
This file is kept by hand so the history reads the way the work actually went.

## [0.2.3] — 2026-11-17

A security and robustness pass. An independent, whole-codebase review drove the
changes; each finding was reproduced before it was fixed, and each fix ships with
a test.

### Security

- **The daemon confirms the sign-on session itself on proxied requests**, rather
  than trusting the `Remote-User` / `Remote-Groups` headers. A Traefik router an
  app defines in its own container labels could otherwise reach the daemon's
  service, add the proxy token (which Traefik shows to every container) and set
  those headers, reaching the root API as an admin without ever signing in. The
  daemon now hands the request's own session cookie to the sign-on's verify
  endpoint and admits only the user it answers with; a label router cannot
  produce the admin's cookie. `SALTBOX_UI_VERIFY_SESSION=off` restores the old
  header-trust behaviour (only if the daemon cannot reach the sign-on) and
  `SALTBOX_UI_SSO_CONTAINER` names the provider's container when it is not the
  default.
- **The terminal installer re-execs through sudo with an isolated interpreter
  (`-I`)**, so root no longer imports a module an app container could plant in
  the current folder.
- **The installer's text widgets strip terminal escape sequences** from app
  output and cloud folder names, so a container cannot repaint root's terminal,
  set its window title or write its clipboard.
- **A recurring error's latest line is masked before it is cut**, so a secret
  that straddles the cut no longer shows its leading characters in the web app or
  in a prompt sent to Claude.
- **rclone's timeout message no longer includes the command**, which can carry a
  typed answer such as a storage password.

### Reliability

- **A container can no longer freeze or exhaust the root daemon with oversized
  input.** The log scanner, the SABnzbd and Bazarr config readers, the container
  registry's challenge parser, the VPN-port reader and piped job output all now
  cap or bound the work they do on data a container controls.
- **A source-checkout update that fails after pulling is finished by the next
  run**, instead of being reported as "already up to date" and leaving the daemon
  to restart without its new dependencies.
- **Smaller fixes:** a non-finite number in an app's answer no longer makes a
  whole page fail; a registry's malformed token answer no longer stops the update
  check for every image; a structured config or inventory save onto a file that
  already has a duplicate key is refused instead of silently dropping the value
  Ansible honours; and a status poll can no longer make a job start fail by
  racing it for the job lock.

### Known limitation

- App icons are still loaded from each app's own address, which sends the
  browser's domain-wide sign-on cookie to that app; a compromised app image could
  read it. Closing this needs the daemon to serve the icons itself, and is
  planned for a later release. Running only trusted app images avoids it in the
  meantime.

## [0.2.2] — 2026-11-16

- Uninstalling an app also removes it from other apps' settings (its Unpackerr
  section, its Prowlarr application).
- The log-problem explainer is given the Saltbox documentation for the app it is
  explaining.
- Find films that Radarr or Sonarr hold as disc images (`.iso`, `.img`) and
  replace them with proper releases.
- First public release; the earlier history was squashed.

Earlier releases (0.2.1 and 0.2.0) are on the
[releases page](https://github.com/syms-dev/saltbox-ui/releases).
