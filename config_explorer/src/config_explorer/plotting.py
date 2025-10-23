"""
Plotting functions for configuration explorer.
"""

from typing import Any

import matplotlib.pyplot as plt
import pandas as pd

from .explorer import (
    COLUMNS,
    SLO,
    get_scenario_df,
    get_meet_slo_df,
    get_pareto_front_df
)

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

def plot_scenario(
        runs_df: pd.DataFrame,
        scenario: dict[str, Any],
        config_keys: list[str] | list[list[str]],
        col_x: str,
        col_y: str,
        col_seg_by: str = '',
        log_x: bool = False,
        log_y: bool = False) -> None:
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
    """
    plot = get_plot_scenario(
        runs_df=runs_df,
        scenario=scenario,
        config_keys=config_keys,
        col_x=col_x,
        col_y=col_y,
        col_seg_by=col_seg_by,
        log_x=log_x,
        log_y=log_y,
    )

    plot.show()

def get_plot_scenario(
        runs_df: pd.DataFrame,
        scenario: dict[str, Any],
        config_keys: list[str] | list[list[str]],
        col_x: str,
        col_y: str,
        col_seg_by: str = '',
        log_x: bool = False,
        log_y: bool = False) -> plt.Figure:

    """
    Returns the plot of a scenario from a column (Y) versus another
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
        The plot figure that can be rendered
    """

    # Create a new figure and axis
    fig, ax = plt.subplots()

    # Replace plt.plot etc. with ax.plot equivalents
    if log_x and log_y:
        plot_func = ax.loglog
    elif log_x:
        plot_func = ax.semilogx
    elif log_y:
        plot_func = ax.semilogy
    else:
        plot_func = ax.plot

    # Ensure config_keys is a list of lists
    if isinstance(config_keys[0], str):
        config_keys = [config_keys]

    for kk, ck_ in enumerate(config_keys):
        ck = ck_[:]
        if col_seg_by and col_seg_by not in ck:
            ck.append(col_seg_by)

        config_sets = list(set(runs_df.set_index(ck).index.dropna()))
        config_sets.sort()

        for ii, conf in enumerate(config_sets):
            conf_df = runs_df
            labels = []
            for jj, val in enumerate(conf):
                conf_df = conf_df[(conf_df[ck[jj]] == val)].sort_values(by=col_x)
                if ck[jj] == col_seg_by:
                    continue
                labels.append(f'{COLUMNS[ck[jj]].label}={val}')
            label = ', '.join(labels)

            plot_func(
                conf_df[col_x], conf_df[col_y],
                label=label,
                marker=MARKERS[kk % len(MARKERS)], markersize=4,
                color=COLORS[ii % len(COLORS)],
                linestyle=LINE_STYLES[kk % len(LINE_STYLES)]
            )

    ax.grid(True, linewidth=1, ls='--', color='gray')
    ax.legend(bbox_to_anchor=(1.05, 1), loc=2, borderaxespad=0.)

    title = ''
    for key, value in scenario.items():
        if len(title.rsplit('\n')[-1]) > 30:
            title += '\n'
        title += f'{COLUMNS[key].label}: {value}  '
    ax.set_title(title.strip())
    ax.set_xlabel(_column_axis_label(col_x), fontsize='16')
    ax.set_ylabel(_column_axis_label(col_y), fontsize='16')

    return fig

def get_scenario_tradeoff_plot(
        runs_df: pd.DataFrame,
        scenario: dict[str, Any],
        config_keys: list[str] | list[list[str]],
        col_x: str,
        col_y: str,
        col_z: str,
        col_seg_by: str = '',
        log_x: bool = False,
        log_y: bool = False) -> None:
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
        The tradeoff Pareto plot that cna be rendered
    """

    # --- Validation ---
    for col in scenario:
        if col not in runs_df.columns:
            raise KeyError(f'Invalid column: {col}')

    # Filter runs to specific scenario
    runs_df = get_scenario_df(runs_df, scenario)

    # Ensure we always have a list of configuration key groups
    if isinstance(config_keys[0], str):
        config_keys = [config_keys]

    # Create figure/axes (no global pyplot state pollution)
    fig, ax = plt.subplots(figsize=(8, 5))

    # Set scaling
    if log_x:
        ax.set_xscale('log')
    if log_y:
        ax.set_yscale('log')

    # Helper for y-offset for point labels (works for both linear and log scales)
    def _label_y_offset(y_values):
        if len(y_values) == 0:
            return 0.0
        ymin = min(y_values)
        ymax = max(y_values)
        if ymin == ymax:
            # Small absolute nudge if constant line
            return (abs(ymin) if ymin != 0 else 1.0) * 0.02
        # 2% of the range
        return (ymax - ymin) * 0.02

    # Plot each configuration group
    for kk, ck_ in enumerate(config_keys):
        # Make a copy so we can modify without side effects
        ck = ck_[:]
        if col_seg_by and col_seg_by not in ck:
            ck.append(col_seg_by)

        # If any requested config key is missing, skip cleanly
        for c in ck:
            if c not in runs_df.columns:
                raise KeyError(f'Invalid configuration column: {c}')

        # Determine unique combinations (drop rows with NA in any of these keys)
        subset_df = runs_df.dropna(subset=ck)
        if subset_df.empty:
            continue

        # Unique configurations as tuples in a stable order
        if len(ck) == 1:
            # Single-key: get unique values as 1-tuples for consistency
            config_sets = [(v,) for v in sorted(subset_df[ck[0]].unique())]
        else:
            # Multi-key: use MultiIndex unique tuples
            config_sets = list(
                set(map(tuple, subset_df[ck].itertuples(index=False, name=None)))
            )
            config_sets.sort()

        for ii, conf in enumerate(config_sets):
            # Build boolean mask for this configuration
            conf_df = subset_df.copy()
            labels = []
            for jj, val in enumerate(conf):
                key = ck[jj]
                conf_df = conf_df[conf_df[key] == val]
                if key == col_seg_by:
                    # segmentation key appears in color/linestyle only, not in label
                    continue
                # Respect provided COLUMNS structure for labels
                labels.append(f'{COLUMNS[key].label}={val}')

            if conf_df.empty:
                continue

            conf_df = conf_df.sort_values(by=col_z)
            label = ', '.join(labels)

            # Choose style elements
            marker = MARKERS[kk % len(MARKERS)]
            color = COLORS[ii % len(COLORS)]
            linestyle = LINE_STYLES[kk % len(LINE_STYLES)]

            # Plot this series
            ax.plot(
                conf_df[col_x], conf_df[col_y],
                label=label if label else None,
                marker=marker, markersize=4,
                color=color, linestyle=linestyle
            )

            # Add Z labels near points
            x_vals = list(conf_df[col_x])
            y_vals = list(conf_df[col_y])
            y_offset = _label_y_offset(y_vals)
            for xpt, ypt, zlab in zip(x_vals, y_vals, conf_df[col_z]):
                ax.text(
                    xpt, ypt + y_offset,
                    str(zlab),
                    ha='center',
                    color=color
                )

    # Axis limits (reproducing the previous intent, but via axes API)
    # We let Matplotlib autoscale first, then adjust mins if needed.
    ax.relim()
    ax.autoscale()

    x_lo, x_hi = ax.get_xlim()
    y_lo, y_hi = ax.get_ylim()

    if not log_x:
        x_lo = 0 if x_lo is None else min(0, x_lo)
    if not log_y:
        y_lo = 0 if y_lo is None else min(0, y_lo)

    # Apply the adjusted bounds
    ax.set_xlim(left=x_lo, right=x_hi)
    ax.set_ylim(bottom=y_lo, top=y_hi)

    # Title building
    title = ''
    for key, value in scenario.items():
        if len(title.rsplit('\n')[-1]) > 30:
            title += '\n'
        title += f'{COLUMNS[key].label}: {value}  '
    title = title.strip()
    title += f'\n\nPoint labels: {_column_axis_label(col_z)}'

    ax.set_title(title)
    ax.set_xlabel(_column_axis_label(col_x), fontsize=16)
    ax.set_ylabel(_column_axis_label(col_y), fontsize=16)

    # Legend (place outside right)
    # Hide legend if no labeled series were added
    handles, labels = ax.get_legend_handles_labels()
    if any(lbl for lbl in labels):
        ax.legend(bbox_to_anchor=(1.05, 1), loc='upper left', borderaxespad=0.)

    ax.grid(True, linewidth=1, ls='--', color='gray')

    # Return the figure; caller (e.g., Streamlit) can render it
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
        log_y: bool = False) -> None:
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
    """

    plot = get_scenario_tradeoff_plot(
        runs_df=runs_df,
        scenario=scenario,
        config_keys=config_keys,
        col_x=col_x,
        col_y=col_y,
        col_z=col_z,
        col_seg_by=col_seg_by,
        log_x=log_x,
        log_y=log_y,
    )

    plot.show()

def plot_pareto_tradeoff(
        runs_df: pd.DataFrame,
        scenario: dict[str, Any],
        col_x: str,
        col_y: str,
        slos: list[SLO] = [],
        log_x: bool = False,
        log_y: bool = False) -> None:
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
    """
    for col in scenario:
        if col not in runs_df.columns:
            raise KeyError(f'Invalid column: {col}')

    # Filter runs to specific scenario
    scenario_df = get_scenario_df(runs_df, scenario)
    # Get just the rows that meet SLOs
    meet_slo_df = get_meet_slo_df(scenario_df, slos)
    # From rows matching SLOs, get rows on Pareto front
    pareto_df = get_pareto_front_df(meet_slo_df, col_x, col_y)
    # Rows that fail SLOs
    fail_slo_df = scenario_df[~scenario_df.index.isin(meet_slo_df.index.tolist())]
    # Rows that meet SLOs, but are not on the Pareto front
    meet_slo_not_pareto_df = meet_slo_df[~meet_slo_df.index.isin(pareto_df.index.tolist())]

    if log_x and log_y:
        plot_func = plt.loglog
    elif log_x:
        plot_func = plt.semilogx
    elif log_y:
        plot_func = plt.semilogy
    else:
        plot_func = plt.plot

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

    title = ''
    for key, value in scenario.items():
        if len(title.rsplit('\n')[-1]) > 30:
            title += '\n'
        title += f'{COLUMNS[key].label}: {value}  '
    title.strip()
    plt.title(title)
    plt.xlabel(_column_axis_label(col_x), fontsize='16')
    plt.ylabel(_column_axis_label(col_y), fontsize='16')
    plt.legend(bbox_to_anchor=(1.05, 1), loc=2, borderaxespad=0.)
    plt.grid(True, linewidth=1, ls='--', color='gray')
    plt.show()
