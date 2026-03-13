"""
Plotting functions for configuration explorer.
"""

from math import log10
from typing import Any

import matplotlib.pyplot as plt
import pandas as pd

from .explorer import (
    COLUMNS,
    SLO,
    col_base,
    get_scenario_df,
    get_meet_slo_df,
    get_pareto_front_df,
    rebound_scenario,
)
from .constants import (
    BOUND_PREFIX_LEN,
    COLUMN_BOUND_STR,
)


# Figure number
fignum = 0

# Plot trace colors
COLORS = [
    '#FF0000', '#FFAA00', '#DDDD00', '#00DD00', '#00FFFF', '#0000FF',
    '#FF00FF', '#666666', '#000000', '#990000', '#777700', '#007700',
    '#009999', '#000099'
]


# Plot line styles
LINE_STYLES = [
    'solid', 'dashed', 'dashdot', 'dotted'
]


# Plot marker styles
MARKERS = [
    'o', 'v', 's', '*', 'd', 'X', 'p'
]


def _column_axis_label(col: str) -> str:
    """Get plot axis label for a column.

    Args:
        col (str): Column to make a label for.

    Returns
        str: Axis label.
    """
    label = COLUMNS[col].label
    if COLUMNS[col].units:
        label += ' (' + COLUMNS[col].units + ')'
    return label


def _make_title(scenario: dict[str, Any]) -> str:
    """Make a plot title that details the scenario.

    Args:
        scenario (dict[str, Any]): Scenario to describe as a multi-line title.

    Returns:
        str: Plot title
    """

    # Columns describing a bound
    cols_bounded = []

    title = ''
    for col, value in scenario.items():
        if col[:BOUND_PREFIX_LEN] in COLUMN_BOUND_STR:
            if col_base(col) not in cols_bounded:
                cols_bounded.append(col_base(col))
            # Handle bounded columns later
            continue
        if len(title.rsplit('\n')[-1]) > 30:
            title += '\n'
        title += f'{COLUMNS[col].label}: {value}  '

    # Add bounded columns to title
    for col in cols_bounded:
        if len(title.rsplit('\n')[-1]) > 30:
            title += '\n'
        # Strings describing value bound
        val_bounds = []
        for bound_type in COLUMN_BOUND_STR:
            # Bounded column name, as it will appear in a scenario
            col_bound = bound_type + col
            if col_bound not in scenario:
                # This bound type is not in the scenario
                continue
            value = scenario[col_bound]
            val_bounds.append(f'{COLUMN_BOUND_STR[bound_type]}{value}')
        title += f'{COLUMNS[col].label}: {" ".join(val_bounds)}  '

    return title.strip()


def plot_col_histogram(
        runs_df: pd.DataFrame,
        col: str,
        num_bins: int = 50) -> plt.Figure:
    """Plot a histogram of values for a column.

    Args:
        runs_df (pandas.DataFrame): Benchmark run data.
        col_x (str): Column to histogram.
        num_bins (int): Number of bins to use in histogram.

    Returns:
        matplotlib.pyplot.Figure: Plot figure.
    """
    if len(runs_df[col].dropna()) < 1:
        return

    global fignum
    fignum += 1
    fig = plt.figure(fignum)

    plt.hist(list(runs_df[col].dropna()), bins=num_bins, color='#0000FF')
    plt.xlabel(_column_axis_label(col), fontsize='16')
    plt.ylabel('Counts', fontsize='16')
    return fig


def plot_scenario_histogram(
        runs_df: pd.DataFrame,
        scenario: dict[str, Any],
        num_bins: int = 50) -> dict[str, plt.Figure]:
    """
    Plot value histograms for numeric columns in a scenario having a bound.
    Any columns having a single value will be skipped.

    Args:
        runs_df (pandas.DataFrame): Benchmark run data.
        scenario (dict[str, Any]): Scenario from benchmark data to plot.
        num_bins (int): Number of bins to use in histogram.

    Returns:
        dict[str, matplotlib.pyplot.Figure]: List of histogram plots.
    """
    figs = {}

    plot_cols = []
    for col in scenario:
        if col[:BOUND_PREFIX_LEN] not in COLUMN_BOUND_STR:
            # This is not a bounded column
            continue
        if col[BOUND_PREFIX_LEN:] in plot_cols:
            # This column was already plotted
            continue
        if len(runs_df[col[BOUND_PREFIX_LEN:]].dropna().unique()) < 2:
            # There is only a single value, no need to plot a histogram
            continue
        # Keep record of plotted columns
        plot_cols.append(col[BOUND_PREFIX_LEN:])
        # Create histogram figure
        figs[col[BOUND_PREFIX_LEN:]] = plot_col_histogram(
            runs_df, col[BOUND_PREFIX_LEN:], num_bins)

    return figs


