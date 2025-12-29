import time

import streamlit as st

from core.cluster import ClusterManager
from ui.cluster_view import render_cluster_view
from ui.sidebar import render_sidebar

st.set_page_config(page_title="PBFT Simulator", layout="wide")
st.title("🛠️ PBFT Consensus Simulator")

# ============================
# INIT CLUSTER MANAGER
# ============================
if "cluster" not in st.session_state:
    st.session_state.cluster = ClusterManager()

cluster = st.session_state.cluster

# ============================
# SIDEBAR
# ============================
render_sidebar(cluster)

# ============================
# MAIN VIEW
# ============================
st.subheader("Cluster View")

if not cluster.nodes:
    st.info("Cluster not initialized. Choose node count and start cluster.")
else:
    render_cluster_view(cluster)

time.sleep(0.5)
st.rerun()
