from rest_framework.throttling import UserRateThrottle, AnonRateThrottle, SimpleRateThrottle
import time

class PlayGameThrottle(AnonRateThrottle):
    """
    Throttle that aims to make sure at least X seconds have elapsed since
    the last request.
    """

    SECS_BETWEEN_REQUESTS = 3 # in seconds, time mandated between requests

    def allow_request(self, request, view):
        # The key will be unique per viewer
        key = self.get_cache_key(request, view)
        if not key: return True

        last_success = self.cache.get(key, None)
        now = time.time()
        if (not last_success or
            now - last_success >= self.SECS_BETWEEN_REQUESTS):
            self.cache.set(key, now, self.SECS_BETWEEN_REQUESTS * 2)
            return True
        return False

    def wait(self):
        return None


class UserThrottle(UserRateThrottle):

    SECS_BETWEEN_REQUESTS = 2 # in seconds, time mandated between requests

    def allow_request(self, request, view):
        uid = request.user.id
        cache_key = f'user-throttle-{uid}'

        last_success = self.cache.get(cache_key, None)
        now = time.time()

        if (not last_success
            or now - last_success >= self.SECS_BETWEEN_REQUESTS):
            self.cache.set(cache_key, now)
            return True
        return False

    def wait(self):
        return None

class EmailThrottle(SimpleRateThrottle):
    scope = 'email'

    def get_cache_key(self, request, view):
        if request.user.is_authenticated:
            ident = request.user.pk
        else:
            ident = self.get_ident(request)
        return self.cache_format % {
            'scope': self.scope,
            'ident': ident
        }
