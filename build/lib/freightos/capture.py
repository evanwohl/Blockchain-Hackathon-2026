import json
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT / "artifacts/reserve_fork"
RPC = "https://api.mainnet-beta.solana.com"
VAULT_PROGRAM = "KvauGMspG5k6rtzrqqn7WNn3oZdyKqLKwK2XWQ8FLjd"
CLOCK = "SysvarC1ock11111111111111111111111111111111"


def rpc(method, params):
    request = Request(
        RPC,
        json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        ).encode(),
        {"Content-Type": "application/json", "User-Agent": "FreightOS/1"},
    )
    with urlopen(request, timeout=60) as response:
        payload = json.load(response)
    if "error" in payload:
        raise RuntimeError(payload["error"])
    return payload["result"]
