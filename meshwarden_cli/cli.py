"""meshwarden command-line interface.

Subcommands (kept intentionally flat):

  status                              daemon + device status
  contacts list [query]               list contacts (optionally filtered)
  contacts show <query>               show one contact
  contacts ping <query>               zero-hop trace ping a repeater or room
  channels [list]                     list configured channels
  channels backfill [--secret N=HEX]  attribute stored channel messages to their secret
    messages list                       list conversation summaries
    messages read <target>              read one conversation
    messages send <target> <text>        send to a contact or channel

`<target>` and `<contact>` accept a name, a pubkey prefix (any hex length),
or a channel index. `<channel>` accepts an index or a channel name.
A channel given by name reads that channel's history even if its slot was
reused; a bare index reads everything recorded in that slot.

Global flags:
  --json           emit JSON instead of plain text (scriptable)
  --host HOST      daemon host (default 127.0.0.1, env MESHWARDEN_HOST)
  --port PORT      daemon port (default 50051, env MESHWARDEN_PORT)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Sequence

from . import __version__
from .client import Client, call
from .output import (
    print_channels,
    print_contact,
    print_contacts,
    print_conversations,
    print_messages,
    print_status,
)
from .resolve import (
    AmbiguousError,
    InvalidError,
    NotFoundError,
    fail,
    MessageTarget,
    channel_target,
    contact_target,
    key_prefix_bytes,
    numeric_channel,
    resolve_contact,
    resolve_message_target,
    split_target,
)


# MeshCore contact types (must match output.CONTACT_TYPE_NAMES).
TYPE_CHAT = 1
TYPE_REPEATER = 2
TYPE_ROOM = 3


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="meshwarden",
        description="Minimalist CLI for meshwarden-daemon (gRPC).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Environment:\n"
            "  MESHWARDEN_HOST   daemon host (default 127.0.0.1)\n"
            "  MESHWARDEN_PORT   daemon port (default 50051)\n"
            "  MESHWARDEN_TLS    set to 1 to use TLS\n"
        ),
    )
    p.add_argument("--version", action="version", version=f"meshwarden {__version__}")
    p.add_argument("--json", action="store_true", help="emit JSON instead of plain text")
    p.add_argument("--host", default=None, help="daemon host (overrides MESHWARDEN_HOST)")
    p.add_argument("--port", default=None, type=int, help="daemon port (overrides MESHWARDEN_PORT)")

    p.set_defaults(help_parser=p)
    sub = p.add_subparsers(dest="cmd", metavar="COMMAND")

    # status ---------------------------------------------------------------
    sub.add_parser("status", help="show daemon + device status")

    # contacts -------------------------------------------------------------
    contacts = sub.add_parser("contacts", help="contact operations")
    contacts.set_defaults(help_parser=contacts)
    contacts_sub = contacts.add_subparsers(dest="contacts_cmd", metavar="ACTION")

    cl = contacts_sub.add_parser("list", help="list contacts")
    cl.add_argument("query", nargs="?", default="", help="substring of name or pubkey prefix")
    cl.add_argument("--device-only", action="store_true", help="only contacts on the device")
    cl.add_argument("--type", type=int, default=None,
                    help="filter by contact type (1=chat, 2=repeater, 3=room)")
    cl.add_argument("--limit", type=int, default=100)
    cl.add_argument("--offset", type=int, default=0)

    cs = contacts_sub.add_parser("show", help="show one contact")
    cs.add_argument("query", help="name, pubkey, or pubkey prefix")

    ping = contacts_sub.add_parser("ping", help="trace ping a repeater or room (five-second deadline)")
    ping.add_argument("query", help="contact name or public key/prefix")
    ping.add_argument("--hash-size", type=int, choices=(1, 2, 4),
                      help="trace hash width in bytes, not payload size (default: bytes of "
                           "pubkey prefix given, rounded down to 1/2/4; 1 for names)")

    # channels -------------------------------------------------------------
    channels = sub.add_parser("channels", help="list configured channels (default) or backfill history")
    channels_sub = channels.add_subparsers(dest="channels_cmd", metavar="ACTION")
    channels_sub.add_parser("list", help="list configured channels")
    backfill = channels_sub.add_parser(
        "backfill", help="attribute stored channel messages to their channel secret"
    )
    backfill.add_argument(
        "--secret", action="append", default=[], type=_channel_secret, metavar="NAME=HEX",
        help="extra (e.g. retired) channel secret, 32 hex chars; repeatable",
    )

    # messages -------------------------------------------------------------
    msg = sub.add_parser("messages", help="list, read, or send conversations")
    msg.set_defaults(help_parser=msg)
    msg_sub = msg.add_subparsers(dest="messages_cmd", metavar="ACTION")
    msg_sub.add_parser("list", help="list channels, room servers, and PMs with counts")
    target_help = (
        "name/partial name, public key/prefix, or channel index; "
        "use contact:QUERY or channel:QUERY to disambiguate (bare numbers are channels)"
    )
    read = msg_sub.add_parser("read", help="read stored messages")
    read.add_argument("target", help=target_help)
    read.add_argument("--limit", type=_positive_uint32, default=50)
    read.add_argument("--offset", type=_uint32, default=0)
    read.add_argument("--direction", choices=("in", "out", "both"), default="both")
    send = msg_sub.add_parser("send", help="send to a contact or channel")
    send.add_argument("target", help=target_help)
    send.add_argument("text", help="message text (or '-' to read from stdin)")

    return p


def _uint32(value: str) -> int:
    number = int(value)
    if not 0 <= number <= 0xFFFFFFFF:
        raise argparse.ArgumentTypeError("must be between 0 and 4294967295")
    return number


def _positive_uint32(value: str) -> int:
    number = _uint32(value)
    if not number:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def _channel_secret(value: str) -> tuple[str, bytes]:
    name, sep, hex_secret = value.rpartition("=")
    try:
        secret = bytes.fromhex(hex_secret)
    except ValueError:
        secret = b""
    if not sep or not name or len(secret) != 16:
        raise argparse.ArgumentTypeError("expected NAME=HEX with a 16-byte (32 hex char) secret")
    return name, secret


def _apply_overrides(args: argparse.Namespace) -> None:
    if args.host:
        os.environ["MESHWARDEN_HOST"] = args.host
    if args.port:
        os.environ["MESHWARDEN_PORT"] = str(args.port)


def _read_text(arg: str) -> str:
    if arg != "-":
        return arg
    return sys.stdin.read().rstrip("\n")


def cmd_status(client: Client, args: argparse.Namespace) -> int:
    s = call(client.get_status)
    print_status(s, args.json)
    return 0


def cmd_contacts_list(client: Client, args: argparse.Namespace) -> int:
    resp = call(lambda: client.list_contacts(
        query=args.query,
        device_only=args.device_only,
        limit=args.limit,
        offset=args.offset,
    ))
    rows = resp.contacts
    if args.type is not None:
        rows = [c for c in rows if c.type == args.type]
    print_contacts(rows, args.json, total=resp.total)
    return 0


def cmd_contacts_show(client: Client, args: argparse.Namespace) -> int:
    contacts = call(client.all_contacts)
    try:
        c = resolve_contact(args.query, contacts)
    except (AmbiguousError, NotFoundError, InvalidError) as e:
        fail(e)
    print_contact(c, args.json)
    return 0


def cmd_channels(client: Client, args: argparse.Namespace) -> int:
    resp = call(client.list_channels)
    print_channels(resp.channels, args.json)
    return 0


def cmd_channels_backfill(client: Client, args: argparse.Namespace) -> int:
    result = call(lambda: client.backfill_message_channels(args.secret))
    if args.json:
        print(json.dumps({
            "decrypted": result.decrypted, "inferred": result.inferred, "unresolved": result.unresolved,
        }))
    else:
        print(f"decrypted: {result.decrypted}  inferred: {result.inferred}  unresolved: {result.unresolved}")
    return 0


def cmd_ping(client: Client, args: argparse.Namespace) -> int:
    contact = resolve_contact(args.query, call(client.all_contacts), types=(TYPE_REPEATER, TYPE_ROOM))
    width = args.hash_size
    if width is None:
        typed = key_prefix_bytes(args.query, contact)
        width = max(w for w in (1, 2, 4) if w <= max(typed, 1))
    result = call(lambda: client.ping(contact.public_key, width))
    if args.json:
        print(json.dumps({"public_key": contact.public_key, "hash_size": width,
                          "elapsed_ms": result.elapsed_ms, "snr": list(result.snr)}))
    else:
        print(f"trace returned via {contact.adv_name}: {result.elapsed_ms:.1f} ms "
              f"({width}-byte hash)")
    return 0


def _resolve_message_target(
    client: Client, target: str
) -> MessageTarget:
    kind, query = split_target(target)
    if kind != "contact" and numeric_channel(query) is not None:
        return resolve_message_target(target)
    contacts = call(client.all_contacts) if kind != "channel" else []
    channels = call(client.list_channels).channels if kind != "contact" else []
    return resolve_message_target(target, contacts, channels)


def cmd_messages_list(client: Client, args: argparse.Namespace) -> int:
    targets = [channel_target(ch) for ch in call(client.list_channels).channels]
    targets.extend(contact_target(c) for c in call(client.all_contacts) if c.type in (TYPE_CHAT, TYPE_ROOM))
    conversations = []
    for target in targets:
        response = call(lambda: client.list_messages(**target.history_filter(), limit=1))
        conversations.append({
            "target": target.selector,
            "name": target.name,
            "type": target.type,
            "message_count": response.total,
            "last_activity": response.messages[0].ts if response.messages else None,
        })
    conversations.sort(key=lambda row: (-(row["last_activity"] or 0), row["name"], row["target"]))
    print_conversations(conversations, args.json)
    return 0


def cmd_messages_read(client: Client, args: argparse.Namespace) -> int:
    target = _resolve_message_target(client, args.target)
    direction = "" if args.direction == "both" else args.direction
    resp = call(lambda: client.list_messages(
        **target.history_filter(),
        direction=direction,
        limit=args.limit,
        offset=args.offset,
    ))
    print_messages(resp.messages, args.json, total=resp.total)
    return 0


def cmd_messages_send(client: Client, args: argparse.Namespace) -> int:
    text = _read_text(args.text)
    if not text:
        print("error: empty message", file=sys.stderr)
        return 1
    target = _resolve_message_target(client, args.target)
    if target.channel_idx is not None:
        result = call(lambda: client.send_channel_message(target.channel_idx, text))
    else:
        result = call(lambda: client.send_message(target.peer, text))
    if not result.ok:
        print(f"error: send failed: {result.error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({
            "ok": True,
            "target": target.selector,
            "to": target.name,
            "type": target.type,
            "channel_idx": target.channel_idx,
            "expected_ack": result.expected_ack,
            "message_id": result.message_id,
        }))
    else:
        print(f"sent to {target.name} (id={result.message_id})")
    return 0


COMMANDS = {
    "status": cmd_status,
    "contacts": {
        "list": cmd_contacts_list,
        "show": cmd_contacts_show,
        "ping": cmd_ping,
    },
    "channels": {
        None: cmd_channels,  # bare `channels` lists
        "list": cmd_channels,
        "backfill": cmd_channels_backfill,
    },
    "messages": {
        "list": cmd_messages_list,
        "read": cmd_messages_read,
        "send": cmd_messages_send,
    },
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _apply_overrides(args)

    handler = COMMANDS.get(args.cmd)
    if isinstance(handler, dict):
        handler = handler.get(getattr(args, f"{args.cmd}_cmd"))
    if handler is None:
        args.help_parser.print_help()
        return 0

    client = Client()
    try:
        return handler(client, args)
    except (AmbiguousError, NotFoundError, InvalidError) as exc:
        fail(exc)
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
