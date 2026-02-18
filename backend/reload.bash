dropdb wrealms
createdb -O django wrealms
psql -d wrealms -U django -f wr-import.psql
