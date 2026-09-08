"""Tests for CLI scenario overrides (``--set``).

Covers the parser (`llmdbenchmark/parser/cli_overrides.py`), the per-stack
selector resolution and fail-fast validation in ``RenderPlans``, the
precedence chain assembled in ``cli._build_setup_overrides_by_stack``, and
end-to-end rendering against the real templates.

Rendering runs against test-owned fixtures in ``tests/fixtures/`` rather
than shipped scenarios -- see the note on ``SINGLE_STACK`` / ``MULTI_STACK``
below.
"""

from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from llmdbenchmark.parser.cli_overrides import (
    GLOBAL_SELECTOR,
    MISSING,
    REDACTED,
    OverrideParseError,
    coerce_value,
    dotted_leaves,
    find_broken_parent_paths,
    find_typo_leaves,
    is_glob,
    is_secret_path,
    leaf_entries,
    parse_cli_overrides,
    resolve_dotted,
    resolve_segments,
    selectors_for_stack,
    split_override_pairs,
    validate_selectors,
)
from llmdbenchmark.parser.cluster_resource_resolver import ClusterResourceResolver
from llmdbenchmark.parser.render_plans import RenderPlans
from llmdbenchmark.parser.version_resolver import VersionResolver


PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = PROJECT_ROOT / "config" / "templates" / "jinja"
DEFAULTS = PROJECT_ROOT / "config" / "templates" / "values" / "defaults.yaml"
# Test-owned fixtures, deliberately NOT scenarios under config/scenarios/.
# Those ship to users and get retuned freely -- replicas, resources, model
# choice, pool names -- and the tests below assert on literal stack names
# and values. Pointing them at a shipped scenario makes an ordinary config
# edit fail the suite for a reason that has nothing to do with the code
# under test. Each fixture's header states the contract its tests rely on.
#
# Whether the *shipped* scenarios still render is a separate concern,
# covered by the CI plan-rendering job over config/specification/**.
SINGLE_STACK = PROJECT_ROOT / "tests" / "fixtures" / "single_stack_overrides.yaml"
MULTI_STACK = PROJECT_ROOT / "tests" / "fixtures" / "multi_stack_overrides.yaml"


# ---------------------------------------------------------------------------
# Pair splitting
# ---------------------------------------------------------------------------


class TestQuotedDottedKeys:
    """Keys whose *name* contains dots, e.g. Kubernetes annotations.

    A quoted segment protects its dots from path splitting. The quoting must
    apply to the KEY half only -- quoting is also load-bearing on the value
    side (comma protection, and the documented escape hatches for YAML's
    octal reading and for multi-line folding), so a transform applied to the
    whole expression would silently break those.
    """

    def test_annotation_key_with_dots(self):
        parsed, _ = parse_cli_overrides(
            ['annotations.prefill.pod."k8s.v1.cni.cncf.io/networks"=multi-nic']
        )
        assert parsed == {
            GLOBAL_SELECTOR: {
                "annotations": {
                    "prefill": {"pod": {"k8s.v1.cni.cncf.io/networks": "multi-nic"}}
                }
            }
        }

    def test_dotted_key_in_a_parent_position(self):
        parsed, _ = parse_cli_overrides(['annotations."k8s.io/zone".pod=east'])
        assert parsed == {
            GLOBAL_SELECTOR: {"annotations": {"k8s.io/zone": {"pod": "east"}}}
        }

    def test_unquoted_dots_still_split_into_a_path(self):
        parsed, _ = parse_cli_overrides(["decode.resources.limits.cpu=8"])
        assert parsed == {
            GLOBAL_SELECTOR: {"decode": {"resources": {"limits": {"cpu": 8}}}}
        }

    # --- value-side quoting must survive ---------------------------------

    def test_quoted_value_with_a_comma_is_still_one_pair(self):
        parsed, _ = parse_cli_overrides(
            ['annotations.pod.note="a,b",decode.replicas=3']
        )
        assert parsed[GLOBAL_SELECTOR]["annotations"]["pod"]["note"] == "a,b"
        assert parsed[GLOBAL_SELECTOR]["decode"]["replicas"] == 3

    def test_quoted_value_keeps_the_octal_escape_hatch(self):
        parsed, _ = parse_cli_overrides(['model.blockSize="012"'])
        assert parsed[GLOBAL_SELECTOR]["model"]["blockSize"] == "012"

    def test_quoted_value_keeps_the_multiline_escape_hatch(self):
        parsed, _ = parse_cli_overrides(
            [r'decode.vllm.customCommand="export FOO=1\nvllm serve /x"']
        )
        assert parsed[GLOBAL_SELECTOR]["decode"]["vllm"]["customCommand"] == (
            "export FOO=1\nvllm serve /x"
        )

    def test_dotted_key_and_quoted_value_together(self):
        parsed, _ = parse_cli_overrides(['annotations.pod."k8s.io/limit"="1,2"'])
        assert parsed[GLOBAL_SELECTOR]["annotations"]["pod"]["k8s.io/limit"] == "1,2"

    def test_dotted_secret_key_is_still_recognised(self):
        # Redaction keys off the last path segment; a quoted segment must
        # not smuggle a credential past it.
        _, warnings = parse_cli_overrides(
            ['foo."my.token"=hf_AAA', 'foo."my.token"=hf_BBB']
        )
        joined = " ".join(warnings)
        assert "hf_AAA" not in joined and "hf_BBB" not in joined

    def test_log_reports_the_true_previous_value_of_a_dotted_key(self):
        # A joined path cannot be split back into segments when a key
        # contains a dot, so the log used to report <unset> for an override
        # that was in fact replacing a real value -- which reads as "the
        # override did not work" even though it did.
        base = {"annotations": {"pod": {"k8s.io/networks": "old-net"}}}
        overrides = {"annotations": {"pod": {"k8s.io/networks": "new-net"}}}
        [(segments, new_value)] = leaf_entries(overrides)
        assert segments == ("annotations", "pod", "k8s.io/networks")
        assert resolve_segments(base, segments) == "old-net"
        # The lossy, display-only form cannot find it:
        assert resolve_dotted(base, ".".join(segments)) is MISSING

    def test_sentinel_is_not_observable_in_output(self):
        parsed, _ = parse_cli_overrides(['annotations.pod."a.b"=x'])
        assert "_PROTECTDOT_" not in str(parsed)


