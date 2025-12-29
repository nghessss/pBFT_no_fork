import grpc
from rpc import pbft_pb2, pbft_pb2_grpc


class PBFTClient:
    def __init__(self, addr):
        self.addr = addr
        channel = grpc.insecure_channel(addr)
        self.stub = pbft_pb2_grpc.PBFTServiceStub(channel)
        
    def ping(self, timeout=1.0) -> str:
        res = self.stub.Ping(
            pbft_pb2.PingRequest(),
            timeout=timeout
        )
        return res.message

    def client_request(self, req: pbft_pb2.ClientRequest, timeout=30.0):
        try:
            return self.stub.SubmitRequest(req, timeout=timeout)
        except grpc.RpcError as e:
            code = e.code()
            # If the selected node is down, transparently retry other replicas.
            if code not in (grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED):
                raise

            last_err: grpc.RpcError = e
            for alt in self._fallback_addrs():
                if alt == self.addr:
                    continue
                try:
                    ch = grpc.insecure_channel(alt)
                    stub = pbft_pb2_grpc.PBFTServiceStub(ch)
                    return stub.SubmitRequest(req, timeout=timeout)
                except grpc.RpcError as e2:
                    last_err = e2
                    continue

            raise last_err

    def _fallback_addrs(self):
        """Best-effort replica discovery for the simulator.

        UI may target a crashed node; retry other default ports on the same host.
        """
        try:
            host, port_s = self.addr.rsplit(":", 1)
            port = int(port_s)
        except Exception:
            return [self.addr]

        # Simulator uses ports 5001..5010.
        if 5001 <= port <= 5010:
            return [f"{host}:{p}" for p in range(5001, 5011)]

        # If a custom port is used, just try itself.
        return [self.addr]

    def pre_prepare(self, req: pbft_pb2.PrePrepareRequest, timeout=5.0):
        return self.stub.PrePrepare(req, timeout=timeout)

    def prepare(self, req: pbft_pb2.PrepareRequest, timeout=5.0):
        return self.stub.Prepare(req, timeout=timeout)

    def commit(self, req: pbft_pb2.CommitRequest, timeout=5.0):
        return self.stub.Commit(req, timeout=timeout)

    def set_view(self, req: pbft_pb2.SetViewRequest, timeout=2.0):
        return self.stub.SetView(req, timeout=timeout)

    def get_status(self, timeout: float = 2.0):
        return self.stub.GetStatus(pbft_pb2.StatusRequest(), timeout=timeout)

    def kill(self):
        return self.stub.KillNode(pbft_pb2.Empty())
