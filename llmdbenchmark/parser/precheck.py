from pathlib import Path


def validate_base_dir(base_dir: Path, config: dict):
    """
    A base directory in our context MUST have minimal requirements that are
    met that are specificed via the configuration passed into this function.

    Currently, we are ONLY checking to see if the sub_dirs exist. This function
    may be expanded to enhanced checks.

    config = {
        "templates": "templates",
        "scenarios": "scenarios"

    }
    """


def validate_specification():
    pass
