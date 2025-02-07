import logging
import os
import sys
import time
from pythonjsonlogger import jsonlogger
from ddtrace import tracer, config
from ddtrace.sampler import DatadogSampler

tracer.configure(sampler=DatadogSampler())
config.httpx_client.distributed_tracing = True

app_verbose = bool(os.getenv("APP_VERBOSE", False))

FORMAT = ('%(asctime)s %(levelname)s [%(name)s] [%(filename)s:%(lineno)d] '
          '[dd.service=%(dd.service)s dd.env=%(dd.env)s dd.version=%(dd.version)s dd.trace_id=%(dd.trace_id)s dd.span_id=%(dd.span_id)s] '
          '- %(message)s')

formatter = jsonlogger.JsonFormatter()

stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(formatter)

file_handler = logging.FileHandler("/tmp/app.log")
file_handler.setFormatter(formatter)

logging.Formatter.converter = time.gmtime

logging.basicConfig(
    handlers=[file_handler, stream_handler],
    format=FORMAT,
)


def get_logger(name: str):
    log = logging.getLogger(name)
    log.level = logging.INFO
    logging.Formatter.converter = time.gmtime
    return log


def log_debug(log, message):
    if app_verbose:
        log.debug(message)


def log_info(log, message):
    if app_verbose:
        log.info(message)


def log_error(log, message):
    log.error(message)


def log_warn(log, message):
    log.warn(message)

# import logging
# import os
# import sys
# import time
# from pythonjsonlogger import jsonlogger

# from app.config import LOG_FILE_PATH

# class LogManager:
#     _instance = None

#     # def __new__(cls, *args, **kwargs):
#     #     if not cls._instance:
#     #         cls._instance = super(LogManager, cls).__new__(cls, *args, **kwargs)
#     #     return cls._instance

#     def __init__(self):
#         if hasattr(self, '_initialized') and self._initialized:
#             return
#         self.app_verbose = bool(os.getenv("APP_VERBOSE", "False") == "True")
#         self.log_directory = LOG_FILE_PATH
#         # create dir if not exists
#         if not os.path.exists(self.log_directory):
#             os.makedirs(self.log_directory)
#         self.log_file = os.path.join(self.log_directory, "app.log")

#         # Setup handlers
#         self.setup_handlers()
#         self._initialized = True

#     def setup_handlers(self):
#         """Sets up the logging handlers and formatters."""
#         FORMAT = (
#             '%(asctime)s %(levelname)s [%(name)s] [%(filename)s:%(lineno)d] '
#             '[dd.service=%(dd.service)s dd.env=%(dd.env)s dd.version=%(dd.version)s '
#             'dd.trace_id=%(dd.trace_id)s dd.span_id=%(dd.span_id)s] - %(message)s'
#         )

#         formatter = jsonlogger.JsonFormatter(FORMAT)

#         # Stream Handler
#         stream_handler = logging.StreamHandler(sys.stdout)
#         stream_handler.setFormatter(formatter)

#         # File Handler
#         if not os.path.exists(self.log_directory):
#             os.makedirs(self.log_directory)
#         file_handler = logging.FileHandler(self.log_file)
#         file_handler.setFormatter(formatter)

#         # Basic Config
#         logging.Formatter.converter = time.gmtime
#         logging.basicConfig(
#             # level=logging.DEBUG if self.app_verbose else logging.INFO,
#             level=logging.INFO,
#             handlers=[file_handler, stream_handler]
#         )

#     def get_logger(self, name: str) -> logging.Logger:
#         """Returns a logger instance with the specified name."""
#         self.logger = logging.getLogger(name)       
#         return self.logger 

#     def info(self, message: str):
#         self.log_info(log, message)

#     def debug(self, message: str):
#         if self.app_verbose:
#             self.log_debug(log, message)

#     def error(self, message: str):
#         self.log_error(log, message)

#     def warn(self, message: str):
#         self.log_warn(log, message)

# # Initialize the logging configuration
# log_manager = LogManager()
