import streamlit as st
import pandas as pd
import streamlit as st
from numpy.random import default_rng as rng
import db

df = pd.DataFrame(rng(0).standard_normal((20, 3)), columns=["a", "b", "c"])

data = {
    "col1": 1
}

def table():
    st.dataframe(db.benchmark_data, use_container_width=True)

if __name__ == "__main__":
    st.title("Parameter Sweep and Search")
    st.caption("Search benchmarking results.")
    table()