import logging
import sys


class StageFormatter(logging.Formatter):
    def __init__(self, stage="RUN", fmt=None, datefmt=None):
        self.stage = stage
        super().__init__(fmt=fmt, datefmt=datefmt)

    def format(self, record):
        record.stage = getattr(record, "stage", self.stage)
        return super().format(record)


def get_logger(name="llmdbench", stage="RUN", level=logging.INFO):
    logger = logging.getLogger(name)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        formatter = StageFormatter(
            stage=stage,
            fmt="%(asctime)s - %(levelname)-8s - %(name)s - %(stage)s - %(message)s",
            datefmt="%H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(level)
        logger.propagate = False
    else:
        for handler in logger.handlers:
            if isinstance(handler.formatter, StageFormatter):
                handler.formatter.stage = stage

    return logger


def set_stage(logger, stage):
    for handler in logger.handlers:
        if isinstance(handler.formatter, StageFormatter):
            handler.formatter.stage = stage
