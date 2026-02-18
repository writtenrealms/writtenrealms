REMOTE_SSH_HOST=${REMOTE_SSH_HOST:-your-host.example.com}
REMOTE_SSH_USER=${REMOTE_SSH_USER:-deploy}
REMOTE_APP_DIR=${REMOTE_APP_DIR:-/code}
scp ${REMOTE_SSH_USER}@${REMOTE_SSH_HOST}:${REMOTE_APP_DIR}/dumps/wr-import.psql .
dropdb wrealms
createdb -O django wrealms
psql -d wrealms -U django -f wr-import.psql