class TestSplitOverridePairs:
    def test_single_pair(self):
        assert split_override_pairs("decode.replicas=2") == ["decode.replicas=2"]

    def test_comma_separated(self):
        assert split_override_pairs("a=1,b=2") == ["a=1", "b=2"]

    def test_whitespace_is_trimmed(self):
        assert split_override_pairs(" a=1 , b=2 ") == ["a=1", "b=2"]

    def test_empty_segments_dropped(self):
        assert split_override_pairs("a=1,,b=2,") == ["a=1", "b=2"]

    def test_commas_inside_brackets_are_kept(self):
        # A naive split(",") would shred the list value here.
        assert split_override_pairs("a=[1,2],b=3") == ["a=[1,2]", "b=3"]

    def test_commas_inside_braces_are_kept(self):
        assert split_override_pairs("a={x: 1, y: 2},b=3") == ["a={x: 1, y: 2}", "b=3"]

    def test_commas_inside_quotes_are_kept(self):
        assert split_override_pairs("a='x,y',b=3") == ["a='x,y'", "b=3"]

    def test_nested_brackets(self):
        assert split_override_pairs("a=[[1,2],[3,4]],b=5") == ["a=[[1,2],[3,4]]", "b=5"]


# ---------------------------------------------------------------------------
# Value coercion
# ---------------------------------------------------------------------------


class TestCoerceValue:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("4", 4),
            ("0.95", 0.95),
            ("true", True),
            ("false", False),
            ("gpu/sglang", "gpu/sglang"),
            ("1Ti", "1Ti"),
            ("300s", "300s"),
            ("[a, b]", ["a", "b"]),
            ("{x: 1}", {"x": 1}),
            ("'8'", "8"),  # quoted -> stays a string, as in the scenario YAML
        ],
    )
    def test_coercion(self, raw, expected):
        assert coerce_value(raw) == expected

    def test_empty_value_is_empty_string_not_none(self):
        # None is skipped by deep_merge, so `foo=` must not silently no-op.
        assert coerce_value("") == ""
        assert coerce_value("   ") == ""

    def test_image_ref_with_colon_and_tag(self):
        assert coerce_value("quay.io/llm-d/epp:v1.2") == "quay.io/llm-d/epp:v1.2"

    def test_real_newlines_are_folded_to_spaces(self):
        # Documented in standup.md: a plain scalar folds line breaks, which
        # silently changes the meaning of a multi-line shell command.
        assert coerce_value("export FOO=1\nvllm serve /model-cache/x") == (
            "export FOO=1 vllm serve /model-cache/x"
        )

    def test_double_quoted_escape_preserves_newlines(self):
        # ...and this is the documented escape hatch for keeping them.
        value = coerce_value(r'"export FOO=1\nvllm serve /model-cache/x"')
        assert value == "export FOO=1\nvllm serve /model-cache/x"
        assert value.count("\n") == 1


# ---------------------------------------------------------------------------
# Pair / expression parsing
# ---------------------------------------------------------------------------


class TestParseCliOverrides:
    def test_none_and_empty(self):
        assert parse_cli_overrides(None) == ({}, [])
        assert parse_cli_overrides([]) == ({}, [])

    def test_global_pair(self):
        parsed, warnings = parse_cli_overrides(["decode.replicas=2"])
        assert parsed == {GLOBAL_SELECTOR: {"decode": {"replicas": 2}}}
        assert warnings == []

    def test_bare_string_accepted(self):
        parsed, _ = parse_cli_overrides("decode.replicas=2")
        assert parsed == {GLOBAL_SELECTOR: {"decode": {"replicas": 2}}}

    def test_multiple_attributes_one_stack(self):
        parsed, _ = parse_cli_overrides(
            [
                "llama-31-8b:decode.replicas=4,"
                "llama-31-8b:decode.resources.limits.memory=64Gi"
            ]
        )
        assert parsed == {
            "llama-31-8b": {
                "decode": {
                    "replicas": 4,
                    "resources": {"limits": {"memory": "64Gi"}},
                }
            }
        }

    def test_multiple_stacks_one_invocation(self):
        parsed, _ = parse_cli_overrides(
            ["qwen3-06b:decode.replicas=4,llama-31-8b:decode.replicas=1"]
        )
        assert parsed == {
            "qwen3-06b": {"decode": {"replicas": 4}},
            "llama-31-8b": {"decode": {"replicas": 1}},
        }

    def test_repeated_flag_accumulates(self):
        parsed, _ = parse_cli_overrides(
            ["decode.replicas=4", "llama-31-8b:wva.hpa.maxReplicas=2"]
        )
        assert parsed == {
            GLOBAL_SELECTOR: {"decode": {"replicas": 4}},
            "llama-31-8b": {"wva": {"hpa": {"maxReplicas": 2}}},
        }

    def test_glob_selector_preserved(self):
        parsed, _ = parse_cli_overrides(["*-8b:decode.replicas=4"])
        assert parsed == {"*-8b": {"decode": {"replicas": 4}}}

    def test_colon_in_value_is_not_a_selector(self):
        parsed, _ = parse_cli_overrides(["router.epp.image=quay.io/llm-d/epp:v1.2"])
        assert parsed == {
            GLOBAL_SELECTOR: {"router": {"epp": {"image": "quay.io/llm-d/epp:v1.2"}}}
        }

    def test_selector_plus_colon_in_value(self):
        parsed, _ = parse_cli_overrides(["llama:router.epp.image=quay.io/x:v1.2"])
        assert parsed == {"llama": {"router": {"epp": {"image": "quay.io/x:v1.2"}}}}

    def test_equals_in_value_is_preserved(self):
        parsed, _ = parse_cli_overrides(["decode.vllm.customCommand=--flag=value"])
        assert parsed[GLOBAL_SELECTOR]["decode"]["vllm"]["customCommand"] == (
            "--flag=value"
        )

    def test_duplicate_key_warns_and_last_wins(self):
        parsed, warnings = parse_cli_overrides(
            ["decode.replicas=4", "decode.replicas=8"]
        )
        assert parsed == {GLOBAL_SELECTOR: {"decode": {"replicas": 8}}}
        assert len(warnings) == 1
        assert "set more than once" in warnings[0]

    def test_same_key_different_selectors_is_not_a_duplicate(self):
        parsed, warnings = parse_cli_overrides(
            ["a:decode.replicas=4", "b:decode.replicas=8"]
        )
        assert warnings == []
        assert parsed["a"] != parsed["b"]

    def test_null_value_warns(self):
        _, warnings = parse_cli_overrides(["decode.replicas=null"])
        assert any("null" in w for w in warnings)

    @pytest.mark.parametrize(
        "expr,value",
        [
            ("a=012", 10),  # YAML 1.1 octal
            ("a=0x10", 16),  # hex
            ("a=1:30", 90),  # sexagesimal
            ("a=.inf", float("inf")),
        ],
    )
    def test_surprising_numeric_coercion_warns(self, expr, value):
        parsed, warnings = parse_cli_overrides([expr])
        assert parsed[GLOBAL_SELECTOR]["a"] == value
        assert any("quote it" in w for w in warnings), warnings

    def test_yaml_date_coercion_warns(self):
        parsed, warnings = parse_cli_overrides(["a=2024-01-01"])
        assert any("YAML date" in w for w in warnings), warnings

    @pytest.mark.parametrize(
        "expr",
        [
            "a=4",
            "a=0.95",
            "a=1.50",
            "a=+4",
            "a=1_000",
            "a=true",
            "a=on",  # documented YAML bool, not "surprising"
            "a=1Ti",
            "a=gpu/sglang",
            "a='012'",  # quoted -> stays a string, no warning
            "a=[1, 2]",
        ],
    )
    def test_ordinary_values_do_not_warn(self, expr):
        _, warnings = parse_cli_overrides([expr])
        assert warnings == []

    @pytest.mark.parametrize(
        "bad",
        [
            "decode.replicas",  # no '='
            ":decode.replicas=4",  # empty selector
            "llama:=4",  # empty key
            "=4",  # empty key, no selector
        ],
    )
    def test_malformed_raises(self, bad):
        with pytest.raises(OverrideParseError):
            parse_cli_overrides([bad])

    def test_conflicting_dotted_keys_raise(self):
        with pytest.raises(OverrideParseError) as exc:
            parse_cli_overrides(["a.b=1,a.b.c=2"])
        assert "conflicting overrides" in str(exc.value)


