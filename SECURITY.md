# Security

Saltbox UI runs a daemon as root on your server. A flaw in it matters more than
a flaw in most apps, so reports are very welcome, and they should not start in
public.

## Reporting a vulnerability

Please use GitHub's private reporting: on this repository, **Security**,
**Report a vulnerability**. It opens a thread only you and the maintainer can
see. Do not open a public issue for something that lets someone do what they
should not be able to.

Say what you did, what happened, and what you expected. A request and its
answer, or a few lines of a script, are worth more than a description. You will
get a reply; this is a one-person project, so allow a few days.

This project is not part of Saltbox. Problems in Saltbox itself (`sb`, its
Ansible roles, its Docker images) belong with
[saltyorg/Saltbox](https://github.com/saltyorg/Saltbox).

## Supported versions

Only the newest release is fixed. Update from the web app (System, Update
Saltbox UI) or install the newest wheel.

## What the daemon trusts, and what it does not

The README's "Security" section describes who the daemon lets in and what it
assumes. In short:

- **Root-equivalent by design:** the Saltbox admin signed in through single
  sign-on, root on the server, and the Saltbox user (who is in the `docker`
  group and normally has `sudo`). The web app lets the admin edit Saltbox's
  configuration and run `sb`, which is Ansible as root. There is no lesser
  role.
- **Not trusted:** anyone without the daemon token; signed-in people who are
  not the admin; web pages on other sites, including other apps on the same
  domain; containers (what they print, what they answer on their ports, the
  files in their own folders, their names and labels); what comes back from
  the network.

A way for anything in the second list to do what the first list can do is a
vulnerability. So is a way to read the secrets the server holds without being
in the first list.
