import argparse
import time

from rpc.client import PBFTClient
from rpc import pbft_pb2


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--addr", type=str, default="localhost:5001", help="host:port of any replica")
    p.add_argument("--payload", type=str, required=True)
    p.add_argument("--client-id", type=str, default="cli")
    p.add_argument("--timeout", type=float, default=30.0)
    args = p.parse_args()

    client = PBFTClient(args.addr)
    now = int(time.time() * 1000)

    req = pbft_pb2.ClientRequest(
        client_id=args.client_id,
        request_id=str(now),
        timestamp_ms=now,
        payload=args.payload,
        forwarded=False,
    )

    reply = client.client_request(req, timeout=args.timeout)
    print(reply)


if __name__ == "__main__":
    main()
