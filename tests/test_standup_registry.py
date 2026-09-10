"""Standup registry shape after the standup/run separation."""

from llmdbenchmark.standup.steps import get_standup_steps


def test_standup_has_no_harness_step() -> None:
    steps = get_standup_steps()
    assert all(s.name != "harness_namespace" for s in steps)


def test_standup_numbering_is_contiguous_after_renumber() -> None:
    numbers = sorted({s.number for s in get_standup_steps()})
    # 0=infra, 2=admin, 3=monitoring, 4=model ns, 5=deploy (4 variants),
    # 6=deploy setup, 7=router, 8=modelservice. (1 remains reserved.)
    assert numbers == [0, 2, 3, 4, 5, 6, 7, 8]


def test_deploy_variants_share_number_five() -> None:
    deploy_names = {
        "fma_deploy",
        "standalone_deploy",
        "kustomize_deploy",
        "nok8s_deploy",
    }
    steps = {s.name: s.number for s in get_standup_steps()}
    matched = {n: steps[n] for n in steps if n in deploy_names or steps[n] == 5}
    assert matched and all(v == 5 for v in matched.values())
