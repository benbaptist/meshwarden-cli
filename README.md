# meshwarden-cli

Minimal command-line client for [meshwarden-daemon](../daemon), over gRPC.
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

## Development

The gRPC definition lives in the shared `meshwarden-protos` repo, included as a
git submodule at `protos/`. Clone with `--recurse-submodules` (or run
`git submodule update --init`), then after changing the proto regenerate the stubs:

```sh
.venv/bin/pip install -e '.[dev]'
bash protos/gen_python.sh meshwarden_cli/pb
```

Run the tests with `.venv/bin/python -m unittest discover tests`.

## Usage

```sh
# daemon + device status
meshwarden status

# contacts
meshwarden contacts list
meshwarden contacts list alice              # substring filter
meshwarden contacts list --device-only
meshwarden contacts list --type 1           # 1=chat, 2=repeater, 3=room
meshwarden contacts show alice              # by name
meshwarden contacts show 80992ed24bb7       # by pubkey prefix (any hex length)

# channels
meshwarden channels
meshwarden channels backfill                # attribute stored channel messages to their secret
meshwarden channels backfill --secret "Old=<32 hex chars>"   # include a retired channel's secret

# trace a neighbouring repeater or room server (needs a known direct route)
meshwarden contacts ping alice

# messages — one scrollback per peer or channel
meshwarden messages alice                   # direct messages with alice
meshwarden messages 0                       # channel 0 (Public)
meshwarden messages "#berrien"              # channel by name
meshwarden messages alice --limit 100 --direction in

# send
meshwarden send alice "hello there"
meshwarden send alice -                     # read body from stdin
echo "long message" | meshwarden send alice -
meshwarden send-channel 0 "hello channel"
meshwarden send-channel "#berrien" "hello"

# scriptable JSON
meshwarden --json contacts list
meshwarden --json messages alice --limit 5 | jq '.messages[].text'
```

## Connection

Defaults to `127.0.0.1:50051`. Override with `--host` / `--port` or env
vars `MESHWARDEN_HOST` / `MESHWARDEN_PORT`. Set `MESHWARDEN_TLS=1` for TLS (with
optional `MESHWARDEN_CA` / `MESHWARDEN_CERT` / `MESHWARDEN_KEY`).
