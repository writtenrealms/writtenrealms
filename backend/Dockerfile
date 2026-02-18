FROM python:3.13-slim
WORKDIR /code

# Build argument for Django SECRET_KEY (only used during build for collectstatic)
ARG DJANGO_SECRET_KEY=build-time-secret-key-only-for-collectstatic

# Upgrade pip to the latest version
RUN pip install --upgrade pip

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libffi-dev \
    libpq-dev \
    libzmq3-dev \
    nginx \
    && rm -rf /var/lib/apt/lists/*

# Create a user to run the app
RUN adduser --disabled-password --gecos "" app
RUN chown app:app /code
USER app

# Create a virtual environment and activate it
RUN python -m venv /home/app/venv
RUN chown -R app:app /home/app/venv
ENV PATH="/home/app/venv/bin:$PATH"

# Install requirements
COPY --chown=app:app requirements.txt requirements.txt
RUN pip install wheel
RUN pip install -r requirements.txt

# Install project code.
COPY --chown=app:app backend backend
COPY --chown=app:app fastapi_app fastapi_app

COPY --chown=app:app redis redis
COPY --chown=app:app entrypoint.sh entrypoint.sh

#COPY --chown=app:app scheduler.py scheduler.py
#RUN chmod +x scheduler.py

RUN PYTHONPATH=/code DJANGO_SECRET_KEY=$DJANGO_SECRET_KEY python backend/manage.py collectstatic --noinput --settings=backend.config.settings.k8s
RUN mkdir /code/logs

USER root
RUN mv /code/backend/admin.nginx.conf /etc/nginx/conf.d/default.conf
RUN mkdir -p /run/nginx && \
    chown -R app:app /var/lib/nginx && \
    chown -R app:app /var/log/nginx && \
    chown -R app:app /etc/nginx/conf.d && \
    chown -R app:app /run/nginx
USER app

ENTRYPOINT ["/code/entrypoint.sh"]
CMD ["uvicorn", "backend.config.asgi:application", "--host", "0.0.0.0", "--port", "8000"]
