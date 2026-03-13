"""Resolve ``"auto"`` image tags and chart versions via skopeo/helm."""

import json
import subprocess
from copy import deepcopy


class VersionResolver:
    """Resolve ``"auto"`` image tags (skopeo/podman) and chart versions (helm)."""

    def __init__(self, logger, dry_run: bool = False):
        self.logger = logger

    def resolve_image_tag(self, registry: str, repository: str) -> str:
        """Resolve the latest tag for an image via skopeo, falling back to podman."""
        image_ref = repository
        if registry and not repository.startswith(registry):
            image_ref = f"{registry}/{repository}"

        self.logger.log_info(f"🔍 Resolving image tag for: {image_ref}")

        tag = self._resolve_via_skopeo(image_ref)
        if tag:
            self.logger.log_info(f"📦 Resolved {image_ref} to {tag}")
            return tag

        tag = self._resolve_via_podman(image_ref)
        if tag:
            self.logger.log_info(f"📦 Resolved {image_ref} to {tag} (via podman)")
            return tag

        raise RuntimeError(
            f'Unable to resolve latest tag for image "{image_ref}". '
            "Ensure skopeo or podman is installed and the image exists."
        )

    def _resolve_via_skopeo(self, image_ref: str) -> str | None:
        """Resolve latest tag using skopeo list-tags."""
        cmd = f"skopeo list-tags docker://{image_ref}"
        try:
            result = subprocess.run(
                cmd.split(), capture_output=True, text=True, check=True
            )
            tags_data = json.loads(result.stdout)
            tags = tags_data.get("Tags", [])
            if tags:
                return tags[-1]
        except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError):
            pass
        return None

    def _resolve_via_podman(self, image_ref: str) -> str | None:
        """Resolve latest tag using podman search."""
        cmd = f"podman search --list-tags --limit 1000 {image_ref}"
        try:
            result = subprocess.run(
                cmd.split(), capture_output=True, text=True, check=False
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split("\n")
                if lines:
                    last_line = lines[-1]
                    parts = last_line.split()
                    if len(parts) >= 2:
                        return parts[1]
        except FileNotFoundError:
            pass
        return None

    def resolve_chart_version(
        self, chart_name: str, repo_url: str | None = None
    ) -> str:
        """Resolve chart version via ``helm search repo``, with repo URL fallback."""
        self.logger.log_info(f"🔍 Resolving chart version for: {chart_name}")

        version = self._search_helm_repo(chart_name)
        if version:
            self.logger.log_info(f"📦 Resolved chart {chart_name} to {version}")
            return version

        if repo_url:
            version = self._resolve_chart_via_url(chart_name, repo_url)
            if version:
                self.logger.log_info(
                    f"📦 Resolved chart {chart_name} to {version} (via repo URL)"
                )
                return version

        raise RuntimeError(
            f'Unable to resolve chart version for "{chart_name}". '
            "Ensure helm is installed and the repository is added, "
            "or provide a valid repo URL."
        )

    def _search_helm_repo(self, chart_name: str) -> str | None:
        """Search for a chart version using helm search repo."""
        cmd = f"helm search repo {chart_name}"
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                shell=True,
                executable="/bin/bash",
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                lines = result.stdout.strip().split("\n")
                if len(lines) > 1:
                    parts = lines[-1].split()
                    if len(parts) > 1:
                        return parts[1]
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
        return None

    def _resolve_chart_via_url(self, chart_name: str, repo_url: str) -> str | None:
        """Temporarily add a helm repo, search for the chart version, then clean up."""
        tmp_repo_name = f"_llmdbench_tmp_{chart_name.replace('/', '_')}"
        try:
            add_cmd = f"helm repo add {tmp_repo_name} {repo_url} --force-update"
            add_result = subprocess.run(
                add_cmd,
                capture_output=True,
                text=True,
                shell=True,
                executable="/bin/bash",
                check=False,
            )
            if add_result.returncode != 0:
                return None

            subprocess.run(
                f"helm repo update {tmp_repo_name}",
                capture_output=True,
                text=True,
                shell=True,
                executable="/bin/bash",
                check=False,
            )

            return self._search_helm_repo(tmp_repo_name)
        finally:
            subprocess.run(
                f"helm repo remove {tmp_repo_name}",
                capture_output=True,
                text=True,
                shell=True,
                executable="/bin/bash",
                check=False,
            )

    def resolve_all(self, values: dict) -> dict:
        """Resolve all ``"auto"`` image tags and chart versions in the values dict."""
        result = deepcopy(values)

        unresolved = []
        self._resolve_image_tags(result, unresolved)
        self._resolve_standalone_image(result, unresolved)
        self._resolve_wva_image(result, unresolved)
        self._resolve_chart_versions(result, unresolved)
        self._resolve_gateway_version(result)

        if unresolved:
            self.logger.log_warning(
                f"⚠️  {len(unresolved)} version(s) could not be resolved: "
                f"{', '.join(unresolved)}. "
                "These will remain as 'auto' and must be resolved before deployment."
            )

        return result

    def _resolve_image_tags(self, values: dict, unresolved: list) -> None:
        """Resolve all 'auto' tags in the images section."""
        images = values.get("images", {})
        for image_key, image_config in images.items():
            if isinstance(image_config, dict) and image_config.get("tag") == "auto":
                repo = image_config.get("repository", "")
                if repo:
                    try:
                        image_config["tag"] = self.resolve_image_tag("", repo)
                    except RuntimeError as exc:
                        self.logger.log_warning(
                            f"⚠️  Could not resolve image tag for " f"{image_key}: {exc}"
                        )
                        unresolved.append(f"images.{image_key}.tag")

    def _resolve_standalone_image(self, values: dict, unresolved: list) -> None:
        """Resolve 'auto' tag for the standalone image."""
        standalone_image = values.get("standalone", {}).get("image", {})
        if isinstance(standalone_image, dict) and standalone_image.get("tag") == "auto":
            repo = standalone_image.get("repository", "")
            if repo:
                try:
                    standalone_image["tag"] = self.resolve_image_tag("", repo)
                except RuntimeError as exc:
                    self.logger.log_warning(
                        f"⚠️  Could not resolve standalone image tag: {exc}"
                    )
                    unresolved.append("standalone.image.tag")

    def _resolve_wva_image(self, values: dict, unresolved: list) -> None:
        """Resolve 'auto' tag for the WVA image."""
        wva_image = values.get("wva", {}).get("image", {})
        if isinstance(wva_image, dict) and wva_image.get("tag") == "auto":
            repo = wva_image.get("repository", "")
            if repo:
                try:
                    wva_image["tag"] = self.resolve_image_tag("", repo)
                except RuntimeError as exc:
                    self.logger.log_warning(
                        f"⚠️  Could not resolve WVA image tag: {exc}"
                    )
                    unresolved.append("wva.image.tag")

    def _resolve_chart_versions(self, values: dict, unresolved: list) -> None:
        """Resolve all 'auto' chart versions."""
        chart_versions = values.get("chartVersions", {})
        helm_repos = values.get("helmRepositories", {})
        for chart_key, version in list(chart_versions.items()):
            if version == "auto":
                repo_info = helm_repos.get(chart_key, {})
                repo_name = repo_info.get("name", chart_key)
                repo_url = repo_info.get("url")
                try:
                    chart_versions[chart_key] = self.resolve_chart_version(
                        repo_name, repo_url=repo_url
                    )
                except RuntimeError as exc:
                    self.logger.log_warning(
                        f"⚠️  Could not resolve chart version for " f"{chart_key}: {exc}"
                    )
                    unresolved.append(f"chartVersions.{chart_key}")

    def _resolve_gateway_version(self, values: dict) -> None:
        """Resolve gateway version from the istio chart version."""
        gateway = values.get("gateway", {})
        if gateway.get("version") == "auto":
            istio_version = values.get("chartVersions", {}).get("istiod")
            if istio_version and istio_version != "auto":
                gateway["version"] = istio_version
                self.logger.log_info(
                    f"📦 Resolved gateway version from istio: {istio_version}"
                )

    def has_unresolved(self, values: dict) -> list[str]:
        """Return field paths that still contain ``"auto"``."""
        unresolved = []
        for key, img in values.get("images", {}).items():
            if isinstance(img, dict) and img.get("tag") == "auto":
                unresolved.append(f"images.{key}.tag")
        for key, ver in values.get("chartVersions", {}).items():
            if ver == "auto":
                unresolved.append(f"chartVersions.{key}")
        standalone = values.get("standalone", {}).get("image", {})
        if isinstance(standalone, dict) and standalone.get("tag") == "auto":
            unresolved.append("standalone.image.tag")
        if values.get("gateway", {}).get("version") == "auto":
            unresolved.append("gateway.version")
        wva = values.get("wva", {}).get("image", {})
        if isinstance(wva, dict) and wva.get("tag") == "auto":
            unresolved.append("wva.image.tag")
        return unresolved
