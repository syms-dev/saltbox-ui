# Changelog

Notable changes to Saltbox UI. Versions match the GitHub releases; dates are UTC.
This file is kept by hand so the history reads the way the work actually went.

## [0.3.0] — 2026-11-17

A redesign: a new look, and a menu built around where people look for things.

### Changed

- **A grouped menu.** Home, Updates and Activity; Apps (Installed, Discover);
  Media (Library, Connect apps); Server (Health, Storage, Network & access,
  Backups, Automation); Settings (Config files). Updates and Health carry a
  count when something is waiting.
- **System is split up.** It held a dozen unrelated things. Load, log problems,
  drive health and Saltbox's services are now on **Health**; disks and mounts
  on **Storage**; Tailscale on **Network & access**; backups on **Backups**.
  Old links to `/system` land on the right page.
- **One Updates page** for everything that can be updated: apps with a newer
  image (each with its version change and its own Update button), Saltbox's
  `sb update`, and Saltbox UI. They used to be on two different pages.
- **Phones get every page.** The bottom bar is Home, Apps, Updates, Activity
  and More; More opens the whole menu. Library, Connect apps and Automation
  used to be reachable only through links on other pages.
- **Home starts with the server at a glance:** uptime, CPU, memory and storage,
  each leading to the page with the detail.
- **A new look ("ops terminal"):** near-black or paper-white surfaces, hairline
  borders, a green accent, page titles and labels in JetBrains Mono and text in
  Inter. It follows the device's light or dark setting. Both fonts are bundled
  (SIL Open Font License), so the page still loads nothing from elsewhere.

### Accessibility

- The theme's text colours meet 4.5:1 contrast against the surfaces they are
  used on, in both themes, and form fields have a 3:1 edge.
- A "Skip to content" link, and after each page change the reading position
  moves to the page's content rather than staying in the menu.
- Buttons and fields grow to 44 px on touch screens.
- The menu says which page is current to screen readers, and the counts are
  read out in words ("6 updates available").
- Reduced motion turns off the remaining animations.

## [0.2.8] — 2026-11-17

### Added

- **Release notes for app updates.** An app with an update waiting has a
  "What's new" card on its page, with the app's own release notes from GitHub
  for every release between the version it runs and the one it would update to
  (betas only when the update is to one). The newest is open; the others fold
  away. It covers Sonarr, Radarr, Prowlarr, Lidarr, Bazarr, Overseerr,
  Tautulli, SABnzbd, NZBGet, Jellyfin, Traefik, Authelia, Gluetun, Unpackerr
  and Scrutiny. Other apps keep the version change and the releases link.
- GitHub is asked only when someone opens an app that has an update, at most
  once every six hours per app (15 minutes after a failure), without signing
  in. The notes are shown as plain text, and only links to the app's own
  GitHub release pages are kept.

## [0.2.7] — 2026-11-17

### Added

- **A newer Saltbox UI shows in the sidebar.** When an update is available, a
  small arrow appears beside the version in the bottom left on every page.
  Hovering says what is available; clicking it opens the update on the System
  page.

## [0.2.6] — 2026-11-17

### Changed

- **A new Library page** holds quality profiles (Recyclarr), disc images and
  seeding limits, which used to sit at the top of Connect apps. They are about
  how your shows and films are handled, not about connecting apps, so they were
  hard to find there. Connect apps now holds only the connections. On a phone,
  the Apps page links to both. Search finds the page under "quality profiles",
  "disc images" and "seeding", and the seeding line on Home opens it. The
  advice shown for disc-image errors in app logs points there too.

## [0.2.5] — 2026-11-17

A bug hunt: eight things noticed in daily use, seven fixed here.

### Fixed

- **Connections no longer list problems for Sonarr, Radarr and Prowlarr that
  are not there.** Saltbox limits the hostnames the *arr apps answer to
  (`AllowedHosts`), and the daemon talked to them by container IP, so each
  request got "400 Bad Request – Invalid Hostname". Requests still go to the
  container's IP but now name the app as Saltbox's own containers do
  (`Host: sonarr:8989`). Errors from an app are shown as plain text rather than
  its HTML error page.
- **Coming up shows what is on its way.** It read the same calendars, so it was
  empty for the same reason. Films are now listed by their next release after
  today (in cinemas, digital or physical, whichever is first) and say which.
  When a calendar cannot be read, the card says which one rather than
  "Nothing monitored".
- **Home and the Apps page load faster.** One request read and parsed the
  Saltbox inventory about two hundred times; it is now parsed once per change
  to the file. The container list comes from two `docker` commands instead of
  one Docker API call per container (about 1.5 s for 22 containers on the test
  box) and is reused for three seconds, cleared whenever a container is
  started, stopped, restarted, removed or installed.
- **A finished update goes away at once.** When an install or update job ends,
  the images it pulled are marked current straight away and every page
  showing apps and updates refreshes. Update buttons are disabled while a job
  runs, so the same update can no longer be started twice.
- The sidebar is a named landmark ("Sidebar"), so screen readers can tell it
  from Home's side rail, and the end-to-end test that reads it no longer
  matches both.

### Added

- **What an update brings:** the app page, the Apps list and the updates card on
  Home show the version an app is on and the one it would move to (for example
  `4.0.10 → 4.0.11`), read from the published image's labels, with a link to
  the app's releases where it has one.

### Changed

- **Discover hides installed apps** by default; untick "Hide installed" to see
  them. Searching still finds everything.
- **Tailscale:** the "Web app over Tailscale" card now gives the address to
  open in a browser. The `ssh` command it used to show moved to its own section,
  "Server terminal over Tailscale".

## [0.2.4] — 2026-11-17

### Security

- **App icons are bundled with the web app** instead of being fetched from each
  app. Loading `https://<app>.<domain>/favicon.ico` made the browser send the
  domain-wide sign-on cookie to every app whenever Home was opened, so a
  compromised app image could have read the admin's session. 191 of the 234
  catalog apps have an icon, taken from
  [dashboard-icons](https://github.com/homarr-labs/dashboard-icons) at a pinned
  commit and re-encoded as small WebP images (never SVG); the rest keep their
  monogram. This closes the known limitation listed under 0.2.3.
- **The page now loads images only from its own origin** (`img-src 'self'
  data:`), which also removes a ready channel for sending data out.

### Changed

- Icons now also show for apps that are not installed yet (the catalog, and the
  Manage and Install entries in search), where there was only a monogram before.

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

### Known limitation (fixed in 0.2.4)

- App icons were still loaded from each app's own address, which sends the
  browser's domain-wide sign-on cookie to that app; a compromised app image could
  read it.

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