# ---------------------------------------------------------------------------
# Selector resolution / specificity
# ---------------------------------------------------------------------------


class TestSelectorsForStack:
    def test_empty(self):
        assert selectors_for_stack({}, "anything") == []

    def test_is_glob(self):
        assert is_glob("*-8b")
        assert is_glob("qwen?")
        assert is_glob("[ab]-stack")
        assert not is_glob("llama-31-8b")

    def test_specificity_order_global_glob_exact(self):
        by_selector = {GLOBAL_SELECTOR: {}, "*-8b": {}, "llama-31-8b": {}}
        assert selectors_for_stack(by_selector, "llama-31-8b") == [
            GLOBAL_SELECTOR,
            "*-8b",
            "llama-31-8b",
        ]

    def test_specificity_beats_insertion_order(self):
        # Exact typed FIRST must still resolve last (most specific wins).
        by_selector = {"llama-31-8b": {}, "*-8b": {}, GLOBAL_SELECTOR: {}}
        assert selectors_for_stack(by_selector, "llama-31-8b") == [
            GLOBAL_SELECTOR,
            "*-8b",
            "llama-31-8b",
        ]

    def test_non_matching_selectors_excluded(self):
        by_selector = {GLOBAL_SELECTOR: {}, "*-8b": {}, "llama-31-8b": {}}
        assert selectors_for_stack(by_selector, "qwen3-06b") == [GLOBAL_SELECTOR]

    def test_cli_order_breaks_ties_within_a_tier(self):
        by_selector = {"*-8b": {}, "llama-*": {}}
        assert selectors_for_stack(by_selector, "llama-31-8b") == ["*-8b", "llama-*"]


class TestValidateSelectors:
    def test_global_never_errors(self):
        assert validate_selectors({GLOBAL_SELECTOR: {}}, ["a"]) == []

    def test_known_exact_passes(self):
        assert validate_selectors({"a": {}}, ["a", "b"]) == []

    def test_unknown_exact_errors_with_known_list(self):
        errors = validate_selectors({"nope": {}}, ["a", "b"])
        assert len(errors) == 1
        assert "unknown stack" in errors[0]
        assert "a, b" in errors[0]

    def test_matching_glob_passes(self):
        assert validate_selectors({"*-8b": {}}, ["llama-31-8b"]) == []

    def test_zero_match_glob_errors(self):
        errors = validate_selectors({"*-70b": {}}, ["llama-31-8b"])
        assert len(errors) == 1
        assert "matched no stack" in errors[0]

    def test_multiple_errors_reported_together(self):
        errors = validate_selectors({"x": {}, "*-70b": {}}, ["a"])
        assert len(errors) == 2


# ---------------------------------------------------------------------------
# Flattening / path helpers
# ---------------------------------------------------------------------------


class TestPathHelpers:
    def test_dotted_leaves(self):
        assert dotted_leaves(
            {"decode": {"replicas": 4, "resources": {"limits": {"cpu": "8"}}}}
        ) == [("decode.replicas", 4), ("decode.resources.limits.cpu", "8")]

    def test_empty_mapping_is_a_leaf(self):
        assert dotted_leaves({"kustomize": {"extraHelmSets": {}}}) == [
            ("kustomize.extraHelmSets", {})
        ]

    def test_resolve_dotted_found_and_missing(self):
        base = {"decode": {"replicas": 1}}
        assert resolve_dotted(base, "decode.replicas") == 1
        assert resolve_dotted(base, "decode.missing") is MISSING
        assert resolve_dotted(base, "nope.deeper") is MISSING

    def test_resolve_dotted_through_scalar_is_missing(self):
        assert resolve_dotted({"a": 1}, "a.b") is MISSING

    def test_broken_parent_reported(self):
        unknown, clobbered = find_broken_parent_paths(
            {"decod": {"replicas": 4}}, {"decode": {"replicas": 1}}
        )
        assert unknown == ["decod"]
        assert clobbered == []

    def test_broken_nested_parent_reported(self):
        unknown, clobbered = find_broken_parent_paths(
            {"decode": {"resurces": {"limits": {"cpu": "8"}}}},
            {"decode": {"replicas": 1, "resources": {}}},
        )
        assert unknown == ["decode.resurces"]
        assert clobbered == []

    def test_new_leaf_on_existing_map_is_not_reported(self):
        # Free-form blocks legitimately gain new keys.
        unknown, clobbered = find_broken_parent_paths(
            {"kustomize": {"guideVariableOverrides": {"INFRA_PROVIDER": "base"}}},
            {"kustomize": {"guideVariableOverrides": {}}},
        )
        assert unknown == [] and clobbered == []

    def test_descending_into_a_list_is_clobber_not_unknown(self):
        # `vllmCommon.volumeMounts.0.mountPath=x` would replace the list.
        unknown, clobbered = find_broken_parent_paths(
            {"vllmCommon": {"volumeMounts": {"0": {"mountPath": "/x"}}}},
            {"vllmCommon": {"volumeMounts": [{"name": "dshm"}]}},
        )
        assert unknown == []
        assert clobbered == [("vllmCommon.volumeMounts", "list")]

    def test_descending_into_a_scalar_is_clobber(self):
        unknown, clobbered = find_broken_parent_paths({"a": {"b": 1}}, {"a": "scalar"})
        assert unknown == []
        assert clobbered == [("a", "str")]

    def test_absent_parent_is_unknown_not_clobber(self):
        # `None`-valued key in base still counts as absent, not a clobber.
        unknown, clobbered = find_broken_parent_paths({"a": {"b": 1}}, {"a": None})
        assert unknown == ["a"]
        assert clobbered == []

    def test_top_level_leaf_never_reported(self):
        assert find_broken_parent_paths({"newKey": 1}, {}) == ([], [])


