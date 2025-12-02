import yaml
import copy
import json
import subprocess
import requests
import re


class LiteralStr(str):
    pass


class SystemParser:
    def __init__(self, defaults_file, output_file, scenario_file=None):
        self.defaults = self._load_yaml(defaults_file)
        self.output_file = output_file
        self.scenario = self._load_yaml(scenario_file) if scenario_file else {}

        self._charts_key = "charts"
        self.charts = {}

        self._images_key = "images"
        self.images = {}

        self._system_stack_key = "system"
        self.system_stack = {}

        self._system_prepare_key = "prepare"
        self._system_prepare = {}

        self._system_harness_key = "harness"
        self._system_harness = {}

        self.system_experiments_key = "experiments"
        self.system_experiments = {}

    def _load_yaml(self, file_path):
        """Load YAML file"""
        with open(file_path, "r") as f:
            return yaml.safe_load(f) or {}

    def _merge_lists(self, base_list, override_list):
        """
        Merge lists of dictionaries by 'name' field.
        If items have 'name' field, merge by matching names.
        Otherwise, replace the entire list.
        """
        if base_list and isinstance(base_list[0], dict) and "name" in base_list[0]:
            result = copy.deepcopy(base_list)
            base_map = {item["name"]: idx for idx, item in enumerate(result)}
            for override_item in override_list:
                if "name" in override_item:
                    name = override_item["name"]
                    if name in base_map:
                        idx = base_map[name]
                        result[idx] = self._deep_merge(result[idx], override_item)
                    else:
                        result.append(copy.deepcopy(override_item))

            return result
        else:
            return copy.deepcopy(override_list)

    def _deep_merge(self, base, overrides):
        """
        Recursively merge overrides into base dictionary.
        For lists of dicts with 'name' field, merge by matching names.
        Overrides take precedence over base values.
        """
        result = copy.deepcopy(base)
        for key, value in overrides.items():
            if key in result:
                if isinstance(result[key], dict) and isinstance(value, dict):
                    result[key] = self._deep_merge(result[key], value)
                elif isinstance(result[key], list) and isinstance(value, list):
                    result[key] = self._merge_lists(result[key], value)
                else:
                    result[key] = value
            else:
                result[key] = copy.deepcopy(value)
        return result

    def _get_nested(self, data, path: str):
        """
        Retrieves a nested structure using dotted paths.
        Supports list indexes like key.0.name or key[0].name.
        """
        path = re.sub(r"\[(\d+)\]", r".\1", path)
        parts = path.split(".")

        current = data

        for part in parts:
            if isinstance(current, dict):
                current = current.get(part, {})
            elif isinstance(current, list):
                if not part.isdigit():
                    return {}
                idx = int(part)
                if idx < 0 or idx >= len(current):
                    return {}
                current = current[idx]
            else:
                return {}

        return current

    def _render_template_attribute(self, key):
        render = self._get_nested(self.defaults, key)
        scenarios = self.scenario.get("scenario", [])
        for i, _ in enumerate(scenarios):
            path = f"scenario.{i}.{key}"
            scenario_value = self._get_nested(self.scenario, path)
            render = self._deep_merge(render, scenario_value)

        return render

    def _build_indexes(self):
        """Build lookup dictionaries for all categories"""
        self._indexes = {}
        for category in [self._charts_key, self._images_key]:
            data = getattr(self, category, {})
            self._indexes[category] = {
                item["name"]: item for item in data.get("user-overrides", [])
            }

    def _skopeo_list_tags(self, ref):
        """
        Call: skopeo list-tags docker://ghcr.io/org/image
        Return: list of tags (strings)
        """
        try:
            cmd = ["skopeo", "list-tags", f"docker://{ref}"]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            data = json.loads(result.stdout)
            return data.get("Tags", [])
        except Exception as e:
            raise RuntimeError(f"Skopeo failed for {ref}: {e}")

    def _is_oci_repo(self, url: str) -> bool:
        return url.startswith("oci://")

    def _helm_http_list_versions(self, url, chart_name):
        """
        Given a Helm HTTP repo URL and chart name, return list of versions.
        Uses index.yaml which lives at: <url>/index.yaml
        """
        index_url = url.rstrip("/") + "/index.yaml"
        response = requests.get(index_url, timeout=10)
        if response.status_code != 200:
            raise RuntimeError(f"Failed to fetch {index_url}: {response.status_code}")
        index = yaml.safe_load(response.text)
        entries = index.get("entries", {})
        if chart_name not in entries:
            raise RuntimeError(f"Chart '{chart_name}' not found at {index_url}")
        versions = [entry["version"] for entry in entries[chart_name]]
        return versions

    def _resolve_chart_auto_versions(self):
        items = self.charts.get("user-overrides", [])
        for item in items:
            if str(item.get("version", "")) != ".auto":
                continue
            url = item["url"]
            name = item["name"]
            if self._is_oci_repo(url):
                ref = url.replace("oci://", "")
                tags = self._skopeo_list_tags(ref)
            else:
                tags = self._helm_http_list_versions(url, name)
            if not tags:
                raise RuntimeError(f"No chart versions found for {name}")
            tags.sort()
            latest = tags[-1]
            item["version"] = latest

    def _resolve_image_auto_tags(self):
        items = self.images.get("user-overrides", [])
        for item in items:
            if str(item.get("tag", "")) == ".auto":
                registry = item["registry"]
                repo = item["repo"]
                image = item["image"]
                ref = f"{registry}/{repo}/{image}"
                tags = self._skopeo_list_tags(ref)
                if not tags:
                    raise RuntimeError(f"No tags found for image {item['name']}")
                tags.sort()
                latest = tags[-1]
                item["tag"] = latest

    def _literal_str_representer(self, dumper, data):
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")

    def _convert_multiline_strings(self, obj):
        if isinstance(obj, dict):
            return {k: self._convert_multiline_strings(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._convert_multiline_strings(v) for v in obj]
        if isinstance(obj, str) and "\n" in obj:
            return LiteralStr(obj)
        return obj

    def get_item_by_name(self, category, name):
        """Generic method to get an item by name from any category"""
        if category not in self._indexes:
            raise ValueError(f"Unknown category: {category}")
        return self._indexes[category].get(name)

    def get_chart_by_name(self, name):
        return self.get_item_by_name(self._charts_key, name)

    def get_image_by_name(self, name):
        return self.get_item_by_name(self._images_key, name)

    def plan_to_dict(self):
        return {
            self._charts_key: self.charts,
            self._images_key: self.images,
            self._system_stack_key: self.system_stack,
            self._system_prepare_key: self.system_prepare,
            self._system_harness_key: self.system_harness,
        }

    def plan_to_yaml(self):
        plan = self.plan_to_dict()
        plan = self._convert_multiline_strings(plan)
        with open(self.output_file, "w") as f:
            yaml.dump(
                plan,
                f,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            )

    def parse(self):
        """Load defaults and apply overrides"""
        yaml.add_representer(LiteralStr, self._literal_str_representer)

        self.charts = self._render_template_attribute(self._charts_key)
        self.images = self._render_template_attribute(self._images_key)
        self.system_stack = self._render_template_attribute(self._system_stack_key)
        self.system_prepare = self._render_template_attribute(self._system_prepare_key)
        self.system_harness = self._render_template_attribute(self._system_harness_key)

        self._resolve_chart_auto_versions()
        self._resolve_image_auto_tags()
        self._build_indexes()

        return self.plan_to_dict()
