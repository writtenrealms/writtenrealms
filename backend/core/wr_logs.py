import time

from pythonjsonlogger import jsonlogger


class WRJsonFormatter(jsonlogger.JsonFormatter):
    def __init__(self, *args, **kwargs):
        super(WRJsonFormatter, self).__init__(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
            rename_fields={
                "asctime": "ts",
                "levelname": "level",
                "name": "logger",
            },
            *args,
            **kwargs,
        )

    def formatTime(self, record, datefmt):
        ct = self.converter(record.created)
        stamp = time.strftime(datefmt, ct)
        return f"{stamp}.{int(record.msecs):03d}"
