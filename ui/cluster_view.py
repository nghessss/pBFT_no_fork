import streamlit as st

from rpc.client import PBFTClient


def render_cluster_view(cluster, max_cols: int = 4) -> None:
    nodes = list(cluster.nodes or [])
    if not nodes:
        return

    max_cols = max(1, int(max_cols))

    for i in range(0, len(nodes), max_cols):
        cols = st.columns(max_cols)
        for col, node in zip(cols, nodes[i : i + max_cols]):
            with col:
                node_id = int(node.get("id"))
                byzantine = bool(node.get("byzantine", False))
                running = bool(node.get("process"))

                # Controls ABOVE the node card (as requested)
                b1, b2 = st.columns(2)
                with b1:
                    if st.button(
                        "▶️ Start",
                        use_container_width=True,
                        disabled=running,
                        key=f"main_start_node_{node_id}",
                    ):
                        cluster.start_node(node_id)

                with b2:
                    if st.button(
                        "💥 Crash",
                        use_container_width=True,
                        disabled=not running,
                        key=f"main_crash_node_{node_id}",
                    ):
                        cluster.stop_node(node_id)

                # Node card
                if not running:
                    st.info(
                        f"Node {node_id}\n\nStatus: STOPPED\n\nByzantine: {'ON' if byzantine else 'OFF'}"
                    )
                    continue

                client = PBFTClient(f"localhost:{node['port']}")
                try:
                    status = client.get_status()
                except Exception:
                    st.warning(
                        f"Node {node_id}\n\nStatus: UNREACHABLE\n\nByzantine: {'ON' if byzantine else 'OFF'}"
                    )
                    continue

                header = f"Node {status.node_id} ({status.role})"
                body = (
                    f"View: {status.view}\n\n"
                    f"Primary: {status.primary_id}\n\n"
                    f"f: {status.f}\n\n"
                    f"Alive: {status.alive}\n\n"
                    f"Byzantine: {'ON' if byzantine else 'OFF'}"
                )

                if (
                    bool(status.alive)
                    and (not byzantine)
                    and (str(status.role) == "Primary")
                ):
                    st.warning(f"{header}\n\n{body}")
                elif bool(status.alive) and (not byzantine):
                    st.success(f"{header}\n\n{body}")
                else:
                    st.info(f"{header}\n\n{body}")
