# Documentation Deployment

Builder and player guides are built from `docs/guides/` with VitePress and
deployed by `.github/workflows/docs.yml`. The deployment stays in the main
Written Realms repository; no separate documentation repository is required.

## Local Verification

```bash
make docs-install
make docs-build
npm --prefix docs run preview
```

The production output is `docs/.vitepress/dist/`. The build also generates
static HTML redirects for every route served by the former Doctrine docs app.

## First-Time GitHub Pages Setup

1. In the `writtenrealms/writtenrealms` repository settings, open **Pages**.
2. Select **GitHub Actions** as the publishing source.
3. If GitHub requests organization-domain verification, add its TXT challenge
   for `writtenrealms.com` and wait for verification.
4. Set the Pages custom domain to `docs.writtenrealms.com` before changing DNS.
5. Run the **Deploy documentation** workflow from the Actions tab or merge a
   docs change to `main`.

GitHub requires custom domains for Actions-based Pages deployments to be set
through repository settings or the API; a committed `CNAME` file does not
perform that setup. See GitHub's [custom workflow](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)
and [custom domain](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site)
documentation.

## DNS Cutover

1. Confirm the Pages deployment completed successfully.
2. Replace the existing `docs.writtenrealms.com` A record with this CNAME:

   ```text
   docs CNAME writtenrealms.github.io.
   ```

   The repository name does not belong in the CNAME target.
3. Wait for GitHub's DNS check and TLS certificate provisioning to complete.
4. Enable **Enforce HTTPS** in the Pages settings.
5. Verify the home page, a builder guide, a player guide, and representative
   old routes such as `/building/conditions` and
   `/building/worlds/publishing`.

## Retiring Doctrine

Keep the legacy Doctrine ingress, service, and deployment available until DNS,
TLS, canonical pages, and legacy redirects have been verified from outside the
cluster. Then remove those resources from the infrastructure repository.

If validation fails before Doctrine is retired, restore the previous DNS
record while the Pages configuration is corrected. DNS rollback is no longer
available after the legacy service is removed, so retire it only after the new
site has been observed working reliably.
