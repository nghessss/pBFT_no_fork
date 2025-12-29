import time

import streamlit as st

from rpc import pbft_pb2
from rpc.client import PBFTClient


def render_sidebar(cluster):
    st.sidebar.header("⚙️ Cluster Controls (PBFT)")

    msg_box = st.sidebar.empty()

    node_count = st.sidebar.number_input(
        "Number of nodes",
        min_value=4,
        max_value=10,
        value=len(cluster.nodes) if cluster.nodes else 4,
        step=1,
    )

    ok, f = cluster.validate_node_count(int(node_count))
    if ok:
        st.sidebar.caption(f"PBFT constraint OK: n={int(node_count)} = 3*{f}+1")
    else:
        st.sidebar.caption("PBFT requires n = 3f + 1 (valid: 4, 7, 10)")

    if not cluster.is_running():
        if st.sidebar.button("Apply Node Count", use_container_width=True):
            cluster.resize(int(node_count))
            if cluster.last_error:
                msg_box.error(cluster.last_error)
            else:
                msg_box.success(f"Prepared {int(node_count)} nodes")
    else:
        msg_box.info("Stop cluster before resizing")

    col1, col2 = st.sidebar.columns(2)

    with col1:
        if st.button("▶️ Start Cluster", use_container_width=True):
            cluster.start_all()
            if cluster.last_error:
                msg_box.error(cluster.last_error)
            else:
                msg_box.success("Cluster started")

    with col2:
        if st.button("⛔ Stop Cluster", use_container_width=True):
            cluster.stop_all()
            msg_box.warning("Cluster stopped")

    st.sidebar.markdown("### 🔍 Node List")

    for node in cluster.nodes:
        node_id = int(node["id"])
        running = bool(node.get("process"))
        status_text = "RUNNING" if running else "STOPPED"

        st.sidebar.write(
            f"Node {node_id} | Port {node['port']} | {status_text} | "
            f"Byzantine={'ON' if node.get('byzantine') else 'OFF'}"
        )

        # Configure byzantine mode (requires restart)
        byz_value = st.sidebar.checkbox(
            f"Byzantine mode (Node {node_id})",
            value=bool(node.get("byzantine", False)),
            disabled=running,
            key=f"byz_mode_{node_id}",
            help="Stop the node to change this. When enabled, the replica will send malformed digests in Prepare/Commit.",
        )
        node["byzantine"] = bool(byz_value)

    st.sidebar.markdown("### 📩 Client Request")
    if not cluster.nodes:
        st.sidebar.info("Apply node count first (valid: 4, 7, 10).")
        return

    # Always target the current primary (disable dropdown selection)
    primary_id = None
    if cluster.is_running():
        for n in cluster.nodes:
            if not n.get("process"):
                continue
            try:
                status = PBFTClient(f"localhost:{n['port']}").get_status()
                primary_id = int(status.primary_id)
                break
            except Exception:
                continue

    # Fallback for initial view=0 when cluster isn't running yet
    if primary_id is None:
        primary_id = int(min(n["id"] for n in cluster.nodes))

    primary_node = next(
        (n for n in cluster.nodes if int(n["id"]) == int(primary_id)), None
    )
    if primary_node is None:
        st.sidebar.error("Primary node not found")
        return

    st.sidebar.selectbox(
        "Send to node",
        options=[int(primary_id)],
        format_func=lambda _: f"Node {primary_node['id']} (localhost:{primary_node['port']}) • PRIMARY",
        disabled=True,
    )
    payload = st.sidebar.text_input(
        "Payload",
        value="hello",
        disabled=not cluster.is_running(),
    )

    if st.sidebar.button(
        "Send Request", use_container_width=True, disabled=not cluster.is_running()
    ):
        try:
            client = PBFTClient(f"localhost:{primary_node['port']}")
            now = int(time.time() * 1000)
            req = pbft_pb2.ClientRequest(
                client_id="streamlit",
                request_id=str(now),
                timestamp_ms=now,
                payload=payload,
                forwarded=False,
            )
            reply = client.client_request(req, timeout=30.0)
            if reply.error:
                st.sidebar.error(reply.error)
            else:
                st.sidebar.success(
                    f"Committed={reply.committed} view={reply.view} seq={reply.seq} primary={reply.replica_id}"
                )
        except Exception as e:
            st.sidebar.error(f"Send failed: {e}")
