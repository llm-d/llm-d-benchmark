"""
Main Page
"""

import streamlit as st

if __name__ == '__main__':

    # Set up streamlit config
    st.set_page_config(page_title="Configuration Recommendation Tool",
                       page_icon=None,
                       layout="wide",
                       initial_sidebar_state="expanded",
                       menu_items=None)

    st.title("Configuration Recommendation")
    st.caption("Finding the most cost-effective, optimal configuration for serving models on llm-d based on hardware specification, workload characteristics, and SLO requirements.")


    st.write("Some help on how to use this tool...")
    st.write("""Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat. Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla pariatur. Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia deserunt mollit anim id est laborum.
""")

    # # Input
    # col1, col2 = st.columns(2)
    # user_model = inputs(col1)

    # outputs(col2, user_model)