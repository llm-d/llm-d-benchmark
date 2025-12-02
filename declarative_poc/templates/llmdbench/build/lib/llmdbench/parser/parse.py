import yaml
import os
import sys
from jinja2 import (
    Environment,
    FileSystemLoader,
    StrictUndefined,
    Template,
    ChainableUndefined,
)


# Load values.yaml if present
def load_values(path="values.yaml"):
    try:
        with open(path, "r") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


def parse(
    template_dir=None, values_file=None, output_dir="/Users/vezio/IBM/llmd/haul/stack"
):

    # Jinja environment
    env = Environment(
        loader=FileSystemLoader(template_dir), undefined=ChainableUndefined
    )

    # Iterate over all template files
    for filename in os.listdir(template_dir):
        if not filename.endswith((".j2", ".jinja", ".tmpl", ".template")):
            continue

        template = env.get_template(filename)
        print(template)
        rendered = template.render(charts={})
        print(rendered)