class TestFindTypoLeaves:
    """A misspelled leaf merges into a dead key; only near-misses are flagged."""

    def test_near_miss_leaf_suggests_the_real_sibling(self):
        # The motivating case: the stack silently deploys to localhost because
        # nok8s.connection kept its default.
        assert find_typo_leaves(
            {"nok8s": {"conection": "10.0.0.7"}},
            {"nok8s": {"connection": "localhost", "runtime": "docker"}},
        ) == [("nok8s.conection", "connection")]

    def test_correct_leaf_is_silent(self):
        assert (
            find_typo_leaves(
                {"nok8s": {"connection": "10.0.0.7"}},
                {"nok8s": {"connection": "localhost"}},
            )
            == []
        )

    def test_case_only_difference_is_flagged(self):
        assert find_typo_leaves(
            {"nok8s": {"sshidentity": "/k"}},
            {"nok8s": {"sshIdentity": "", "sshArgs": []}},
        ) == [("nok8s.sshidentity", "sshIdentity")]

    def test_genuinely_new_leaf_on_a_free_form_block_is_silent(self):
        # Same shape the parent-path check deliberately allows: free-form
        # blocks gain new keys by design, so absence alone must not warn.
        assert (
            find_typo_leaves(
                {"kustomize": {"guideVariableOverrides": {"INFRA_PROVIDER": "base"}}},
                {"kustomize": {"guideVariableOverrides": {"FOO_BAR": "x"}}},
            )
            == []
        )

    def test_new_leaf_on_an_empty_map_is_silent(self):
        assert (
            find_typo_leaves(
                {"kustomize": {"guideVariableOverrides": {"INFRA_PROVIDER": "base"}}},
                {"kustomize": {"guideVariableOverrides": {}}},
            )
            == []
        )

    def test_distinct_real_key_is_not_a_typo_of_its_neighbours(self):
        # extraArgs vs deviceArgs/sshArgs share a suffix but are different keys.
        assert (
            find_typo_leaves(
                {"vllm": {"extraArgs": ["--x"]}},
                {"vllm": {"deviceArgs": [], "gpus": "all"}},
            )
            == []
        )

    def test_top_level_leaf_is_not_reported(self):
        # Mirrors find_broken_parent_paths: a bare new top-level key is
        # indistinguishable from a legitimate one.
        assert find_typo_leaves({"newKey": 1}, {}) == []

    def test_missing_parent_is_left_to_the_parent_check(self):
        # nok8ss.* already warns via find_broken_parent_paths; don't double-report.
        assert find_typo_leaves({"nok8ss": {"connection": "x"}}, {"nok8s": {}}) == []

    def test_non_map_parent_is_not_traversed(self):
        # Descending into a scalar is a clobber, reported elsewhere.
        assert find_typo_leaves({"a": {"b": 1}}, {"a": "scalar"}) == []

    def test_nested_leaf_path_is_fully_qualified(self):
        assert find_typo_leaves(
            {"nok8s": {"vllm": {"replias": 2}}},
            {"nok8s": {"vllm": {"replicas": 1, "image": "x"}}},
        ) == [("nok8s.vllm.replias", "replicas")]

    def test_only_the_closest_sibling_is_suggested(self):
        typos = find_typo_leaves(
            {"nok8s": {"conection": "x"}},
            {"nok8s": {"connection": "a", "connections": "b"}},
        )
        assert len(typos) == 1
        assert typos[0][0] == "nok8s.conection"
        assert typos[0][1] in ("connection", "connections")


# ---------------------------------------------------------------------------
# cli._build_setup_overrides_by_stack -- precedence assembly
# ---------------------------------------------------------------------------


def _build(set_overrides=None, cluster_config_overrides=None, logger=None):
    from llmdbenchmark.cli import _build_setup_overrides_by_stack

    args = Namespace(
        set_overrides=set_overrides,
        cluster_config_overrides=cluster_config_overrides,
    )
    return _build_setup_overrides_by_stack(args, logger or MagicMock())


class TestBuildSetupOverridesByStack:
    def test_no_input_is_empty(self, monkeypatch):
        monkeypatch.delenv("LLMDBENCH_SET", raising=False)
        assert _build() == {}

    def test_cluster_config_only_keeps_legacy_shape(self, monkeypatch):
        monkeypatch.delenv("LLMDBENCH_SET", raising=False)
        cluster = {"storage": {"modelPvc": {"storageClassName": "ocs-rwx"}}}
        assert _build(cluster_config_overrides=cluster) == {GLOBAL_SELECTOR: cluster}

    def test_set_beats_cluster_config(self, monkeypatch):
        monkeypatch.delenv("LLMDBENCH_SET", raising=False)
        cluster = {"storage": {"modelPvc": {"size": "1Ti", "storageClassName": "ocs"}}}
        built = _build(
            set_overrides=["storage.modelPvc.size=2Ti"],
            cluster_config_overrides=cluster,
        )
        # CLI wins on the contested key; the file's other keys survive.
        assert built[GLOBAL_SELECTOR]["storage"]["modelPvc"] == {
            "size": "2Ti",
            "storageClassName": "ocs",
        }

    def test_scoped_set_does_not_absorb_cluster_config(self, monkeypatch):
        monkeypatch.delenv("LLMDBENCH_SET", raising=False)
        built = _build(
            set_overrides=["llama:decode.replicas=4"],
            cluster_config_overrides={"storage": {"modelPvc": {"size": "1Ti"}}},
        )
        assert built["llama"] == {"decode": {"replicas": 4}}
        assert built[GLOBAL_SELECTOR] == {"storage": {"modelPvc": {"size": "1Ti"}}}

    def test_env_var_used_when_flag_absent(self, monkeypatch):
        monkeypatch.setenv("LLMDBENCH_SET", "decode.replicas=9")
        assert _build() == {GLOBAL_SELECTOR: {"decode": {"replicas": 9}}}

    def test_flag_wins_over_env_var(self, monkeypatch):
        monkeypatch.setenv("LLMDBENCH_SET", "decode.replicas=9")
        built = _build(set_overrides=["decode.replicas=3"])
        assert built == {GLOBAL_SELECTOR: {"decode": {"replicas": 3}}}

    def test_malformed_override_exits(self, monkeypatch):
        monkeypatch.delenv("LLMDBENCH_SET", raising=False)
        logger = MagicMock()
        with pytest.raises(SystemExit):
            _build(set_overrides=["decode.replicas"], logger=logger)
        assert logger.log_error.called

    def test_applied_overrides_are_logged_with_values(self, monkeypatch):
        monkeypatch.delenv("LLMDBENCH_SET", raising=False)
        logger = MagicMock()
        _build(set_overrides=["llama:decode.replicas=4"], logger=logger)
        logged = " ".join(str(c) for c in logger.log_info.call_args_list)
        assert "decode.replicas=4" in logged
        assert "stack llama" in logged


