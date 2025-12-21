# Specification Guidelines

The specification directory contains a number of Jinja files that will be rendered into YAML documents.
Each Jinja file pertains to a specific stack that `llmdbenchmark` will provision that pertains to an optional scenario, and optional
experiment(s) to be run. The below sections will elaborate on this further.

## Jinja Usage

As seen in the templates found in this repository, we make strong use of Jinja, and we will do that here as well with
the intention to push the the rendering to the Jinja library, rather than making that logic hard coded into our tooling.

The specification files here will have a simple templated value `base_dir` - the reason we do this is to allow the user to 
override these files in a structured way - not all users have the same `base_dir` - we want to accomodate those that may
have completely rearranged their structure.

The default value for `base_dir` in these documents is `../` - pertaining to the default structure of this repository (root of the repository).

To override the `base_dir` value, we recommend the user to utilize the `cli flag` provided through `llmdbenchmark --base_dir <directory> <...other cmds...>`

For an example of what the rendered template looks like, albeit simple, the `examples` directory shows a template and fully rendered contents of the respective template.

## Specification Structure

### Content Expectations

The specification directory contains a grouping of YAML documents that will detail the following content per specification:

```yaml
#
# Required
#
base_dir
values_dir
template_dir

#
# Optional
#
scenario_file
experiments
```

### Example of a Template Specification

An annotated example of the template specification template can be observed below for an `inference-scheduling` scenario and experiment:

```yaml

# -------------------
# Required Parameters
# -------------------

# [REQUIRED]
# The base directory to use when finding the subdirectories and files below.
# 
# In this example, we assume we are working from the repository it self, the location of this
# file is nested in a subdirectory, so will appropriately target the root direcotry of the
# repository.
# 
# Note. You can provide absolute paths, or different paths, if you have custom configurations.
# Please do note you will need to adjust the subsequent directories to accomodate your changes if
# those directory locations have also been changed such that it breaks the existing template.
# 

{% set base_dir = '../' -%}

base_dir: {{ base_dir }}

# [REQUIRED]
# Directory containing default values for generating YAMLs
values_dir:
  path: {{ base_dir }}templates/values

# [REQUIRED]
# Directory containing all template files that will be populated from loading
# both the values and scenarios (overrides) files.
template_dir:
  path: {{ base_dir }}templates/jinja
   
# -------------------
# Optional Parameters
# -------------------

# [OPTIONAL] 
# Specific file containing a scenario that will have values that will override the default
# values supplied in the values_dir. 
# 
# If the attribute "scenario_file" is not declared, then the
# default values will be used as the scenario.
scenario_file:
  path: {{ base_dir }}scenarios/inference-scheduling.yaml

# [OPTIONAL] 
# Experiment schema that will generate the cartesian product of all values specified in the below
# section. These values will be used as the final override values in generating the complete set
# of YAML documents to be used during provision and runtime. 
# 
# If the attribute "experiments" is not provided, then it is assumed the user ONLY wants to 
# provision (standup) a stack.
experiments:
  - name: "experiment-1"
    attributes:
      - name: "harness"
        factors:
          - name: data.shared_prefix.question_len 
            levels:
              - 100
              - 300
              - 1000
          - name: data.shared_prefix.output_len
            levels:
              - 100
              - 300
              - 1000
        treatments:
          - "100,100"
          - "100,300"
          - "100,1000"
          - "300,100"
          - "300,300"
          - "300,1000"
          - "1000,100"
          - "1000,300"
          - "1000,1000"

```
