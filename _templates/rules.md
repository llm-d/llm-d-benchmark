1 - At the end of the parsing, a single unified "experiment_<experiment id>.yaml" is generated

2 - ALL internal operations will be executed by loading "experiment_<experiment id>.yaml" into a dictionary

3 - During the parsing process, "defaults.yaml" is always and implicitly loaded last

4 - For the construction of "experiment_<experiment id>.yaml", multiple "overrides" can specified (e.g., `-e overrides_for_gaie.yaml -e overrides_for_model_service.yaml`), loaded in sequence of appearance

5 - Every time a value containing `BASE/<path>`, `HOME/<path>` or `/<path>` (i.e., a path starting with a `/`) is assigned for an attribute other than "mount", it will be intrepeted as "open the indicated file, and assign the contents to this attribute"

6 - Every time a value containing an URL is assigned to an attribute other than `url`, the contents are automatically fetched and assigned to the attribute

7 - The list of "factors" and "levels" should be automatically unrolled into a list of "treatments""

8 - The list of "treatments" is then unrolled into a list of items for "standup"

9 - Each item under root key "imports" indicating a file path (again `BASE/<path>`, `HOME/<path>` or `/<path>`) will be loaded as a "full specification" (i.e., same structure) with overrides to the main dictionary

10 - Entries under `import:` root key should be interpreted as "load the contents into a dictionary and perform a `update()` on the dictionary already existing

11 - Entries under `loadfrom:` should be interpreted as "load the contents from file into a separate dictionary, and just assign it to the parent key to `loadfrom`"