# ---------------------------------------------------------------------------
# RenderPlans -- per-stack resolution
# ---------------------------------------------------------------------------


def _renderer(tmp_path, scenario, **kwargs):
    logger = kwargs.pop("logger", None) or MagicMock()
    return RenderPlans(
        template_dir=TEMPLATES,
        defaults_file=DEFAULTS,
        scenarios_file=scenario,
        output_dir=tmp_path,
        logger=logger,
        version_resolver=VersionResolver(logger=logger, dry_run=True),
        cluster_resource_resolver=ClusterResourceResolver(logger=logger, dry_run=True),
        **kwargs,
    )


def _configs(result) -> dict[str, dict]:
    return {
        path.name: yaml.safe_load((path / "config.yaml").read_text())
        for path in result.rendered_paths
    }


class TestEffectiveSetupOverrides:
    def test_no_overrides_is_empty(self, tmp_path):
        r = _renderer(tmp_path, SINGLE_STACK)
        assert r._effective_setup_overrides("any") == {}

    def test_global_applies_to_every_stack(self, tmp_path):
        r = _renderer(
            tmp_path,
            SINGLE_STACK,
            setup_overrides_by_stack={GLOBAL_SELECTOR: {"decode": {"replicas": 5}}},
        )
        assert r._effective_setup_overrides("a") == {"decode": {"replicas": 5}}
        assert r._effective_setup_overrides("b") == {"decode": {"replicas": 5}}

    def test_exact_beats_global(self, tmp_path):
        r = _renderer(
            tmp_path,
            SINGLE_STACK,
            setup_overrides_by_stack={
                GLOBAL_SELECTOR: {"decode": {"replicas": 5}},
                "llama": {"decode": {"replicas": 2}},
            },
        )
        assert r._effective_setup_overrides("llama") == {"decode": {"replicas": 2}}
        assert r._effective_setup_overrides("qwen") == {"decode": {"replicas": 5}}

    def test_exact_beats_glob(self, tmp_path):
        r = _renderer(
            tmp_path,
            SINGLE_STACK,
            setup_overrides_by_stack={
                "llama-31-8b": {"decode": {"replicas": 2}},
                "*-8b": {"decode": {"replicas": 5}},
            },
        )
        assert r._effective_setup_overrides("llama-31-8b") == {
            "decode": {"replicas": 2}
        }

    def test_buckets_merge_rather_than_replace(self, tmp_path):
        r = _renderer(
            tmp_path,
            SINGLE_STACK,
            setup_overrides_by_stack={
                GLOBAL_SELECTOR: {"decode": {"replicas": 5, "enabled": True}},
                "llama": {"decode": {"replicas": 2}},
            },
        )
        # The global bucket's sibling key survives the exact bucket.
        assert r._effective_setup_overrides("llama") == {
            "decode": {"replicas": 2, "enabled": True}
        }

    def test_unscoped_setup_overrides_beat_cli_set(self, tmp_path):
        # DoE setup.treatments ride in `setup_overrides` and must win --
        # the treatment is the deliberate sweep factor.
        r = _renderer(
            tmp_path,
            SINGLE_STACK,
            setup_overrides={"decode": {"replicas": 99}},
            setup_overrides_by_stack={
                GLOBAL_SELECTOR: {"decode": {"replicas": 5}},
                "llama": {"decode": {"replicas": 2}},
            },
        )
        assert r._effective_setup_overrides("llama") == {"decode": {"replicas": 99}}


# ---------------------------------------------------------------------------
# RenderPlans -- end-to-end against real templates
# ---------------------------------------------------------------------------