def plot_scenario(
        runs_df: pd.DataFrame,
        scenario: dict[str, Any],
        config_keys: list[str] | list[list[str]],
        col_x: str,
        col_y: str,
        col_seg_by: str = '',
        log_x: bool = False,
        log_y: bool = False) -> plt.Figure:
    """Plot the metrics of a scenario from a column (Y) versus another
    column (X).

    An example would be viewing throughput (Y) vs queries per second (X).

    Args:
        runs_df (pandas.DataFrame): Benchmark run data.
        scenario (dict[str, Any]): Scenario from benchmark data to plot.
        config_keys (list[str] | list[list[str]]): a list of columns to be
            grouped together as a set of configuration parameters to be
            compared within the plot. Each unique grouping of these columns
            will be a trace on the plot. A list of configuration keys may
            also be provided (list of lists of column names).
        col_x (str): Column from benchmark data for X axis.
        col_y (str): Column from benchmark data for Y axis.
        col_seg_by (str): Group points with matching config_keys only
            if they come from rows where this column also matches. This is
            effectively another configuration key, but its value is not
            displayed on the plot. This is helpful when repeated runs of the
            same experiment are viewed, and this is set to the source
            directory that is common only to points within a run.
        log_x (bool): Plot X axis on log scale.
        log_y (bool): Plot Y axis on log scale.

    Returns:
        matplotlib.pyplot.Figure: Plot figure.
    """
    for col in scenario:
        if col_base(col) not in runs_df.columns:
            raise KeyError(f'Invalid column: {col_base(col)}')

    scenario = rebound_scenario(runs_df, scenario)

    # Filter runs to specific scenario
    runs_df = get_scenario_df(runs_df, scenario)

    if log_x and log_y:
        plot_func = plt.loglog
    elif log_x:
        plot_func = plt.semilogx
    elif log_y:
        plot_func = plt.semilogy
    else:
        plot_func = plt.plot

    # Ensure we always have a list of configuration keys (list of lists)
    if isinstance(config_keys[0], str):
        config_keys = [config_keys]

    global fignum
    fignum += 1
    fig = plt.figure(fignum)

    for kk, ck_ in enumerate(config_keys):
        # Make a copy of config keys so we can modify it without side effects.
        ck = ck_[:]
        if col_seg_by and col_seg_by not in ck:
            ck.append(col_seg_by)

        # Given configuration keys, find the set of unique combinations of
        # these columns within the dataset.
        config_sets = list(set(runs_df.set_index(ck).index.dropna()))
        config_sets.sort()

        for ii, conf in enumerate(config_sets):
            # Make a DataFrame for specific configuration
            conf_df = runs_df
            labels = []
            for jj, val in enumerate(conf):
                conf_df = conf_df[(conf_df[ck[jj]] == val)
                                  ].sort_values(by=col_x)
                if ck[jj] == col_seg_by:
                    continue
                labels.append(f'{COLUMNS[ck[jj]].label}={val}')
            label = ', '.join(labels)

            # Make plot
            plot_func(
                conf_df[col_x], conf_df[col_y],
                label=label,
                marker=MARKERS[kk % len(MARKERS)], markersize=4,
                color=COLORS[ii % len(COLORS)],
                linestyle=LINE_STYLES[kk % len(LINE_STYLES)]
            )

    if log_x and log_y:
        plt.axis([None, None, None, None])
    elif log_x:
        plt.axis([None, None, 0, None])
    elif log_y:
        plt.axis([0, None, None, None])
    else:
        plt.axis([0, None, 0, None])

    plt.title(_make_title(scenario))
    plt.xlabel(_column_axis_label(col_x), fontsize='16')
    plt.ylabel(_column_axis_label(col_y), fontsize='16')
    plt.legend(bbox_to_anchor=(1.05, 1), loc=2, borderaxespad=0.)
    plt.grid(True, linewidth=1, ls='--', color='gray')
    return fig


