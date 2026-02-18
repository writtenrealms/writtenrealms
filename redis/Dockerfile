# Use the official Redis image as the base image
FROM redis:latest

# Copy the custom Redis configuration file into the container
COPY redis.conf /usr/local/etc/redis/redis.conf

# Specify the Redis port to expose
EXPOSE 6379

# Command to run Redis server with the custom configuration file
CMD [ "redis-server", "/usr/local/etc/redis/redis.conf" ]