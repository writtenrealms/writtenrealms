"""Shared bounds for authored containers and interactive item transfers."""

MAX_TRANSFER_ITEMS = 4096
DEFAULT_CONTAINER_CAPACITY = 100
# Moving a full container also moves the container itself.
MAX_CONTAINER_CAPACITY = MAX_TRANSFER_ITEMS - 1
STORAGE_CONTAINER_TYPES = ('container', 'trash')


def effective_container_capacity(value):
    # Previously unconfigured runtime items have capacity=0. Treat that as the
    # default, never unlimited, and retain the transfer ceiling for every writer.
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return DEFAULT_CONTAINER_CAPACITY
    return min(MAX_CONTAINER_CAPACITY, value)
