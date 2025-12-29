import grpc
import time
from concurrent import futures
from rpc import pbft_pb2, pbft_pb2_grpc

class PBFTServer(pbft_pb2_grpc.PBFTServiceServicer):
    def __init__(self, node):
        self.node = node

    def SubmitRequest(self, req, ctx):
        return self.node.on_client_request(req)

    def PrePrepare(self, req, ctx):
        return self.node.on_pre_prepare(req)

    def Prepare(self, req, ctx):
        return self.node.on_prepare(req)

    def Commit(self, req, ctx):
        return self.node.on_commit(req)

    def SetView(self, req, ctx):
        return self.node.on_set_view(req)

    def GetStatus(self, request, context):
        state = self.node.state
        return pbft_pb2.StatusReply(
            node_id=state.node_id,
            role=state.role,
            view=state.view,
            alive=state.alive,
            primary_id=state.primary_id,
            f=state.f,
        )

    def KillNode(self, req, ctx):
        self.node.state.alive = False
        return pbft_pb2.Empty()
    
    def Ping(self, request, context):
        print(f"[RPC] Node {self.node.state.node_id} received Ping!")
        return pbft_pb2.PingReply(
            message=f"Node {self.node.state.node_id} alive"
        )


def serve(node, port):
    server = grpc.server(futures.ThreadPoolExecutor(10))
    pbft_pb2_grpc.add_PBFTServiceServicer_to_server(
        PBFTServer(node), server
    )
    server.add_insecure_port(f"[::]:{port}")
    server.start()
    print(f"[RPC] Node {node.state.node_id} on {port}")

    server.wait_for_termination()
