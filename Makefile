REDIS = redis

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

build_docs:
	if [ -d static ]; then mkdir -p static/docs ; fi
	ve/bin/sphinx-build docs static/docs

doc_loop:
	bash -c "while [ true ] ; do make build_docs; done"

testreadme:
	ve/bin/python -m doctest README.rst

test:
	docker compose exec backend python manage.py test --settings=config.settings.testing

test-wr2:
	docker compose exec backend python manage.py test wr2_tests --settings=config.settings.testing

.PHONY: docker-up docker-up-mount docker-restart docker-restart-mount

docker-up:
	docker compose up -d --build

# Uses bind mounts for source folders so code changes only need `docker compose restart`.
docker-up-mount:
	COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose up -d --build

docker-restart:
	docker compose restart

docker-restart-mount:
	COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml docker compose restart