class TestClusterConfigStillWorks:
    """Regression guard for ``--cluster-config``.

    The file used to be passed to ``RenderPlans`` as ``setup_overrides``;
    it now rides in the global bucket of ``setup_overrides_by_stack`` so
    that ``--set`` can beat it. These pin the observable behaviour so the
    restructuring cannot silently regress.
    """

    CLUSTER_CONFIG = {
        "storage": {
            "workloadPvc": {
                "accessModes": ["ReadWriteMany"],
                "storageClassName": "ocs-storagecluster-cephfs",
            }
        },
        "harness": {"serviceAccount": "anyuid-sa", "runAsUser": 0},
        "dataAccess": {"serviceAccount": "anyuid-sa", "runAsUser": 0},
    }

    def test_cluster_config_alone_reaches_rendered_config(self, tmp_path):
        result = _renderer(
            tmp_path,
            SINGLE_STACK,
            setup_overrides_by_stack={GLOBAL_SELECTOR: self.CLUSTER_CONFIG},
        ).eval()
        assert result.global_errors == []
        config = _configs(result)["single-pool"]
        assert config["storage"]["workloadPvc"]["storageClassName"] == (
            "ocs-storagecluster-cephfs"
        )
        assert config["storage"]["workloadPvc"]["accessModes"] == ["ReadWriteMany"]
        assert config["harness"]["serviceAccount"] == "anyuid-sa"
        assert config["dataAccess"]["serviceAccount"] == "anyuid-sa"

    def test_set_beats_cluster_config_in_rendered_config(self, tmp_path):
        # cli._build_setup_overrides_by_stack folds the file under --set
        # inside the same bucket; emulate the shape it produces.
        from llmdbenchmark.cli import _deep_merge_dicts

        merged = _deep_merge_dicts(
            self.CLUSTER_CONFIG,
            {"storage": {"workloadPvc": {"storageClassName": "from-cli"}}},
        )
        result = _renderer(
            tmp_path, SINGLE_STACK, setup_overrides_by_stack={GLOBAL_SELECTOR: merged}
        ).eval()
        assert result.global_errors == []
        pvc = _configs(result)["single-pool"]["storage"]["workloadPvc"]
        assert pvc["storageClassName"] == "from-cli"
        # ...and the file's other values are untouched.
        assert pvc["accessModes"] == ["ReadWriteMany"]

    def test_treatment_beats_cluster_config_and_set(self, tmp_path):
        # The experiment path: cluster-config (+ --set) in the bucket,
        # DoE treatment in setup_overrides. Treatment wins the contested
        # key; every non-contested cluster-config value survives.
        result = _renderer(
            tmp_path,
            SINGLE_STACK,
            setup_overrides_by_stack={GLOBAL_SELECTOR: self.CLUSTER_CONFIG},
            setup_overrides={
                "storage": {"workloadPvc": {"storageClassName": "from-treatment"}},
                "decode": {"replicas": 5},
            },
        ).eval()
        assert result.global_errors == []
        config = _configs(result)["single-pool"]
        assert config["storage"]["workloadPvc"]["storageClassName"] == (
            "from-treatment"
        )
        assert config["storage"]["workloadPvc"]["accessModes"] == ["ReadWriteMany"]
        assert config["harness"]["serviceAccount"] == "anyuid-sa"
        assert config["decode"]["replicas"] == 5

    def test_cluster_config_scoped_to_a_stack_by_set(self, tmp_path):
        # cluster-config is global; a scoped --set still applies on top of
        # it for the stack it names, and not to the sibling.
        result = _renderer(
            tmp_path,
            MULTI_STACK,
            setup_overrides_by_stack={
                GLOBAL_SELECTOR: self.CLUSTER_CONFIG,
                "llama-31-8b": {"decode": {"replicas": 4}},
            },
        ).eval()
        assert result.global_errors == []
        configs = _configs(result)
        for name in ("llama-31-8b", "qwen3-06b"):
            assert configs[name]["harness"]["serviceAccount"] == "anyuid-sa"
        assert configs["llama-31-8b"]["decode"]["replicas"] == 4
        assert configs["qwen3-06b"]["decode"]["replicas"] == 1


class TestSingleStackRender:
    def test_override_lands_in_rendered_config(self, tmp_path):
        result = _renderer(
            tmp_path,
            SINGLE_STACK,
            setup_overrides_by_stack={
                GLOBAL_SELECTOR: {"decode": {"replicas": 7}},
            },
        ).eval()
        assert result.global_errors == []
        config = _configs(result)["single-pool"]
        assert config["decode"]["replicas"] == 7

    def test_kustomize_variant_reproduced_from_base_scenario(self, tmp_path):
        # The motivating case for `--set`: the repo used to ship a
        # `*-sglang` twin for each kustomize guide, differing only in the
        # accelerator backend. Those files are gone -- `-t kustomize` plus
        # one `--set` reproduces them. `enabled` flips from the CLI method,
        # `acceleratorBackend` comes from the override, and `guideName`
        # survives untouched from the scenario.
        result = _renderer(
            tmp_path,
            SINGLE_STACK,
            cli_methods="kustomize",
            setup_overrides_by_stack={
                GLOBAL_SELECTOR: {"kustomize": {"acceleratorBackend": "gpu/sglang"}},
            },
        ).eval()
        assert result.global_errors == []
        config = _configs(result)["single-pool"]
        assert config["kustomize"]["enabled"] is True
        assert config["kustomize"]["acceleratorBackend"] == "gpu/sglang"
        assert config["kustomize"]["guideName"] == "single-pool-guide"

    def test_dedicated_model_flag_beats_override(self, tmp_path):
        result = _renderer(
            tmp_path,
            SINGLE_STACK,
            cli_model="facebook/opt-125m",
            setup_overrides_by_stack={
                GLOBAL_SELECTOR: {"model": {"name": "ignored/by-cli-flag"}},
            },
        ).eval()
        assert result.global_errors == []
        assert _configs(result)["single-pool"]["model"]["name"] == ("facebook/opt-125m")

    def test_dedicated_flags_beat_doe_treatments_too(self, tmp_path):
        # Pins the documented precedence tail:
        #   ... < --set < DoE setup.treatments < dedicated flags
        # The _resolve_* helpers run after the whole merge, so -m and
        # --gateway-class are authoritative over every override layer.
        result = _renderer(
            tmp_path,
            SINGLE_STACK,
            cli_model="facebook/opt-125m",
            cli_gateway_class="istio",
            setup_overrides={
                "model": {"name": "from-treatment/model"},
                "gateway": {"className": "agentgateway"},
            },
            setup_overrides_by_stack={
                GLOBAL_SELECTOR: {"model": {"name": "from-set/model"}}
            },
        ).eval()
        assert result.global_errors == []
        config = _configs(result)["single-pool"]
        assert config["model"]["name"] == "facebook/opt-125m"
        assert config["gateway"]["className"] == "istio"

    def test_treatment_beats_set_when_no_dedicated_flag(self, tmp_path):
        # ...and with no dedicated flag in play, the treatment wins over --set.
        result = _renderer(
            tmp_path,
            SINGLE_STACK,
            setup_overrides={"decode": {"replicas": 9}},
            setup_overrides_by_stack={GLOBAL_SELECTOR: {"decode": {"replicas": 3}}},
        ).eval()
        assert result.global_errors == []
        assert _configs(result)["single-pool"]["decode"]["replicas"] == 9

    def test_legacy_setup_overrides_param_still_applies(self, tmp_path):
        # Back-compat: DoE treatments and older callers pass only this.
        result = _renderer(
            tmp_path, SINGLE_STACK, setup_overrides={"decode": {"replicas": 6}}
        ).eval()
        assert result.global_errors == []
        assert _configs(result)["single-pool"]["decode"]["replicas"] == 6

    def test_no_overrides_leaves_scenario_untouched(self, tmp_path):
        # Pins the fixture baseline the override tests measure against: if
        # this moves, "2 -> 4" in the override-log assertions is stale too.
        baseline = _configs(_renderer(tmp_path, SINGLE_STACK).eval())
        assert baseline["single-pool"]["decode"]["replicas"] == 2


