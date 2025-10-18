"""
Plotting functions for configuration explorer.
"""

from typing import Any

import matplotlib.pyplot as plt
import pandas as pd

from explorer import (
    COLUMNS,
    SLO,
    get_scenario_df,
    get_meet_slo_df,
    get_pareto_front_df
)

# Plot trace colors
COLORS = [
    '#FF0000', '#FFAA00', '#DDDD00', '#00DD00', '#00FFFF',
    '#0000FF', '#FF00FF', '#666666', '#000000', '#990000', 
    '#777700', '#007700', '#009999', '#000099'
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
        config_keys: list[str],
        col_x: str,
        col_y: str,
        col_seg_by: str = '',
        log_x: bool = False,
        log_y: bool = False) -> None:
    """Plot the metrics of a scenario from a column (Y) versus another
    column (X).

    An example would be viewing throughput (Y) vs queries per second (X).

    config_keys is a list of columns to be grouped together as a set of
    configuration parameters to be compared within the plot. Each unique
    grouping of these columns will be a trace on the plot.

    Args:
        runs_df (pandas.DataFrame): Benchmark run data.
        scenario (dict[str, Any]): Scenario from benchmark data to plot.
        config_keys (list[str]): Columns to group together as a configuration
            key.
        col_x (str): Column from benchmark data for X axis.
        col_y (str): Column from benchmark data for Y axis.
        col_seg_by (str): Group points with matching config_keys only
            if they come from rows where this column also matches. This is
            effectively another configuartion key, but its value is not
            displayed on the plot. This is helpful when repeated runs of the
            same experiment are viewed, and this is set to the source
            directory that is common only to points within a run.
        log_x (bool): Plot X axis on log scale.
        log_y (bool): Plot Y axis on log scale.
    """
    for col in scenario:
        if col not in runs_df.columns:
            raise KeyError(f'Invalid column: {col}')
    
    # Filter runs to specific scenario
    runs_df = get_scenario_df(runs_df, scenario)
    
    # Get unique configurations of values for config_keys columns
    if col_seg_by and col_seg_by not in config_keys:
        # Make a copy of config_keys so we can modify it without side effects.
        config_keys = config_keys[:]
        config_keys.append(col_seg_by)

    # Given config_keys, find the set of unique combinations of these columns
    # within the dataset.
    config_sets = list(set(runs_df.set_index(config_keys).index.dropna()))
    config_sets.sort()

    if log_x and log_y:
        plot_func = plt.loglog
    elif log_x:
        plot_func = plt.semilogx
    elif log_y:
        plot_func = plt.semilogy
    else:
        plot_func = plt.plot

    for ii, conf in enumerate(config_sets):
        color = COLORS[ii%len(COLORS)]
        # Make a DataFrame for specific configuration
        conf_df = runs_df
        label = ''
        for jj, val in enumerate(conf):
            conf_df = conf_df[(conf_df[config_keys[jj]] == val)].sort_values(by=col_x)
            if config_keys[jj] == col_seg_by:
                continue
            label += f'{COLUMNS[config_keys[jj]].label}={val}, '
        # Remove trailing ", "
        label = label.rsplit(', ', 1)[0]

        # Make plot
        plot_func(conf_df[col_x], conf_df[col_y],
                 label=label,
                 marker='o', markersize=4,
                 color=color
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


def plot_scenario_tradeoff(
        runs_df: pd.DataFrame,
        scenario: dict[str, Any],
        config_keys: list[str],
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

    config_keys is a list of columns to be grouped together as a set of
    configuration parameters to be compared within the plot. Each unique
    grouping of these columns will be a trace on the plot.

    Args:
        runs_df (pandas.DataFrame): Benchmark run data.
        scenario (dict[str, Any]): Scenario from benchmark data to plot.
        config_keys (list[str]): Columns to group together as a configuration
            key.
        col_x (str): Column from benchmark data to plot on X axis.
        col_y (str): Column from benchmark data to plot on Y axis.
        col_z (str): Column from benchmark data to label points with.
        col_seg_by (str): Group points with matching config_keys only
            if they come from rows where this column also matches. This is
            effectively another configuartion key, but its value is not
            displayed on the plot. This is helpful when repeated runs of the
            same experiment are viewed, and this is set to the source
            directory that is common only to points within a run.
        log_x (bool): Plot X axis on log scale.
        log_y (bool): Plot Y axis on log scale.
    """
    for col in scenario:
        if col not in runs_df.columns:
            raise KeyError(f'Invalid column: {col}')
    
    # Filter runs to specific scenario
    runs_df = get_scenario_df(runs_df, scenario)
    
    # Get unique configurations of values for config_keys columns
    if col_seg_by and col_seg_by not in config_keys:
        # Make a copy of config_keys so we can modify it without side effects.
        config_keys = config_keys[:]
        config_keys.append(col_seg_by)

    # Given config_keys, find the set of unique combinations of these columns
    # within the dataset.
    config_sets = list(set(runs_df.set_index(config_keys).index.dropna()))
    config_sets.sort()

    if log_x and log_y:
        plot_func = plt.loglog
    elif log_x:
        plot_func = plt.semilogx
    elif log_y:
        plot_func = plt.semilogy
    else:
        plot_func = plt.plot

    for ii, conf in enumerate(config_sets):
        color = COLORS[ii%len(COLORS)]
        # Make a DataFrame for specific configuration
        conf_df = runs_df
        label = ''
        for jj, val in enumerate(conf):
            conf_df = conf_df[(conf_df[config_keys[jj]] == val)].sort_values(by=col_z)
            if config_keys[jj] == col_seg_by:
                continue
            label += f'{COLUMNS[config_keys[jj]].label}={val}, '
        # Remove trailing ", "
        label = label.rsplit(', ', 1)[0]

        # Make plot
        plot_func(conf_df[col_x], conf_df[col_y],
                 label=label,
                 marker='o', markersize=4,
                 color=color
                )
        # Add Z labels to plot
        for jj, val in enumerate(conf_df[col_z]):
            plt.text(list(conf_df[col_x])[jj],
                     list(conf_df[col_y])[jj]+runs_df[col_y].max()*0.02,
                     str(val), ha='center', color=color)

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
    title += f'\n\nPoint labels: {_column_axis_label(col_z)}'
    plt.title(title)
    plt.xlabel(_column_axis_label(col_x), fontsize='16')
    plt.ylabel(_column_axis_label(col_y), fontsize='16')
    plt.legend(bbox_to_anchor=(1.05, 1), loc=2, borderaxespad=0.)
    plt.grid(True, linewidth=1, ls='--', color='gray')
    plt.show()


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

    plot_func(pareto_df[col_x], pareto_df[col_y],
             marker='o', markersize=4,
             color='#FF00FF',
             linestyle='',
             label='Pareto front'
            )
    plot_func(meet_slo_not_pareto_df[col_x], meet_slo_not_pareto_df[col_y],
             marker='o', markersize=4,
             color='#000000',
             linestyle='',
             label='Meets SLOs, non-optimal'
            )
    plot_func(fail_slo_df[col_x], fail_slo_df[col_y],
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
