# Admin Guides

Run your own Written Realms Core server from the open-source repository.
These guides take you from a fresh clone to your first administrator and
world, then cover hosting and ongoing operations.

## Start your own instance

1. **[Run Core locally](quick-start.md).** Configure the repository, start the
   Docker services, create your administrator, and open your first world.
2. **[Set up sign-in and access](sign-in-and-access.md).** Understand local
   login links, configure email or Google sign-in, and choose who can join.
3. **[Prepare production hosting](production-hosting.md).** Configure your
   domain, HTTPS, database, queues, frontend build, and service startup.
4. **[Operate and back up your server](operations.md).** Check health, inspect
   queues, protect data, and plan upgrades and recovery.

The repository's Docker Compose stack is a **local development installation**.
It is the runnable starting point for a fresh clone. Production requires a
deployment configuration for your own infrastructure; the production guide
describes that configuration and the current gaps in the public tooling.

## Manage the running site

Use the **[Control Panel](control-panel.md)** at `/staff` on your installation
to manage invitations, open building, the main world, maintenance, and jobs.
It is available to staff accounts. World builders manage their assigned
worlds; that role does not grant site administration privileges.

| Task | Guide |
| --- | --- |
| Create the first administrator and sign in | [Quick start](quick-start.md#first-administrator) |
| Invite players or restrict world creation | [Sign-in and access](sign-in-and-access.md#choose-who-can-join) |
| Choose the world shown on the homepage | [Quick start](quick-start.md#first-world) |
| Configure public URLs and WebSockets | [Production hosting](production-hosting.md#public-routing) |
| Inspect activity and recover delayed work | [Control Panel](control-panel.md#queues-and-jobs) |
| Back up or restore an installation | [Operations and backups](operations.md#backups-and-restore) |

## Add your content

A new installation starts without authored worlds or player accounts. Create
a world in the builder, or import an authored world using
[canonical YAML manifests](../builders/yaml-manifests.md#importing-into-a-new-world).
Use the [builder guides](../builders/index.md) for rooms, mobs, quests, and game
rules. A world export moves authored content; it is not an account, character,
inventory, or progress migration. Use database backups to recover your own
Core installation's runtime data.
