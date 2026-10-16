# meshcli

Minimalist CLI for [meshcore-daemon](../daemon). Talks gRPC. No TUI, no
colors, no progress bars — just plain text you can grep and pipe. Add
`--json` for scriptable output.

## Install

```sh
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/pip install -e ".[dev]"   # only needed to regenerate stubs
```

## Usage

```sh
# daemon + device status
meshcli status

# contacts
meshcli contacts list
meshcli contacts list alice              # substring filter
meshcli contacts list --device-only
meshcli contacts list --type 1           # 1=chat, 2=repeater, 3=room
meshcli contacts show alice              # by name
meshcli contacts show 80992ed24bb7       # by pubkey prefix (any hex length)

# channels
meshcli channels
meshcli channels backfill                # attribute stored channel messages to their secret
meshcli channels backfill --secret "Old=<32 hex chars>"   # include a retired channel's secret

# messages — one scrollback per peer or channel
meshcli messages alice                   # direct messages with alice
meshcli messages 0                       # channel 0 (Public)
meshcli messages "#berrien"              # channel by name
meshcli messages alice --limit 100 --direction in

# send
meshcli send alice "hello there"
meshcli send alice -                     # read body from stdin
echo "long message" | meshcli send alice -
meshcli send-channel 0 "hello channel"
meshcli send-channel "#berrien" "hello"

# scriptable JSON
meshcli --json contacts list
meshcli --json messages alice --limit 5 | jq '.messages[].text'
```

## Ping

`meshcli contacts ping <contact>` sends a
zero-hop trace to a neighbouring repeater or room server. `--hash-size 1|2|4`
selects the **trace hash width**, not payload length. By default it follows the
pubkey prefix you typed: the largest of 1/2/4 bytes not exceeding it (`3d` → 1,
`3d5a` or `3d5a80` → 2, 8+ hex chars → 4); names default to 1.
The ping RPC has a five-second deadline including command queueing and send
acknowledgement; contact-name resolution happens before that RPC.
Only a matching returned trace succeeds. `--json` includes elapsed milliseconds
and per-hop SNR values.

A known direct route is required; no implicit flood discovery or login is sent.
Wider hashes require an unambiguous known full key for each shorter stored hop.
The return route reverses the outward relays, so asymmetric paths may time out.
Servers must permit forwarding, and multi-byte traces require compatible firmware
(introduced in MeshCore v1.11). Traces are not authenticated identity proofs;
short hashes can collide. A timeout cannot recall a packet already queued on the radio.

## Connection

Defaults to `127.0.0.1:50051`. Override with `--host` / `--port` or env
vars `MESHCLI_HOST` / `MESHCLI_PORT`. Set `MESHCLI_TLS=1` for TLS (with
optional `MESHCLI_CA` / `MESHCLI_CERT` / `MESHCLI_KEY`).

## Regenerating stubs

```sh
bash scripts/gen_protos.sh
```

Reads `../daemon/protos/meshcored.proto` and writes into `meshcli/pb/`.
