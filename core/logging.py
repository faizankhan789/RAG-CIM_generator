import logging
import logging.config

from core.config import settings

# Log format includes timestamp, level, module name, and message.
# Module name (%(name)s) maps to the logger created with logging.getLogger(__name__)
# in each file, giving hierarchical names like "services.cim", "core.worker_pool".
_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging() -> None:
    """
    Configure the root logger. Called once at app startup.
    Log level is controlled by the LOG_LEVEL setting in .env.
    """
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": _LOG_FORMAT,
                    "datefmt": _DATE_FORMAT,
                }
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                }
            },
            "root": {
                "handlers": ["console"],
                "level": settings.log_level.upper(),
            },
        }
    )