class TestMultiStackRender:
    def test_exact_selector_scopes_to_one_stack(self, tmp_path):
        result = _renderer(
            tmp_path,
            MULTI_STACK,
            setup_overrides_by_stack={"llama-31-8b": {"decode": {"replicas": 4}}},
        ).eval()
        assert result.global_errors == []
        configs = _configs(result)
        assert configs["llama-31-8b"]["decode"]["replicas"] == 4
        assert configs["qwen3-06b"]["decode"]["replicas"] == 1  # scenario value

    def test_multiple_stacks_in_one_invocation(self, tmp_path):
        parsed, _ = parse_cli_overrides(
            ["qwen3-06b:decode.replicas=4,llama-31-8b:decode.replicas=2"]
        )
        result = _renderer(
            tmp_path, MULTI_STACK, setup_overrides_by_stack=parsed
        ).eval()
        assert result.global_errors == []
        configs = _configs(result)
        assert configs["qwen3-06b"]["decode"]["replicas"] == 4
        assert configs["llama-31-8b"]["decode"]["replicas"] == 2

    def test_global_floor_with_exact_exception(self, tmp_path):
        # The global selector sets a floor for every stack; the exact-name
        # selector overrides it for one, regardless of flag order.
        parsed, _ = parse_cli_overrides(
            [
                "decode.resources.limits.memory=64Gi",
                "llama-31-8b:decode.resources.limits.memory=32Gi",
            ]
        )
        result = _renderer(
            tmp_path, MULTI_STACK, setup_overrides_by_stack=parsed
        ).eval()
        assert result.global_errors == []
        configs = _configs(result)
        assert configs["qwen3-06b"]["decode"]["resources"]["limits"]["memory"] == "64Gi"
        assert (
            configs["llama-31-8b"]["decode"]["resources"]["limits"]["memory"] == "32Gi"
        )

    def test_glob_selector_matches_subset(self, tmp_path):
        parsed, _ = parse_cli_overrides(["*-8b:decode.replicas=3"])
        result = _renderer(
            tmp_path, MULTI_STACK, setup_overrides_by_stack=parsed
        ).eval()
        assert result.global_errors == []
        configs = _configs(result)
        assert configs["llama-31-8b"]["decode"]["replicas"] == 3
        assert configs["qwen3-06b"]["decode"]["replicas"] == 1

    def test_multiple_attributes_on_one_stack(self, tmp_path):
        parsed, _ = parse_cli_overrides(
            [
                "llama-31-8b:decode.replicas=4",
                "llama-31-8b:decode.resources.limits.memory=64Gi",
            ]
        )
        result = _renderer(
            tmp_path, MULTI_STACK, setup_overrides_by_stack=parsed
        ).eval()
        assert result.global_errors == []
        decode = _configs(result)["llama-31-8b"]["decode"]
        assert decode["replicas"] == 4
        assert decode["resources"]["limits"]["memory"] == "64Gi"
        # Sibling keys of the overridden path are preserved, not replaced.
        assert decode["resources"]["limits"]["cpu"] == "16"

    def test_unknown_selector_fails_fast_without_rendering(self, tmp_path):
        result = _renderer(
            tmp_path,
            MULTI_STACK,
            setup_overrides_by_stack={"llama-8b": {"decode": {"replicas": 4}}},
        ).eval()
        assert result.global_errors
        assert "unknown stack" in result.global_errors[0]
        assert result.rendered_paths == []

    def test_zero_match_glob_fails_fast(self, tmp_path):
        result = _renderer(
            tmp_path,
            MULTI_STACK,
            setup_overrides_by_stack={"*-70b": {"decode": {"replicas": 4}}},
        ).eval()
        assert result.global_errors
        assert "matched no stack" in result.global_errors[0]
        assert result.rendered_paths == []

    def test_global_selector_needs_no_matching_stack(self, tmp_path):
        result = _renderer(
            tmp_path,
            MULTI_STACK,
            setup_overrides_by_stack={GLOBAL_SELECTOR: {"decode": {"replicas": 4}}},
        ).eval()
        assert result.global_errors == []


class TestSecretRedaction:
    """Credentials must never reach a log.

    ``huggingface.token`` is a plain-text token in ``defaults.yaml``, and
    overrides are echoed back with their values. The rendered
    ``config.yaml`` still carries the real value on purpose -- it is what
    ``05_namespace_sa_rbac_secret.yaml.j2`` turns into the cluster Secret --
    but logs get streamed to CI and pasted into bug reports, so they get the
    redaction marker instead.
    """

    @pytest.mark.parametrize(
        "path,secret",
        [
            ("huggingface.token", True),
            ("huggingface.tokenBase64", True),
            ("huggingface.tokenKey", True),
            ("foo.password", True),
            ("foo.apiKey", True),
            ("some.credential", True),
            # Near-misses that must keep showing their values.
            ("decode.maxNumBatchedTokens", False),
            ("decode.acceleratorType.labelKey", False),
            ("images.pullSecret", False),
            ("decode.replicas", False),
            ("model.name", False),
        ],
    )
    def test_path_classification(self, path, secret):
        assert is_secret_path(path) is secret

    def test_token_is_redacted_in_the_render_log(self, tmp_path):
        logger = MagicMock()
        parsed, _ = parse_cli_overrides(
            ["huggingface.token=hf_SUPERSECRET123,decode.replicas=4"]
        )
        _renderer(
            tmp_path, SINGLE_STACK, logger=logger, setup_overrides_by_stack=parsed
        ).eval()
        emitted = " ".join(
            str(c.args[0])
            for c in logger.log_info.call_args_list + logger.log_warning.call_args_list
            if c.args
        )
        assert "hf_SUPERSECRET123" not in emitted
        assert f"huggingface.token: {REDACTED} -> {REDACTED}" in emitted
        # Non-secret overrides still report their values.
        assert "decode.replicas: 2 -> 4" in emitted

    def test_token_still_reaches_the_rendered_config(self, tmp_path):
        # Intentional: config.yaml is what produces the cluster Secret.
        parsed, _ = parse_cli_overrides(["huggingface.token=hf_SUPERSECRET123"])
        result = _renderer(
            tmp_path, SINGLE_STACK, setup_overrides_by_stack=parsed
        ).eval()
        assert result.global_errors == []
        config = _configs(result)["single-pool"]
        assert config["huggingface"]["token"] == "hf_SUPERSECRET123"

    def test_duplicate_secret_warning_does_not_echo_values(self):
        _, warnings = parse_cli_overrides(
            ["huggingface.token=hf_AAA", "huggingface.token=hf_BBB"]
        )
        joined = " ".join(warnings)
        assert "hf_AAA" not in joined and "hf_BBB" not in joined
        assert REDACTED in joined

    def test_coercion_warning_suppressed_for_secret_paths(self):
        # The coercion message quotes the raw value, so it is dropped
        # entirely rather than redacted.
        _, warnings = parse_cli_overrides(["huggingface.token=012"])
        assert not any("012" in w for w in warnings)