def plot_scenario_tradeoff(
        runs_df: pd.DataFrame,
        scenario: dict[str, Any],
        config_keys: list[str] | list[list[str]],
        col_x: str,
        col_y: str,
        col_z: str,
        col_seg_by: str = '',
        log_x: bool = False,
        log_y: bool = False) -> plt.Figure:
    """Make a plot displaying the tradeoff between two columns (X and Y)
    while a third column (Z) is changed.

    An example would be viewing throughput vs latency as concurrency is
    adjusted.

    Args:
        runs_df (pandas.DataFrame): Benchmark run data.
        scenario (dict[str, Any]): Scenario from benchmark data to plot.
        config_keys (list[str] | list[list[str]]): a list of columns to be
            grouped together as a set of configuration parameters to be
            compared within the plot. Each unique grouping of these columns
            will be a trace on the plot. A list of configuration keys may
            also be provided (list of lists of column names).
        col_x (str): Column from benchmark data to plot on X axis.
        col_y (str): Column from benchmark data to plot on Y axis.
        col_z (str): Column from benchmark data to label points with.
        col_seg_by (str): Group points with matching config_keys only
            if they come from rows where this column also matches. This is
            effectively another configuration key, but its value is not
            displayed on the plot. This is helpful when repeated runs of the
            same experiment are viewed, and this is set to the source
            directory that is common only to points within a run.
        log_x (bool): Plot X axis on log scale.
        log_y (bool): Plot Y axis on log scale.

    Returns:
        matplotlib.pyplot.Figure: Plot figure.
    """
    for col in scenario:
        if col_base(col) not in runs_df.columns:
            raise KeyError(f'Invalid column: {col_base(col)}')

    scenario = rebound_scenario(runs_df, scenario)

    # Filter runs to specific scenario
    runs_df = get_scenario_df(runs_df, scenario)

    if log_x and log_y:
        plot_func = plt.loglog
    elif log_x:
        plot_func = plt.semilogx
    elif log_y:
        plot_func = plt.semilogy
    else:
        plot_func = plt.plot

    # Ensure we always have a list of configuration keys (list of lists)
    if isinstance(config_keys[0], str):
        config_keys = [config_keys]

    global fignum
    fignum += 1
    fig = plt.figure(fignum)

    for kk, ck_ in enumerate(config_keys):
        # Make a copy of config keys so we can modify it without side effects.
        ck = ck_[:]
        if col_seg_by and col_seg_by not in ck:
            ck.append(col_seg_by)

        # Given configuration keys, find the set of unique combinations of
        # these columns within the dataset.
        config_sets = list(set(runs_df.set_index(ck).index.dropna()))
        config_sets.sort()

        for ii, conf in enumerate(config_sets):
            # Make a DataFrame for specific configuration
            conf_df = runs_df
            labels = []
            for jj, val in enumerate(conf):
                conf_df = conf_df[(conf_df[ck[jj]] == val)
                                  ].sort_values(by=col_z)
                if ck[jj] == col_seg_by:
                    continue
                labels.append(f'{COLUMNS[ck[jj]].label}={val}')
            label = ', '.join(labels)

            # Make plot
            plot_func(
                conf_df[col_x], conf_df[col_y],
                label=label,
                marker=MARKERS[kk % len(MARKERS)], markersize=4,
                color=COLORS[ii % len(COLORS)],
                linestyle=LINE_STYLES[kk % len(LINE_STYLES)]
            )
            # Add Z labels to plot
            for jj, val in enumerate(conf_df[col_z]):
                if log_y:
                    y_offset = list(conf_df[col_y])[jj] * \
                        log10(runs_df[col_y].max() - runs_df[col_y].min()) * 0.01
                else:
                    y_offset = runs_df[col_y].max() * 0.02
                plt.text(list(conf_df[col_x])[jj],
                         list(conf_df[col_y])[jj] + y_offset,
                         str(val), ha='center', color=COLORS[ii % len(COLORS)])

    if log_x and log_y:
        plt.axis([None, None, None, None])
    elif log_x:
        plt.axis([None, None, 0, None])
    elif log_y:
        plt.axis([0, None, None, None])
    else:
        plt.axis([0, None, 0, None])

    title = _make_title(scenario)
    title += f'\n\nPoint labels: {_column_axis_label(col_z)}'
    plt.title(title)
    plt.xlabel(_column_axis_label(col_x), fontsize='16')
    plt.ylabel(_column_axis_label(col_y), fontsize='16')
    plt.legend(bbox_to_anchor=(1.05, 1), loc=2, borderaxespad=0.)
    plt.grid(True, linewidth=1, ls='--', color='gray')
    return fig


