"""
GPU Recommender Page - Streamlit UI for GPU recommendation engine
This page helps users find the optimal GPU for running LLM inference.
"""

import json
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots

# Import the recommender
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from config_explorer.recommender.recommender import GPURecommender
from llm_optimizer.predefined.gpus import GPU_SPECS

# Helper function to convert result objects to JSON-serializable format
def result_to_dict(result) -> dict:
    """Convert a PerformanceEstimationResult to a JSON-serializable dictionary.

    Recursively converts all nested objects, lists, and dictionaries to ensure
    complete serialization of the PerformanceEstimationResult.
    """

    def convert_value(val):
        """Recursively convert a value to JSON-serializable format."""
        if val is None:
            return None
        elif isinstance(val, (int, float, str, bool)):
            return val
        elif isinstance(val, (list, tuple)):
            return [convert_value(item) for item in val]
        elif isinstance(val, dict):
            return {k: convert_value(v) for k, v in val.items()}
        elif hasattr(val, '__dict__'):
            # Convert object to dictionary recursively
            obj_dict = {}
            for k, v in val.__dict__.items():
                if not k.startswith('_'):  # Skip private attributes
                    obj_dict[k] = convert_value(v)
            return obj_dict
        else:
            # For other types, convert to string
            return str(val)

    return convert_value(result)

# Page configuration
st.set_page_config(
    page_title="GPU Recommender",
    layout="wide"
)

# Initialize session state
if 'recommendation_results' not in st.session_state:
    st.session_state.recommendation_results = None
if 'failed_gpus' not in st.session_state:
    st.session_state.failed_gpus = None
if 'recommender_params' not in st.session_state:
    st.session_state.recommender_params = None
if 'recommender_instance' not in st.session_state:
    st.session_state.recommender_instance = None

# Title and description
st.title("GPU Recommendation Engine")
st.markdown("This tool helps you find the optimal GPU for running LLM inference by predicting inference performance.")

# Sidebar for inputs
st.sidebar.header("⚙️ Configuration")

# Model configuration section
st.sidebar.subheader("Model Configuration")
model_id = st.sidebar.text_input(
    "Model ID (HuggingFace)",
    value="Qwen/Qwen-7B",
    help="Enter the HuggingFace model ID"
)

# Workload parameters
st.sidebar.subheader("Workload Parameters")
input_len = st.sidebar.number_input(
    "Input Sequence Length",
    min_value=1,
    max_value=128000,
    value=1024,
    step=128,
    help="Expected input sequence length in tokens"
)

output_len = st.sidebar.number_input(
    "Output Sequence Length",
    min_value=1,
    max_value=128000,
    value=1024,
    step=128,
    help="Expected output sequence length in tokens"
)

max_gpus = st.sidebar.number_input(
    "Maximum GPUs",
    min_value=1,
    value=1,
    step=1,
    help="Maximum number of GPUs to use for inference, affects TP and DP values."
)

# Performance constraints section
st.sidebar.subheader("Performance Constraints (Optional)")
st.sidebar.markdown("Set SLO requirements. Leave empty for no constraint.")

enable_ttft = st.sidebar.checkbox("Enable TTFT constraint", value=False)
max_ttft = None
if enable_ttft:
    max_ttft = st.sidebar.number_input(
        "Max Time to First Token (ms)",
        min_value=1.0,
        value=1000.0,
        step=10.0,
        help="Maximum acceptable time to first token in milliseconds"
    )

enable_itl = st.sidebar.checkbox("Enable ITL constraint", value=False)
max_itl = None
if enable_itl:
    max_itl = st.sidebar.number_input(
        "Max Inter-Token Latency (ms)",
        min_value=1.0,
        value=100.0,
        step=10.0,
        help="Maximum acceptable inter-token latency in milliseconds"
    )

enable_latency = st.sidebar.checkbox("Enable E2E Latency constraint", value=False)
max_latency = None
if enable_latency:
    max_latency = st.sidebar.number_input(
        "Max End-to-End Latency (s)",
        min_value=0.0,
        value=100.0,
        step=1.0,
        help="Maximum acceptable end-to-end latency in seconds"
    )

# GPU Selection
st.sidebar.subheader("GPU Selection (Optional)")
available_gpus = sorted(list(GPU_SPECS.keys()))
selected_gpus = st.sidebar.multiselect(
    "Select GPUs to analyze",
    options=available_gpus,
    default=None,
    help="Select specific GPUs to analyze. Leave empty to analyze all available GPUs."
)

# Per-GPU max_gpus configuration
st.sidebar.subheader("GPU Count Configuration (Optional)")
enable_per_gpu_config = st.sidebar.checkbox(
    "Configure max GPUs per GPU type",
    value=False,
    help="Set different maximum GPU counts for each GPU type. When disabled, all GPUs use the default max GPU value."
)

max_gpus_per_type = {}
if enable_per_gpu_config:
    st.sidebar.markdown("Set maximum GPU count for each GPU type:")

    # Get list of GPUs to configure (either selected or all)
    gpus_to_configure = selected_gpus if selected_gpus else available_gpus

    # Create a form or expandable section for cleaner UI
    with st.sidebar.expander("⚙️ Configure GPU Counts", expanded=True):
        for gpu_name in gpus_to_configure:
            gpu_max = st.number_input(
                f"{gpu_name}",
                min_value=1,
                value=max_gpus,  # Default to the general max_gpus value
                step=1,
                key=f"max_gpus_{gpu_name}",
                help=f"Maximum number of {gpu_name} GPUs to use"
            )
            max_gpus_per_type[gpu_name] = gpu_max

# Cost Configuration
st.sidebar.subheader("💰 Custom GPU Costs (Optional)")

# Cost unit selector
cost_unit = st.sidebar.radio(
    "Cost Unit",
    options=["per_hour", "per_1m_tokens"],
    format_func=lambda x: "$/hour" if x == "per_hour" else "$/1M tokens",
    index=0,
    help="Select the unit for GPU costs. Default values are relative reference values for comparison."
)

# Import CostManager to get default costs
from config_explorer.recommender.cost_manager import CostManager, CostUnit
# Cast cost_unit to CostUnit type for type safety
cost_unit_typed: CostUnit = cost_unit  # type: ignore
cost_manager_temp = CostManager(cost_unit=cost_unit_typed)

