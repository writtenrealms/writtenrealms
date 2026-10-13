# Control Panel development

Open `/staff` (or `/staff/panel`) with a staff account. Operator instructions are in [the administration guide](../guides/administrators/control-panel.md).

```sh
make test
docker compose exec frontend npm test
docker compose exec frontend npm run build
make docs-build
```

The API lives in `backend/system/control_{overview,access,jobs}.py`; admission is shared through `backend/system/access.py`. Migration `system.0027_platform_control_access` adds the access mode, email whitelist, and administrative audit records. Existing environment admission restrictions remain effective for non-staff accounts.

The overview is 10 database queries on a cache miss, independent of result size, and no analytics queries on a cache hit. Regression coverage uses 506 online characters and 56 running worlds, while returning at most 100 characters and 50 worlds. Durable queue summaries use 9 queries, including a regression with 505 failures that returns only 10 failure details. Snapshots are shared for 15 seconds. Game admission caches expire after 5 seconds and retain at most 4,096 entries per process; warm admission adds no database queries.

Queue inspections use a one-second broker socket timeout and a one-second deadline for each worker inspection phase. Separate queue and overview requests prevent a broker failure from blocking the dashboard. Broker output excludes task arguments, results, credentials, and raw exception strings. Recovery submissions are restricted to three existing bounded tasks and have a shared 30-second cooldown.

## Browser checks

With the frontend running, open `/tests/browser/controlPanel.html?test=1`. This renders the real components with synthetic data and intercepts all API requests. It checks rendering, draft preservation between tabs, failed and successful saves, unavailable queue data, audit history, and page overflow. It creates no accounts and changes no backend data. The harness is not a production application route.

Add `&restricted=1` to check a deployment with two invited addresses and an open panel policy. Both status badges must say **Invite only**, the first option must say **Use deployment invitations**, and unsaved edits or unrelated saves must preserve the effective status. The display uses the existing settings response and adds no API requests or admission-path work.

The restricted preview passed all 21 browser checks at 1,280 × 960 and 390 × 844 on October 12, 2026. The unrestricted preview also passed its 13 checks at 390 × 844.

![Access page reflecting deployment invitations, with synthetic data](screenshots/core-control-panel-restricted-access.png)

These screenshots use synthetic data. The browser checks passed at desktop (1,280 × 960) and mobile (390 × 844) sizes:

![Desktop Control Panel with synthetic data](screenshots/core-control-panel-desktop.jpg)

![Access settings with synthetic data](screenshots/core-control-panel-access.jpg)

[Mobile screenshot](screenshots/core-control-panel-mobile.jpg)
