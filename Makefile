REDIS = redis
DOCS_PORT ?= 5174
DOCS_MODULES = docs/node_modules/.package-lock.json

run:
	docker compose up backend

dev:
	COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose up backend

demo:
	@echo "Legacy WR1 demo tooling is no longer part of this repo."

shell:
	docker compose exec backend python manage.py shell --settings=config.settings.dev

run_redis:
	rm -f dump.rdb && $(REDIS)/src/redis-server redis/redis.conf

run_wot_redis:
	$(REDIS)/src/redis-server redis-wot.conf

wtest:
	ve/bin/nosetests wot/tests.py


load:
	docker compose exec backend python manage.py loaddata users --settings=config.settings.testing

cleanprocs:
	@echo "No legacy WR1 processes to clean."

css:
	compass watch frontend/

client:
	npm --prefix frontend run dev-local

$(DOCS_MODULES): docs/package.json docs/package-lock.json
	npm --prefix docs ci

docs-install:
	npm --prefix docs ci

docs: $(DOCS_MODULES)
	npm --prefix docs run dev -- --port $(DOCS_PORT)

docs-build: $(DOCS_MODULES)
	npm --prefix docs run build

redis: bin/redis-server
	bin/redis-server

local:
	cp frontend/config.js.local frontend/config.js

bin/redis-server: src/$(REDIS)/src/redis-server
	mkdir -p bin
	cp $< $@

src/$(REDIS)/src/redis-server: src/$(REDIS)/README
	cd src/$(REDIS) && make

src/$(REDIS)/README: src/$(REDIS).tar.gz
	cd src && tar -xvf $(REDIS).tar.gz
	@touch $@ # Ensure we do not untar every time, by updating README time.

src/$(REDIS).tar.gz:
	mkdir -p src
	cd src && wget http://redis.googlecode.com/files/$(REDIS).tar.gz

clean:
	rm -fr bin/redis-server src/$(REDIS)*

#install_redis:
#	if [ ! -d redis-2.6.12 ] ; then \
#		if [ ! -d redis.2-6.12.tar.gz ] ; then \
#			wget http://redis.googlecode.com/files/redis-2.6.12.tar.gz && \
#			tar xzf redis-2.6.12.tar.gz && \
#			rm redis-2.6.12.tar.gz ; fi \
#		&& cd redis-2.6.12 && make ; fi


install:
	if [ ! -d ve ] ; then virtualenv ve -p python3 ; fi
	ve/bin/pip install -r requirements.txt

TEST_PARALLEL ?= 4
DJANGO_TEST = docker compose exec backend python manage.py test --settings=config.settings.testing --noinput

test:
	$(DJANGO_TEST) --parallel $(TEST_PARALLEL) --buffer

test-serial:
	$(DJANGO_TEST)

test-keepdb:
	$(DJANGO_TEST) --parallel $(TEST_PARALLEL) --buffer --keepdb

.PHONY: test test-serial test-keepdb docs docs-install docs-build docker-up docker-up-mount docker-restart docker-restart-mount reset-dev-db reset-dev-db-mount

docker-up:
	docker compose up -d --build

# Uses bind mounts for source folders so code changes only need `docker compose restart`.
docker-up-mount:
	COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose up -d --build

docker-restart:
	docker compose restart

docker-restart-mount:
	COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose restart

reset-dev-db:
	./scripts/reset-dev-db

reset-dev-db-mount:
	./scripts/reset-dev-db --mount