custom_gpu_costs = {}
with st.sidebar.expander("⚙️ Set Custom Costs", expanded=False):
    # List of GPUs to configure - use the 8 specified GPUs
    priority_gpus = ["H100", "H200", "A100", "A100-40GB", "L20", "L40", "B100", "B200"]

    # Use selected GPUs if any, otherwise show all priority GPUs that are available
    if selected_gpus:
        gpus_for_cost = selected_gpus
    else:
        gpus_for_cost = [gpu for gpu in priority_gpus if gpu in available_gpus]

    for gpu_name in gpus_for_cost:
        # Get default cost as initial value
        default_cost = cost_manager_temp.get_cost(gpu_name, num_gpus=1)
        default_value = default_cost if default_cost is not None else 0.0

        # Format label based on cost unit
        unit_label = cost_manager_temp.get_cost_unit_label()

        cost = st.number_input(
            f"{gpu_name} ({unit_label})",
            min_value=0.0,
            value=default_value,
            step=0.5,
            key=f"cost_{gpu_name}",
            help=f"Custom cost for {gpu_name} (default: {default_value:.2f} {unit_label})"
        )
        # Only add to custom costs if different from default
        if cost != default_value:
            custom_gpu_costs[gpu_name] = cost

# Conditional disclaimer based on whether using custom costs
if custom_gpu_costs:
    st.sidebar.caption(f"💡 Displaying costs in {cost_manager_temp.get_cost_unit_label()}")
else:
    st.sidebar.caption(f"💡 Initial cost values ({cost_manager_temp.get_cost_unit_label()}) are relative reference values for comparison purposes.")

# Run button
run_analysis = st.sidebar.button("🚀 Run Analysis", type="primary", use_container_width=True)

# Main content area
if run_analysis:
    with st.spinner("Running GPU recommendation analysis... This may take a few moments."):
        try:
            # Create recommender instance
            recommender = GPURecommender(
                model_id=model_id,
                input_len=input_len,
                output_len=output_len,
                max_gpus=max_gpus,
                max_gpus_per_type=max_gpus_per_type if max_gpus_per_type else None,
                gpu_list=selected_gpus if selected_gpus else None,
                max_ttft=max_ttft,
                max_itl=max_itl,
                max_latency=max_latency,
                custom_gpu_costs=custom_gpu_costs if custom_gpu_costs else None,
                cost_unit=cost_unit_typed
            )

            # Run recommendation
            gpu_results, failed_gpus = recommender.get_gpu_results()

            # Store in session state
            st.session_state.recommendation_results = gpu_results
            st.session_state.failed_gpus = failed_gpus
            st.session_state.recommender_instance = recommender
            st.session_state.recommender_params = {
                'model_id': model_id,
                'input_len': input_len,
                'output_len': output_len,
                'max_gpus': max_gpus,
                'max_gpus_per_type': max_gpus_per_type if max_gpus_per_type else None,
                'max_ttft': max_ttft,
                'max_itl': max_itl,
                'custom_gpu_costs': custom_gpu_costs if custom_gpu_costs else None,
                'max_latency': max_latency,
                'cost_unit': cost_unit_typed
            }

            st.success("✅ Analysis complete!")

        except Exception as e:
            # Clear any previous results from session state
            st.session_state.recommendation_results = None
            st.session_state.failed_gpus = None
            st.session_state.recommender_instance = None
            st.session_state.recommender_params = None

            error_str = str(e).lower()

            # Check for gated model errors
            if "gated" in error_str or "401" in error_str or "403" in error_str or "unauthorized" in error_str:
                st.error("🔒 **This model is gated and requires authentication**")
                st.info("""
                **To access gated models:**

                1. **Request access** to the model on [HuggingFace](https://huggingface.co/)
                2. **Generate a token** from your [HuggingFace settings page](https://huggingface.co/settings/tokens)
                3. **Set the environment variable** before running the application:
                   ```bash
                   export HF_TOKEN=your_token_here
                   ```
                4. **Restart** the Streamlit application

                **Popular gated models:** Llama 3, Gemma, Mistral, etc.
                """)
                with st.expander("🔍 View detailed error"):
                    st.exception(e)
            else:
                st.error(f"❌ Error running analysis: {str(e)}")
                st.exception(e)