def plot_pareto_tradeoff(
        runs_df: pd.DataFrame,
        scenario: dict[str, Any],
        col_x: str,
        col_y: str,
        slos: list[SLO] = [],
        log_x: bool = False,
        log_y: bool = False) -> plt.Figure:
    """Make a plot displaying the tradeoff between two columns (X and Y),
    highlighting the Pareto front and graying out points failng SLOs.

    Args:
        runs_df (pandas.DataFrame): Benchmark run data.
        scenario (dict[str, Any]): Scenario from benchmark data to select.
        col_x (str): Column from benchmark data to plot on X axis.
        col_y (str): Column from benchmark data to plot on Y axis.
        slos (list[SLO]): Service level objectives.
        log_x (bool): Plot X axis on log scale.
        log_y (bool): Plot Y axis on log scale.

    Returns:
        matplotlib.pyplot.Figure: Plot figure.
    """
    for col in scenario:
        if col_base(col) not in runs_df.columns:
            raise KeyError(f'Invalid column: {col_base(col)}')

    scenario = rebound_scenario(runs_df, scenario)

    # Filter runs to specific scenario
    scenario_df = get_scenario_df(runs_df, scenario)
    # Get just the rows that meet SLOs
    meet_slo_df = get_meet_slo_df(scenario_df, slos)
    # From rows matching SLOs, get rows on Pareto front
    pareto_df = get_pareto_front_df(meet_slo_df, col_x, col_y)
    # Rows that fail SLOs
    fail_slo_df = scenario_df[~scenario_df.index.isin(
        meet_slo_df.index.tolist())]
    # Rows that meet SLOs, but are not on the Pareto front
    meet_slo_not_pareto_df = meet_slo_df[~meet_slo_df.index.isin(
        pareto_df.index.tolist())]

    if log_x and log_y:
        plot_func = plt.loglog
    elif log_x:
        plot_func = plt.semilogx
    elif log_y:
        plot_func = plt.semilogy
    else:
        plot_func = plt.plot

    global fignum
    fignum += 1
    fig = plt.figure(fignum)

    plot_func(
        pareto_df[col_x], pareto_df[col_y],
        marker='o', markersize=4,
        color='#FF00FF',
        linestyle='',
        label='Pareto front'
    )
    plot_func(
        meet_slo_not_pareto_df[col_x], meet_slo_not_pareto_df[col_y],
        marker='o', markersize=4,
        color='#000000',
        linestyle='',
        label='Meets SLOs, non-optimal'
    )
    plot_func(
        fail_slo_df[col_x], fail_slo_df[col_y],
        marker='o', markersize=4,
        color='#CCCCCC',
        linestyle='',
        label='Fails SLOs'
    )

    if log_x and log_y:
        plt.axis([None, None, None, None])
    elif log_x:
        plt.axis([None, None, 0, None])
    elif log_y:
        plt.axis([0, None, None, None])
    else:
        plt.axis([0, None, 0, None])

    plt.title(_make_title(scenario))
    plt.xlabel(_column_axis_label(col_x), fontsize='16')
    plt.ylabel(_column_axis_label(col_y), fontsize='16')
    plt.legend(bbox_to_anchor=(1.05, 1), loc=2, borderaxespad=0.)
    plt.grid(True, linewidth=1, ls='--', color='gray')
    return fig
