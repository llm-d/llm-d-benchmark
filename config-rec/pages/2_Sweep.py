import streamlit as st
import db

def table(benchmark_data):
    """
    Display table of benchmark data
    """
    st.subheader("Benchmark data")
    st.dataframe(benchmark_data, use_container_width=True)

def user_graph(benchmark_data):
    """
    Let the user customize the graph
    """

    col1, col2, col3 = st.columns(3)
    input = col1.selectbox("Select an input", db.input_cols)
    output = col2.selectbox("Select an output", db.output_cols)
    color_by = col3.selectbox("Color by", db.get_color_by_col(input))

    # Prepare chart
    df = benchmark_data[[input, output, color_by]]
    st.bar_chart(df, x=input, y=output, color=color_by)
    with st.expander("See filtered data"):
        st.write(df)



if __name__ == "__main__":
    st.title("Parameter Sweep and Search")
    st.caption("Visualize benchmarking results.")

    benchmark_data = db.read_benchmark_data()
    user_graph(benchmark_data)
    table(benchmark_data)