# Display results if available
if st.session_state.recommendation_results is not None:
    gpu_results = st.session_state.recommendation_results
    failed_gpus = st.session_state.failed_gpus
    params = st.session_state.recommender_params

    # Show constraints if any
    constraints = []
    if params['max_ttft']:
        constraints.append(f"TTFT ≤ {params['max_ttft']} ms")
    if params['max_itl']:
        constraints.append(f"ITL ≤ {params['max_itl']} ms")
    if params['max_latency']:
        constraints.append(f"Latency ≤ {params['max_latency']} ms")

    if constraints:
        st.info("🎯 **Constraints:** " + " & ".join(constraints))


    # Initialize tracking lists
    gpu_comparison_data = []
    gpus_cannot_fit = []
    gpus_no_data = []

    # Results section
    if len(gpu_results) > 0:
        st.header("🏆 Recommended GPUs")

        # Prepare data for visualization
        for gpu_name, result in gpu_results.items():
            try:
                # Extract relevant metrics from the result
                gpu_info = {
                    'GPU': gpu_name,
                }

                # Try to extract best config info
                if hasattr(result, 'best_configs') and result.best_configs:
                    best_config = result.best_configs[0] if isinstance(result.best_configs, list) else result.best_configs

                    best_latency_performnace_result = result.best_configs.get('best_latency') if isinstance(result.best_configs, dict) else None

                    # Check if best_latency result is None or empty
                    if best_latency_performnace_result is None:
                        gpus_cannot_fit.append(gpu_name)
                        continue

                    # Check if we have actual performance data
                    has_data = False
                    if hasattr(best_latency_performnace_result, 'output_throughput_tps') and best_latency_performnace_result.output_throughput_tps is not None:
                        gpu_info['Throughput (tokens/s)'] = best_latency_performnace_result.output_throughput_tps
                        has_data = True
                    if hasattr(best_latency_performnace_result, 'ttft_ms') and best_latency_performnace_result.ttft_ms is not None:
                        gpu_info['TTFT (ms)'] = best_latency_performnace_result.ttft_ms
                        has_data = True
                    if hasattr(best_latency_performnace_result, 'itl_ms') and best_latency_performnace_result.itl_ms is not None:
                        gpu_info['ITL (ms)'] = best_latency_performnace_result.itl_ms
                        has_data = True
                    if hasattr(best_latency_performnace_result, 'e2e_latency_s') and best_latency_performnace_result.e2e_latency_s is not None:
                        gpu_info['E2E Latency (s)'] = best_latency_performnace_result.e2e_latency_s
                        has_data = True

                    if not has_data:
                        gpus_no_data.append(gpu_name)
                        continue

                # Extract optimal concurrency if available
                if hasattr(result, 'optimal_concurrency') and result.optimal_concurrency is not None:
                    gpu_info['Optimal Concurrency'] = result.optimal_concurrency

                # Add cost information
                num_gpus = recommender.max_gpus_per_type.get(gpu_name, recommender.max_gpus)
                cost = recommender.cost_manager.get_cost(gpu_name, num_gpus)
                if cost is not None:
                    cost_label = f"Cost ({recommender.cost_manager.get_cost_unit_label()})"
                    gpu_info[cost_label] = cost

                gpu_comparison_data.append(gpu_info)
            except Exception as e:
                # If we get an error, likely the GPU cannot fit the model
                gpus_cannot_fit.append(gpu_name)

        # Create comparison dataframe
        if len(gpu_comparison_data) == 0:
            # Display compatibility and failure information
            status_messages = []

            # Add compatibility status
            if gpus_cannot_fit:
                status_messages.append(("error", f"**{len(gpus_cannot_fit)} GPU(s) cannot fit this model:** {', '.join(gpus_cannot_fit)}"))

            # Add no data status
            if gpus_no_data:
                status_messages.append(("warning", f"**{len(gpus_no_data)} GPU(s) have no performance data:** {', '.join(gpus_no_data)}"))

            # Add failed analysis status
            if failed_gpus:
                status_messages.append(("warning", f"**{len(failed_gpus)} GPU(s) failed analysis:** {', '.join(failed_gpus.keys())}"))

            # Display status messages if any
            if status_messages:

                for msg_type, msg in status_messages:
                    if msg_type == "error":
                        st.error(f"❌ {msg}")
                    elif msg_type == "warning":
                        st.warning(f"⚠️ {msg}")

                # Show details in columns for better visibility
                detail_cols = []
                if gpus_cannot_fit:
                    detail_cols.append("cannot_fit")
                if gpus_no_data:
                    detail_cols.append("no_data")
                if failed_gpus:
                    detail_cols.append("failed")

                # Determine column layout based on number of issues
                if len(detail_cols) == 3:
                    col_detail1, col_detail2, col_detail3 = st.columns(3)

                    with col_detail1:
                        st.markdown("**💡 GPUs that cannot fit:**")
                        st.caption("Insufficient memory")
                        for gpu in gpus_cannot_fit:
                            st.write(f"• {gpu}")

                    with col_detail2:
                        st.markdown("**📊 No performance data:**")
                        st.caption("Missing latency/throughput metrics")
                        for gpu in gpus_no_data:
                            st.write(f"• {gpu}")

                    with col_detail3:
                        st.markdown("**⚠️ Failed analysis:**")
                        st.caption("Estimation errors")
                        with st.expander("View reasons", expanded=False):
                            for gpu, reason in failed_gpus.items():
                                st.write(f"**{gpu}:**")
                                st.caption(reason)
                                st.divider()

                elif len(detail_cols) == 2:
                    col_detail1, col_detail2 = st.columns(2)

                    with col_detail1:
                        if "cannot_fit" in detail_cols:
                            st.markdown("**💡 GPUs that cannot fit:**")
                            st.caption("Insufficient memory to load the model and process the workload")
                            for gpu in gpus_cannot_fit:
                                st.write(f"• {gpu}")
                        elif "no_data" in detail_cols:
                            st.markdown("**📊 GPUs with no performance data:**")
                            st.caption("Missing latency or throughput metrics")
                            for gpu in gpus_no_data:
                                st.write(f"• {gpu}")

                    with col_detail2:
                        if "no_data" in detail_cols and "cannot_fit" in detail_cols:
                            st.markdown("**📊 GPUs with no performance data:**")
                            st.caption("Missing latency or throughput metrics")
                            for gpu in gpus_no_data:
                                st.write(f"• {gpu}")
                        elif "failed" in detail_cols:
                            st.markdown("**⚠️ GPUs that failed analysis:**")
                            st.caption("Encountered errors during performance estimation")
                            with st.expander("View failure reasons", expanded=False):
                                for gpu, reason in failed_gpus.items():
                                    st.write(f"**{gpu}:**")
                                    st.caption(reason)
                                    st.divider()

                elif len(detail_cols) == 1:
                    if "cannot_fit" in detail_cols:
                        st.markdown("**💡 GPUs that cannot fit:**")
                        st.caption("Insufficient memory to load the model and process the workload")
                        for gpu in gpus_cannot_fit:
                            st.write(f"• {gpu}")

                    elif "no_data" in detail_cols:
                        st.markdown("**📊 GPUs with no performance data:**")
                        st.caption("These GPUs returned no latency or throughput metrics. This may indicate compatibility issues or estimation problems.")
                        for gpu in gpus_no_data:
                            st.write(f"• {gpu}")

                    elif "failed" in detail_cols:
                        st.markdown("**⚠️ GPUs that failed analysis:**")
                        st.caption("Encountered errors during performance estimation")
                        for gpu, reason in failed_gpus.items():
                            with st.expander(f"**{gpu}**", expanded=False):
                                st.error(reason)

        if gpu_comparison_data:
            df = pd.DataFrame(gpu_comparison_data)

            # Sort by cost if enabled
            params = st.session_state.recommender_params
            cost_label = f"Cost ({recommender.cost_manager.get_cost_unit_label()})"
            if params.get('sort_by_cost', False) and cost_label in df.columns:
                df = df.sort_values(cost_label)

            # Combined Summary Section - Best GPUs and Compatibility Status
            st.subheader("⭐ Best GPU Recommendations")
            st.caption("These results represent best latency performance at concurrency = 1")

            # Create metric cards for best GPUs
            col1, col2, col3, col4, col5 = st.columns(5)

            # Get recommender instance from session state
            recommender = st.session_state.recommender_instance

            with col1:
                best_throughput = recommender.get_gpu_with_highest_throughput()
                if best_throughput:
                    best_gpu, best_val = best_throughput
                    st.metric(
                        "🚀 Highest Throughput",
                        f"{best_gpu}",
                        f"{best_val:.2f} tokens/s"
                    )

            with col2:
                best_ttft = recommender.get_gpu_with_lowest_ttft()
                if best_ttft:
                    best_gpu, best_val = best_ttft
                    st.metric(
                        "⚡ Lowest TTFT",
                        f"{best_gpu}",
                        f"{best_val:.2f} ms"
                    )

            with col3:
                best_itl = recommender.get_gpu_with_lowest_itl()
                if best_itl:
                    best_gpu, best_val = best_itl
                    st.metric(
                        "⏱️ Lowest ITL",
                        f"{best_gpu}",
                        f"{best_val:.2f} ms"
                    )

            with col4:
                best_e2e = recommender.get_gpu_with_lowest_e2e_latency()
                if best_e2e:
                    best_gpu, best_val = best_e2e
                    st.metric(
                        "🎯 Lowest E2E Latency",
                        f"{best_gpu}",
                        f"{best_val:.2f} s"
                    )

            with col5:
                best_cost = recommender.get_gpu_with_lowest_cost()
                if best_cost:
                    best_gpu, best_val = best_cost
                    cost_unit_label = recommender.cost_manager.get_cost_unit_label()
                    st.metric(
                        "💰 Lowest Cost",
                        f"{best_gpu}",
                        f"${best_val:.2f} {cost_unit_label}"
                    )

            # Show summary of excluded GPUs if any
            excluded_count = len(gpus_cannot_fit) + len(gpus_no_data) + len(failed_gpus)
            if excluded_count > 0:
                summary_parts = []
                if gpus_cannot_fit:
                    summary_parts.append(f"**{len(gpus_cannot_fit)}** cannot fit the model")
                if gpus_no_data:
                    summary_parts.append(f"**{len(gpus_no_data)}** have no performance data")
                if failed_gpus:
                    summary_parts.append(f"**{len(failed_gpus)}** don't meet constraints or failed analysis")

                summary_text = " • ".join(summary_parts)
                st.info(f"ℹ️ **{excluded_count} GPU(s) excluded:** {summary_text}")

                # Show details in an expander
                with st.expander("📋 View excluded GPUs details", expanded=False):
                    if gpus_cannot_fit:
                        st.markdown("**❌ Cannot Fit Model:**")
                        st.caption("Insufficient memory to load the model")
                        for gpu in gpus_cannot_fit:
                            st.write(f"• {gpu}")
                        if gpus_no_data or failed_gpus:
                            st.markdown("---")

                    if gpus_no_data:
                        st.markdown("**📊 No Performance Data:**")
                        st.caption("Missing latency or throughput metrics")
                        for gpu in gpus_no_data:
                            st.write(f"• {gpu}")
                        if failed_gpus:
                            st.markdown("---")

                    if failed_gpus:
                        st.markdown("**⚠️ Failed Analysis or Constraints Not Met:**")
                        st.caption("Errors during performance estimation or constraints violation")
                        for gpu, reason in failed_gpus.items():
                            with st.expander(f"**{gpu}**", expanded=False):
                                st.write(reason)


            st.divider()

            # Reorganized tabs
            st.subheader("Analysis Results")

            # Create tabs for different sections
            tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
                "Performance Visualizations",
                "Cost Analysis",
                "Model Details",
                "Detailed GPU Analysis",
                "LLM-Optimizer Commands",
                "Data Table"
            ])

            with tab1:
                st.markdown("### 📈 Performance Comparisons")

                # Throughput visualization
                st.markdown("#### 🚀 Throughput Comparison")
                if 'Throughput (tokens/s)' in df.columns:
                    df_sorted_throughput = df.sort_values('Throughput (tokens/s)', ascending=False)
                    fig_throughput = px.bar(
                        df_sorted_throughput,
                        x='GPU',
                        y='Throughput (tokens/s)',
                        title='GPU Throughput Comparison (Concurrency = 1)'
                    )
                    fig_throughput.update_layout(
                        xaxis_title="GPU Type",
                        yaxis_title="Throughput (tokens/s)",
                        showlegend=False,
                        height=500
                    )
                    st.plotly_chart(fig_throughput, use_container_width=True, key="overall_throughput_chart")
                else:
                    st.info("Throughput data not available in results")

                st.markdown("---")

                # Latency visualization
                st.markdown("#### ⚡ Latency Metrics")
                latency_cols = [col for col in df.columns if any(metric in col for metric in ['TTFT', 'ITL', 'Latency'])]
                if latency_cols:
                    fig_latency = make_subplots(
                        rows=1,
                        cols=len(latency_cols),
                        subplot_titles=latency_cols
                    )

                    for idx, col in enumerate(latency_cols, 1):
                        fig_latency.add_trace(
                            go.Bar(
                                x=df['GPU'],
                                y=df[col],
                                name=col,
                                marker_color=px.colors.qualitative.Set2[idx-1]
                            ),
                            row=1,
                            col=idx
                        )

                    fig_latency.update_layout(
                        title_text="Latency Metrics Comparison (Concurrency = 1)",
                        showlegend=False,
                        height=500
                    )
                    st.plotly_chart(fig_latency, use_container_width=True, key="overall_latency_chart")
                else:
                    st.info("Latency metrics not available in results")

                st.markdown("---")

                # Concurrency visualization
                st.markdown("#### 🔄 Concurrency Analysis")
                if 'Optimal Concurrency' in df.columns:
                    # Filter out N/A values and ensure we have numeric data
                    df_concurrency = df[df['Optimal Concurrency'].notna()].copy()
                    if not df_concurrency.empty:
                        # Sort by optimal concurrency for better visualization
                        df_concurrency = df_concurrency.sort_values('Optimal Concurrency', ascending=False)

                        fig_concurrency = px.bar(
                            df_concurrency,
                            x='GPU',
                            y='Optimal Concurrency',
                            title='Optimal Concurrency by GPU',
                            text='Optimal Concurrency',
                        )
                        fig_concurrency.update_traces(
                            texttemplate='%{text:.0f}',
                            textposition='outside',
                            marker_color='violet'
                        )
                        fig_concurrency.update_layout(
                            xaxis_title="GPU Type",
                            yaxis_title="Optimal Concurrency (concurrent requests)",
                            showlegend=False,
                            height=500
                        )
                        st.plotly_chart(fig_concurrency, use_container_width=True, key="overall_concurrency_chart")

                        # Show summary statistics
                        col_stat1, col_stat2 = st.columns(2)
                        with col_stat1:
                            st.metric("Highest Concurrency",
                                     f"{df_concurrency.loc[df_concurrency['Optimal Concurrency'].idxmax(), 'GPU']}",
                                     f"{df_concurrency['Optimal Concurrency'].max():.0f} requests")
                        with col_stat2:
                            st.metric("Lowest Concurrency",
                                     f"{df_concurrency.loc[df_concurrency['Optimal Concurrency'].idxmin(), 'GPU']}",
                                     f"{df_concurrency['Optimal Concurrency'].min():.0f} requests")
                    else:
                        st.info("No concurrency data available for the analyzed GPUs")
                else:
                    st.info("Concurrency data not available in results")

            with tab2:
                st.markdown("### 💰 Cost Analysis")
                
                # Show conditional disclaimer based on whether custom costs are used
                if recommender.cost_manager.is_using_custom_costs():
                    st.caption(f"💡 Displaying costs in {recommender.cost_manager.get_cost_unit_label()}")
                else:
                    st.caption(f"💡 Initial cost values ({recommender.cost_manager.get_cost_unit_label()}) are relative reference values for comparison purposes.")

                # Cost comparison chart
                cost_label = f"Cost ({recommender.cost_manager.get_cost_unit_label()})"
                if cost_label in df.columns:
                    st.markdown("#### 💵 Cost Comparison")
                    df_sorted_cost = df.sort_values(cost_label)
                    fig_cost = px.bar(
                        df_sorted_cost,
                        x='GPU',
                        y=cost_label,
                        title=f'GPU Cost Comparison ({recommender.cost_manager.get_cost_unit_label()})',
                        text=cost_label
                    )
                    fig_cost.update_traces(
                        texttemplate='$%{text:.2f}',
                        textposition='outside'
                    )
                    fig_cost.update_layout(
                        xaxis_title="GPU Type",
                        yaxis_title=cost_label,
                        showlegend=False,
                        height=500
                    )
                    st.plotly_chart(fig_cost, use_container_width=True, key="cost_comparison_chart")

                    st.markdown("---")

                    # Cost vs Performance scatter
                    st.markdown("#### 📊 Performance Cost Analysis")
                    if 'Throughput (tokens/s)' in df.columns:
                        st.caption("💡 Lower-right quadrant represents better value (high throughput, low latency)")
                        st.caption(f"🔵 Bubble size represents cost (larger bubbles = higher cost in {recommender.cost_manager.get_cost_unit_label()})")
                        st.caption("Small bubbles near the lower-right are the most performant and cost-effective solutions.")

                        # Filter out rows with NaN cost values
                        cost_label = f"Cost ({recommender.cost_manager.get_cost_unit_label()})"
                        df_cost_perf = df[df[cost_label].notna()].copy()

                        if not df_cost_perf.empty:
                            fig_cost_perf = px.scatter(
                                df_cost_perf,
                                x='Throughput (tokens/s)',
                                y='E2E Latency (s)',
                                text='GPU',
                                title='E2E Latency vs Throughput',
                                size=cost_label,
                                hover_data=['TTFT (ms)', 'ITL (ms)'] if 'TTFT (ms)' in df_cost_perf.columns else None
                            )
                            fig_cost_perf.update_traces(
                                textposition='top center',
                                marker=dict(sizemode='diameter', sizeref=2)
                            )
                            fig_cost_perf.update_layout(
                                xaxis_title="Throughput (tokens/s)",
                                yaxis_title="E2E Latency (s)",
                                height=500
                            )
                            st.plotly_chart(fig_cost_perf, use_container_width=True, key="cost_performance_scatter")

                        else:
                            st.info("Cost data not available for performance comparison")
                else:
                    st.info("Cost data not available in results")

            with tab3:
                st.markdown("### 🔧 Model Details")
                st.caption("Model card details")

                # Get model config from any result (they're all the same)
                sample_result = next(iter(gpu_results.values()))
                if hasattr(sample_result, 'model_config') and sample_result.model_config:
                    model_config = sample_result.model_config

                    col1, col2 = st.columns(2)

                    with col1:
                        st.markdown("**Architecture:**")
                        if hasattr(model_config, 'num_params'):
                            params_b = model_config.num_params / 1e9
                            st.write(f"• Parameters: `{params_b:.2f}B`")
                        if hasattr(model_config, 'num_layers'):
                            st.write(f"• Layers: `{model_config.num_layers}`")
                        if hasattr(model_config, 'hidden_dim'):
                            st.write(f"• Hidden Dimension: `{model_config.hidden_dim}`")
                        if hasattr(model_config, 'vocab_size'):
                            st.write(f"• Vocabulary Size: `{model_config.vocab_size:,}`")

                    with col2:
                        st.markdown("**Attention Configuration:**")
                        if hasattr(model_config, 'num_heads'):
                            st.write(f"• Attention Heads: `{model_config.num_heads}`")
                        if hasattr(model_config, 'num_kv_heads'):
                            st.write(f"• KV Heads: `{model_config.num_kv_heads}`")
                        if hasattr(model_config, 'inferred_precision'):
                            st.write(f"• Precision: `{model_config.inferred_precision}`")
                else:
                    st.info("Model configuration not available")

            with tab4:
                st.markdown("### 🔍 Detailed GPU Analysis")
                st.caption("Comprehensive performance breakdown for each GPU")

                # Create expandable sections for each GPU
                for gpu_name, result in gpu_results.items():
                    with st.expander(f"**{gpu_name}**"):

                        # Cost Information
                        st.markdown("#### 💰 Cost")
                        cost_col1, cost_col2 = st.columns(2)

                        num_gpus = recommender.max_gpus_per_type.get(gpu_name, recommender.max_gpus)
                        cost = recommender.cost_manager.get_cost(gpu_name, num_gpus)

                        with cost_col1:
                            cost_unit_label = recommender.cost_manager.get_cost_unit_label()
                            if cost is not None:
                                st.write(f"• **Cost ({cost_unit_label}):** `${cost:.2f}`")
                            else:
                                st.write(f"• **Cost ({cost_unit_label}):** `N/A`")

                        with cost_col2:
                            st.write(f"• **Number of GPUs:** `{num_gpus}`")
                            if params.get('custom_gpu_costs') and gpu_name in params.get('custom_gpu_costs', {}):
                                st.caption("🔧 Custom cost")

                        st.markdown("---")

                        # GPU Specifications
                        if gpu_name in GPU_SPECS:
                            gpu_spec = GPU_SPECS[gpu_name]
                            st.markdown("#### 💻 GPU Specifications")
                            col1, col2 = st.columns(2)

                            with col1:
                                st.write(f"• Compute: `{gpu_spec['FP16_TFLOPS']:.0f} TFLOPS (FP16)`")
                                st.write(f"• Memory: `{gpu_spec['VRAM_GB']} GB`")
                            with col2:
                                st.write(f"• Bandwidth: `{gpu_spec['Memory_Bandwidth_GBs']} GB/s`")
                                st.write(f"• Architecture: `{gpu_spec.get('Architecture', 'N/A')}`")

                            st.markdown("---")

                        # Concurrency Information
                        st.markdown("#### ⚙️ Concurrency Configuration")
                        conc_col1, conc_col2 = st.columns(2)

                        with conc_col1:
                            if hasattr(result, 'optimal_concurrency') and result.optimal_concurrency:
                                st.write(f"• **Optimal Concurrency:** `{result.optimal_concurrency}`")

                        with conc_col2:
                            if hasattr(result, 'concurrency_limits') and result.concurrency_limits:
                                limits = result.concurrency_limits
                                if isinstance(limits, dict):
                                    st.markdown("**Limits:**")
                                    for limit_name, limit_val in limits.items():
                                        formatted_name = limit_name.replace('_', ' ').title()
                                        st.write(f"• {formatted_name}: `{limit_val}`")

                        st.markdown("---")

                        # Best Configurations with Charts
                        if hasattr(result, 'best_configs') and result.best_configs:
                            st.markdown("#### 🏆 Best Configurations")

                            configs = result.best_configs
                            if isinstance(configs, dict):
                                # Prepare data for visualization
                                config_data = []
                                for config_type, perf_result in configs.items():
                                    if perf_result is None:
                                        continue

                                    config_row = {'Configuration': config_type.replace('_', ' ').title()}

                                    if hasattr(perf_result, 'ttft_ms') and perf_result.ttft_ms:
                                        config_row['TTFT (ms)'] = perf_result.ttft_ms
                                    if hasattr(perf_result, 'itl_ms') and perf_result.itl_ms:
                                        config_row['ITL (ms)'] = perf_result.itl_ms
                                    if hasattr(perf_result, 'e2e_latency_s') and perf_result.e2e_latency_s:
                                        config_row['E2E Latency (s)'] = perf_result.e2e_latency_s
                                    if hasattr(perf_result, 'output_throughput_tps') and perf_result.output_throughput_tps:
                                        config_row['Output Throughput (tok/s)'] = perf_result.output_throughput_tps
                                    if hasattr(perf_result, 'input_throughput_tps') and perf_result.input_throughput_tps:
                                        config_row['Input Throughput (tok/s)'] = perf_result.input_throughput_tps
                                    if hasattr(perf_result, 'requests_per_sec') and perf_result.requests_per_sec:
                                        config_row['Requests/sec'] = perf_result.requests_per_sec
                                    if hasattr(perf_result, 'concurrency') and perf_result.concurrency:
                                        config_row['Concurrency'] = perf_result.concurrency

                                    config_data.append(config_row)

                                if config_data:
                                    df_configs = pd.DataFrame(config_data)

                                    # Display as styled table
                                    st.dataframe(df_configs, use_container_width=True, hide_index=True)

                                    # Expandable resource details
                                    for config_type, perf_result in configs.items():
                                        if perf_result is None:
                                            continue
                                        with st.expander(f"📋 Resource Details - {config_type.replace('_', ' ').title()}"):
                                            res_col1, res_col2 = st.columns(2)

                                            with res_col1:
                                                st.markdown("**Memory & Compute:**")
                                                if hasattr(perf_result, 'memory_needed_gb') and perf_result.memory_needed_gb:
                                                    st.write(f"• Memory Needed: `{perf_result.memory_needed_gb:.2f} GB`")
                                                if hasattr(perf_result, 'usable_vram_gb') and perf_result.usable_vram_gb:
                                                    st.write(f"• Usable VRAM: `{perf_result.usable_vram_gb:.2f} GB`")
                                                if hasattr(perf_result, 'bottleneck_is_memory') and perf_result.bottleneck_is_memory is not None:
                                                    bottleneck = "Memory" if perf_result.bottleneck_is_memory else "Compute"
                                                    st.write(f"• Bottleneck: `{bottleneck}`")

                                            with res_col2:
                                                st.markdown("**Arithmetic Intensity:**")
                                                if hasattr(perf_result, 'prefill_arithmetic_intensity') and perf_result.prefill_arithmetic_intensity:
                                                    st.write(f"• Prefill: `{perf_result.prefill_arithmetic_intensity:.2f}`")
                                                if hasattr(perf_result, 'decode_arithmetic_intensity') and perf_result.decode_arithmetic_intensity:
                                                    st.write(f"• Decode: `{perf_result.decode_arithmetic_intensity:.2f}`")
                                                if hasattr(perf_result, 'hardware_ops_per_byte') and perf_result.hardware_ops_per_byte:
                                                    st.write(f"• HW Ops/Byte: `{perf_result.hardware_ops_per_byte:.2f}`")

                                            st.markdown("**Memory Bound:**")
                                            bound_col1, bound_col2 = st.columns(2)
                                            with bound_col1:
                                                if hasattr(perf_result, 'prefill_is_memory_bound') and perf_result.prefill_is_memory_bound is not None:
                                                    prefill_status = "✅ Yes" if perf_result.prefill_is_memory_bound else "❌ No"
                                                    st.write(f"• Prefill: {prefill_status}")
                                            with bound_col2:
                                                if hasattr(perf_result, 'decode_is_memory_bound') and perf_result.decode_is_memory_bound is not None:
                                                    decode_status = "✅ Yes" if perf_result.decode_is_memory_bound else "❌ No"
                                                    st.write(f"• Decode: {decode_status}")

            with tab5:
                st.markdown("### 🔧 LLM-Optimizer Tuning Commands")
                st.caption("Use these commands with the llm-optimizer engine for fine-tuning")

                # Create expandable sections for each GPU
                for gpu_name, result in gpu_results.items():
                    if hasattr(result, 'tuning_commands') and result.tuning_commands:
                        with st.expander(f"**{gpu_name}** - Tuning Commands"):
                            tuning_cmds = result.tuning_commands

                            if isinstance(tuning_cmds, dict):
                                for complexity_level, frameworks in tuning_cmds.items():
                                    st.markdown(f"#### {complexity_level.title()} Tuning")

                                    if isinstance(frameworks, dict):
                                        for framework_name, framework_data in frameworks.items():
                                            st.markdown(f"**{framework_name.upper()}:**")

                                            if isinstance(framework_data, dict) and 'commands' in framework_data:
                                                commands = framework_data['commands']
                                                if isinstance(commands, list):
                                                    for idx, cmd in enumerate(commands, 1):
                                                        st.code(cmd, language='bash')
                    else:
                        with st.expander(f"**{gpu_name}**"):
                            st.info("No tuning commands available for this GPU")

            with tab6:
                st.markdown("### 📊 GPU Performance Comparison Table")
                st.caption("Download or sort the complete performance data")

                # Add sorting options
                sort_col1, sort_col2 = st.columns([3, 1])
                with sort_col1:
                    # Get available metric columns for sorting
                    metric_columns = [col for col in df.columns if col != 'GPU' and df[col].dtype in ['float64', 'int64']]

                    if metric_columns:
                        sort_by = st.selectbox(
                            "Sort by:",
                            options=['GPU (Name)'] + metric_columns,
                            index=1 if len(metric_columns) > 0 else 0,
                            help="Select a metric to sort the GPU comparison table"
                        )
                    else:
                        sort_by = 'GPU (Name)'

                with sort_col2:
                    if sort_by != 'GPU (Name)':
                        # Smart default based on metric type
                        if any(term in sort_by for term in ['Latency', 'TTFT', 'ITL']):
                            default_order = 'Ascending'
                        else:
                            default_order = 'Descending'

                        sort_order = st.radio(
                            "Order:",
                            options=['Descending', 'Ascending'],
                            index=0 if default_order == 'Descending' else 1,
                            help="Higher values first (Descending) or lower values first (Ascending)"
                        )
                    else:
                        sort_order = 'Ascending'

                    # Add helper text
                if sort_by != 'GPU (Name)':
                    if any(term in sort_by for term in ['Latency', 'TTFT', 'ITL']):
                        st.caption("ℹ️ Lower latency values are better")
                    else:
                        st.caption("ℹ️ Higher throughput values are better")

                # Apply sorting
                if sort_by == 'GPU (Name)':
                    df_sorted = df.sort_values('GPU', ascending=True)
                else:
                    ascending = (sort_order == 'Ascending')
                    df_sorted = df.sort_values(sort_by, ascending=ascending)

                # Display the sorted table
                st.dataframe(
                    df_sorted,
                    use_container_width=True,
                    hide_index=True
                )

        # Export functionality
        st.divider()
        st.subheader("💾 Export Results")

        col1, col2 = st.columns(2)

        with col1:
            # Export successful results as JSON
            export_data = {
                'parameters': params,
                'successful_gpus': {},
                'failed_gpus': failed_gpus
            }

            for gpu_name, result in gpu_results.items():
                try:
                    export_data['successful_gpus'][gpu_name] = result_to_dict(result)
                except Exception as e:
                    export_data['successful_gpus'][gpu_name] = {'error': str(e)}

            json_str = json.dumps(export_data, indent=2)

            st.download_button(
                label="📥 Download Results (JSON)",
                data=json_str,
                file_name=f"gpu_recommendation_{params['model_id'].replace('/', '_')}.json",
                mime="application/json",
                use_container_width=True
            )

        with col2:
            # Export comparison table as CSV
            if gpu_comparison_data:
                csv = df.to_csv(index=False)
                st.download_button(
                    label="📥 Download Comparison Table (CSV)",
                    data=csv,
                    file_name=f"gpu_comparison_{params['model_id'].replace('/', '_')}.csv",
                    mime="text/csv",
                    use_container_width=True
                )

    else:
        # No compatible GPUs found
        st.header("🏆 GPU Analysis Results")

        # Show summary
        total_analyzed = len(gpu_results)
        total_cannot_fit = len(gpus_cannot_fit)
        total_no_data = len(gpus_no_data)
        total_failed = len(failed_gpus)

        st.error(f"❌ **No compatible GPUs found** among {total_analyzed} analyzed GPU(s)")

        # Show breakdown in columns
        issue_cols = []
        if gpus_cannot_fit:
            issue_cols.append("cannot_fit")
        if gpus_no_data:
            issue_cols.append("no_data")
        if failed_gpus:
            issue_cols.append("failed")

        if len(issue_cols) == 3:
            col1, col2, col3 = st.columns(3)

            with col1:
                st.markdown(f"### ❌ Cannot Fit ({len(gpus_cannot_fit)})")
                st.caption("Insufficient memory")
                for gpu in gpus_cannot_fit:
                    st.write(f"• {gpu}")

            with col2:
                st.markdown(f"### 📊 No Data ({len(gpus_no_data)})")
                st.caption("Missing performance metrics")
                for gpu in gpus_no_data:
                    st.write(f"• {gpu}")

            with col3:
                st.markdown(f"### ⚠️ Failed ({len(failed_gpus)})")
                st.caption("Estimation errors")
                for gpu, reason in failed_gpus.items():
                    with st.expander(f"**{gpu}**", expanded=False):
                        st.error(reason)

        elif len(issue_cols) == 2:
            col1, col2 = st.columns(2)

            with col1:
                if "cannot_fit" in issue_cols:
                    st.markdown(f"### ❌ Cannot Fit ({len(gpus_cannot_fit)})")
                    st.caption("Insufficient memory for model and workload")
                    for gpu in gpus_cannot_fit:
                        st.write(f"• {gpu}")
                elif "no_data" in issue_cols:
                    st.markdown(f"### 📊 No Performance Data ({len(gpus_no_data)})")
                    st.caption("Missing latency or throughput metrics")
                    for gpu in gpus_no_data:
                        st.write(f"• {gpu}")

            with col2:
                if "no_data" in issue_cols and "cannot_fit" in issue_cols:
                    st.markdown(f"### 📊 No Performance Data ({len(gpus_no_data)})")
                    st.caption("Missing latency or throughput metrics")
                    for gpu in gpus_no_data:
                        st.write(f"• {gpu}")
                elif "failed" in issue_cols:
                    st.markdown(f"### ⚠️ Failed Analysis ({len(failed_gpus)})")
                    st.caption("Encountered errors during estimation")
                    for gpu, reason in failed_gpus.items():
                        with st.expander(f"**{gpu}**", expanded=False):
                            st.error(reason)

        elif len(issue_cols) == 1:
            if "cannot_fit" in issue_cols:
                st.markdown(f"### ❌ Cannot Fit ({len(gpus_cannot_fit)})")
                st.caption("Insufficient memory for model and workload")
                for gpu in gpus_cannot_fit:
                    st.write(f"• {gpu}")
            elif "no_data" in issue_cols:
                st.markdown(f"### 📊 No Performance Data ({len(gpus_no_data)})")
                st.caption("These GPUs returned no latency or throughput metrics")
                for gpu in gpus_no_data:
                    st.write(f"• {gpu}")
            elif "failed" in issue_cols:
                st.markdown(f"### ⚠️ Failed Analysis ({len(failed_gpus)})")
                st.caption("Encountered errors during estimation")
                for gpu, reason in failed_gpus.items():
                    with st.expander(f"**{gpu}**", expanded=False):
                        st.error(reason)

        # Provide helpful guidance
        st.divider()
        st.info("💡 **Suggestions:**")
        suggestions_col1, suggestions_col2 = st.columns(2)
        with suggestions_col1:
            st.markdown("""
            - Try a smaller model
            - Increase `max_gpus` for tensor parallelism
            - Select GPUs with more memory
            """)
        with suggestions_col2:
            st.markdown("""
            - Reduce input/output sequence lengths
            - Relax performance constraints
            - Check model compatibility
            """)

        # Check if we have any results at all or if all GPUs failed
        if len(gpu_results) == 0:
            st.error("❌ No GPUs were able to run the analysis. The model may be too large for the available GPUs.")
        else:
            st.warning("⚠️ No GPUs met the specified requirements. Try relaxing your performance constraints or selecting different GPUs.")

