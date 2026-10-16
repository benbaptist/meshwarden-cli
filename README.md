# meshcli

Minimal command-line client for [meshcore-daemon](../daemon), over gRPC.
Output is plain text you can grep and pipe, or JSON with `--json`.

## Vibecoded

This project was mostly written by AI coding agents as an experiment in
vibecoding. It works for me, but it hasn't had careful human review, so treat
it accordingly and read the code before trusting it with anything important.

## Install

```sh
python3 -m venv .venv
.venv/bin/pip install -e .
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

# trace a neighbouring repeater or room server (needs a known direct route)
meshcli contacts ping alice

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

## Connection

Defaults to `127.0.0.1:50051`. Override with `--host` / `--port` or env
vars `MESHCLI_HOST` / `MESHCLI_PORT`. Set `MESHCLI_TLS=1` for TLS (with
optional `MESHCLI_CA` / `MESHCLI_CERT` / `MESHCLI_KEY`).
