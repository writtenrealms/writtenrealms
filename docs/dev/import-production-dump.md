# Import a Production `.psql.gz` Dump

This replaces your local DB with a dump file you already have.

## Steps

1. Start database services:
```bash
docker compose up -d db backend
```

2. Recreate the local database in the `db` container:
```bash
docker compose exec -T db sh -lc 'dropdb -U "$POSTGRES_USER" --if-exists "$POSTGRES_DB" && createdb -U "$POSTGRES_USER" "$POSTGRES_DB"'
```

3. Import your dump (replace the filename):
```bash
gunzip -c ./wr-import.psql.gz | docker compose exec -T db sh -lc 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

4. Ensure app schema is current for local code:
```bash
docker compose exec backend python manage.py migrate
```

5. Quick check:
```bash
docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT NOW();"'
```