else:
    # Initial state - show instructions
    st.info("👈 Configure your model and workload parameters in the sidebar, then click **Run Analysis** to get GPU recommendations.")

    # Show available GPUs with specs
    st.subheader("📋 Available GPUs for Analysis")
    st.markdown(f"The analysis will evaluate **{len(GPU_SPECS)}** GPU types. Click on any GPU to view its specifications:")

    # Create expandable sections for each GPU
    gpu_list = sorted(GPU_SPECS.keys())

    # Display GPUs in a grid with expanders
    num_cols = 2
    for i in range(0, len(gpu_list), num_cols):
        cols = st.columns(num_cols)
        for col_idx, gpu_name in enumerate(gpu_list[i:i+num_cols]):
            with cols[col_idx]:
                with st.expander(f"**{gpu_name}**"):
                    gpu_spec = GPU_SPECS[gpu_name]

                    # Display GPU specifications
                    if isinstance(gpu_spec, dict):
                        # Memory
                        if 'VRAM_GB' in gpu_spec:
                            st.metric("Memory", f"{gpu_spec['VRAM_GB']} GB")

                        # Memory Type
                        if 'Memory_Type' in gpu_spec:
                            st.write(f"**Memory Type:** {gpu_spec['Memory_Type']}")

                        # Memory bandwidth
                        if 'Memory_Bandwidth_GBs' in gpu_spec:
                            st.write(f"**Memory Bandwidth:** {gpu_spec['Memory_Bandwidth_GBs']} GB/s")

                        # FP16/FP8 TFLOPS
                        if 'FP16_TFLOPS' in gpu_spec:
                            st.write(f"**FP16 TFLOPS:** {gpu_spec['FP16_TFLOPS']:.1f}")
                        if 'FP8_TFLOPS' in gpu_spec and gpu_spec['FP8_TFLOPS'] is not None:
                            st.write(f"**FP8 TFLOPS:** {gpu_spec['FP8_TFLOPS']:.1f}")

                        # Architecture
                        if 'Architecture' in gpu_spec:
                            st.write(f"**Architecture:** {gpu_spec['Architecture']}")
                    else:
                        # Fallback if GPU_SPECS has a different structure
                        st.write(gpu_spec)

    # Show example use cases
    st.divider()
    st.subheader("💡 Example Use Cases")

    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown("**:computer: Chatbot**")
        st.markdown("""
        - Small to medium models
        - Low latency requirements
        - Moderate throughput
        - Example: Llama-2-7b
        """)

    with col2:
        st.markdown("**📄 Document Processing**")
        st.markdown("""
        - Long input sequences
        - Batch processing
        - High throughput priority
        - Example: Long-context models
        """)

    with col3:
        st.markdown("**⚡ Real-time Inference**")
        st.markdown("""
        - Strict TTFT requirements
        - Low ITL constraints
        - Optimized for speed
        - Example: Code completion
        """)