class TestOverrideLogging:
    def test_logs_old_to_new_per_stack(self, tmp_path):
        logger = MagicMock()
        _renderer(
            tmp_path,
            MULTI_STACK,
            logger=logger,
            setup_overrides_by_stack={"llama-31-8b": {"decode": {"replicas": 4}}},
        ).eval()
        lines = [str(c.args[0]) for c in logger.log_info.call_args_list if c.args]
        overrides = [line for line in lines if "Scenario override" in line]
        assert overrides == ["[llama-31-8b] Scenario override: decode.replicas: 1 -> 4"]

    def test_unset_previous_value_is_labelled(self, tmp_path):
        logger = MagicMock()
        _renderer(
            tmp_path,
            SINGLE_STACK,
            logger=logger,
            setup_overrides_by_stack={
                GLOBAL_SELECTOR: {"kustomize": {"guideVariableOverrides": {"X": "y"}}}
            },
        ).eval()
        lines = [str(c.args[0]) for c in logger.log_info.call_args_list if c.args]
        assert any(
            "kustomize.guideVariableOverrides.X: <unset> -> 'y'" in line
            for line in lines
        )

    def test_list_index_path_fails_the_render(self, tmp_path):
        # Dotted overrides cannot index into a list: the merge would replace
        # the whole list (silently dropping every element). Must be fatal.
        result = _renderer(
            tmp_path,
            SINGLE_STACK,
            setup_overrides_by_stack={
                GLOBAL_SELECTOR: {
                    "vllmCommon": {"volumeMounts": {"0": {"mountPath": "/x"}}}
                }
            },
        ).eval()
        assert result.global_errors
        assert "descends into a list" in result.global_errors[0]
        assert result.rendered_paths == []

    def test_assigning_a_whole_list_still_works(self, tmp_path):
        parsed, _ = parse_cli_overrides(
            ["vllmCommon.volumeMounts=[{name: only, mountPath: /only}]"]
        )
        result = _renderer(
            tmp_path, SINGLE_STACK, setup_overrides_by_stack=parsed
        ).eval()
        assert result.global_errors == []
        assert _configs(result)["single-pool"]["vllmCommon"]["volumeMounts"] == [
            {"name": "only", "mountPath": "/only"}
        ]

    def test_typo_in_parent_path_warns(self, tmp_path):
        logger = MagicMock()
        _renderer(
            tmp_path,
            SINGLE_STACK,
            logger=logger,
            setup_overrides_by_stack={
                GLOBAL_SELECTOR: {"decode": {"resurces": {"limits": {"cpu": "8"}}}}
            },
        ).eval()
        warnings = [str(c.args[0]) for c in logger.log_warning.call_args_list if c.args]
        assert any("decode.resurces" in w for w in warnings)

    def test_valid_path_does_not_warn(self, tmp_path):
        logger = MagicMock()
        _renderer(
            tmp_path,
            SINGLE_STACK,
            logger=logger,
            setup_overrides_by_stack={
                GLOBAL_SELECTOR: {"decode": {"resources": {"limits": {"cpu": "8"}}}}
            },
        ).eval()
        warnings = [str(c.args[0]) for c in logger.log_warning.call_args_list if c.args]
        assert not any("does not exist" in w for w in warnings)


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------


class TestArgparseWiring:
    def _parse(self, argv):
        from unittest.mock import patch

        from llmdbenchmark.cli import cli

        # cli() runs the whole pipeline; we only want the parser. Patch the
        # first thing that happens after parse_args to capture the namespace.
        captured = {}

        def _capture(args, *rest, **kwargs):
            captured["args"] = args
            raise SystemExit(0)

        with (
            patch("sys.argv", ["llmdbenchmark"] + argv),
            patch("llmdbenchmark.cli.dispatch_cli", _capture),
            patch("llmdbenchmark.cli._log_env_overrides", lambda *a, **k: None),
        ):
            try:
                cli()
            except SystemExit:
                pass
        return captured.get("args")

    def test_standup_set_maps_to_set_overrides(self):
        args = self._parse(
            [
                "--spec",
                "guides/optimized-baseline",
                "standup",
                "--set",
                "decode.replicas=4",
            ]
        )
        assert args is not None
        assert args.set_overrides == ["decode.replicas=4"]

    def test_repeated_set_flags_accumulate(self):
        args = self._parse(
            [
                "--spec",
                "guides/optimized-baseline",
                "standup",
                "--set",
                "a=1",
                "--set",
                "b=2",
            ]
        )
        assert args is not None
        assert args.set_overrides == ["a=1", "b=2"]

    @pytest.mark.parametrize("alias", ["-o", "--overrides", "--override"])
    def test_standup_rejects_workload_profile_spellings(self, alias):
        # One naming convention: `--set` is the only scenario-override
        # spelling. `-o`/`--overrides` mean the workload profile and must
        # NOT be accepted on standup, which has no workload profile --
        # otherwise the same word means two things across subcommands.
        args = self._parse(
            [
                "--spec",
                "guides/optimized-baseline",
                "standup",
                alias,
                "decode.replicas=4",
            ]
        )
        assert args is None  # argparse rejected it before dispatch

    def test_set_is_available_on_every_rendering_subcommand(self):
        for command in ("plan", "standup", "smoketest", "run", "teardown"):
            args = self._parse(
                [
                    "--spec",
                    "guides/optimized-baseline",
                    command,
                    "--set",
                    "decode.replicas=4",
                ]
            )
            assert args is not None, command
            assert args.set_overrides == ["decode.replicas=4"], command

    def test_run_dash_o_still_means_profile_overrides(self):
        args = self._parse(
            [
                "--spec",
                "guides/optimized-baseline",
                "run",
                "-o",
                "max-concurrency=8",
                "--set",
                "decode.replicas=4",
            ]
        )
        assert args is not None
        # The workload-profile flag is untouched...
        assert args.overrides == "max-concurrency=8"
        # ...and the scenario flag is separate.
        assert args.set_overrides == ["decode.replicas=4"]
