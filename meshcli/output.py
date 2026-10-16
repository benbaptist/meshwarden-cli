"""Output formatting: plain text (default) or JSON (--json).

Plain text is intentionally boring: fixed-width columns, no colors, no
progress bars. The goal is something you can grep, awk, and pipe.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

# MeshCore contact types (from the companion protocol).
CONTACT_TYPE_NAMES = {
    0: "invalid",
    1: "chat",
    2: "repeater",
    3: "room",
}


def fmt_ts(ts: int | None) -> str:
    """Unix seconds -> ISO-8601 UTC. 0/None -> '-'."""
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fmt_age(ts: int | None, now: int | None = None) -> str:
    """Human-friendly '3h ago' style. 0/None -> '-'."""
    if not ts:
        return "-"
    if now is None:
        now = int(datetime.now(tz=timezone.utc).timestamp())
    delta = now - ts
    if delta < 0:
        return "future"
    if delta < 60:
        return f"{delta}s"
    if delta < 3600:
        return f"{delta // 60}m"
    if delta < 86400:
        return f"{delta // 3600}h"
    return f"{delta // 86400}d"


@dataclass
class Printer:
    as_json: bool = False
    stream = sys.stdout

    def emit(self, obj: Any) -> None:
        if self.as_json:
            print(json.dumps(obj, default=str), file=self.stream)
        else:
            self._emit_pretty(obj)

    def _emit_pretty(self, obj: Any) -> None:
        raise NotImplementedError


def contact_type_name(t: int) -> str:
    return CONTACT_TYPE_NAMES.get(t, f"type{t}")


def contact_to_dict(c) -> dict[str, Any]:
    return {
        "public_key": c.public_key,
        "adv_name": c.adv_name,
        "type": c.type,
        "type_name": contact_type_name(c.type),
        "flags": c.flags,
        "out_path_len": c.out_path_len,
        "out_path": c.out_path,
        "adv_lat": c.adv_lat,
        "adv_lon": c.adv_lon,
        "last_advert": c.last_advert,
        "last_seen": c.last_seen,
        "on_device": c.on_device,
    }


def channel_to_dict(ch) -> dict[str, Any]:
    return {
        "index": ch.index,
        "name": ch.name,
        "flags": ch.flags,
        "channel_id": ch.channel_id,
    }


def message_to_dict(m) -> dict[str, Any]:
    return {
        "id": m.id,
        "ts": m.ts,
        "direction": m.direction,
        "scope": m.scope,
        "peer": m.peer,
        "channel_idx": m.channel_idx,
        "channel_id": m.channel_id if m.HasField("channel_id") else None,
        "channel_name": m.channel_name,
        "channel_source": m.channel_source,
        "text": m.text,
        "snr": m.snr,
        "acked": m.acked,
        "origin": m.origin,
    }


def status_to_dict(s) -> dict[str, Any]:
    return {
        "device_connected": s.device_connected,
        "transport": s.transport,
        "device_name": s.device_name,
        "public_key": s.public_key,
        "connected_since": s.connected_since,
        "proxy_clients": s.proxy_clients,
        "packets_logged": s.packets_logged,
        "messages_stored": s.messages_stored,
        "contacts_stored": s.contacts_stored,
        "daemon_version": s.daemon_version,
    }


# ---- pretty printers ---------------------------------------------------


def print_contacts(contacts: Iterable, as_json: bool, total: int | None = None) -> None:
    rows = list(contacts)
    if as_json:
        out = {
            "total": total if total is not None else len(rows),
            "contacts": [contact_to_dict(c) for c in rows],
        }
        print(json.dumps(out, default=str))
        return

    if total is not None and total != len(rows):
        print(f"# showing {len(rows)} of {total}")
    print(
        f"{'NAME':<24} {'TYPE':<10} {'PUBKEY':<14} {'LAST SEEN':<10} "
        f"{'PATH':<5} {'DEVICE':<6}"
    )
    for c in rows:
        name = c.adv_name or "(no name)"
        if len(name) > 24:
            name = name[:23] + "\u2026"
        print(
            f"{name:<24} {contact_type_name(c.type):<10} "
            f"{c.public_key[:12]:<14} {fmt_age(c.last_seen):<10} "
            f"{c.out_path_len:<5} {'yes' if c.on_device else 'no':<6}"
        )


def print_contact(c, as_json: bool) -> None:
    if as_json:
        print(json.dumps(contact_to_dict(c), default=str))
        return
    print(f"name:           {c.adv_name or '(no name)'}")
    print(f"public_key:     {c.public_key}")
    print(f"type:           {contact_type_name(c.type)} ({c.type})")
    print(f"flags:          {c.flags}")
    print(f"out_path_len:   {c.out_path_len}")
    print(f"out_path:       {c.out_path or '-'}")
    print(f"adv_lat:        {c.adv_lat}")
    print(f"adv_lon:        {c.adv_lon}")
    print(f"last_advert:    {fmt_ts(c.last_advert)}")
    print(f"last_seen:      {fmt_ts(c.last_seen)}")
    print(f"on_device:      {'yes' if c.on_device else 'no'}")


def print_channels(channels: Iterable, as_json: bool) -> None:
    rows = list(channels)
    if as_json:
        print(json.dumps({"channels": [channel_to_dict(c) for c in rows]}, default=str))
        return
    print(f"{'IDX':<4} {'NAME':<32} {'FLAGS':<6}")
    for c in rows:
        name = c.name or "(unnamed)"
        if len(name) > 32:
            name = name[:31] + "\u2026"
        print(f"{c.index:<4} {name:<32} {c.flags:<6}")


def print_conversations(conversations: list[dict[str, Any]], as_json: bool) -> None:
    if as_json:
        print(json.dumps({"total": len(conversations), "conversations": conversations}))
        return
    print(f"{'NAME':<24} {'TYPE':<8} {'COUNT':>8} {'LAST ACTIVITY':<20} TARGET")
    for row in conversations:
        name = row['name'].replace("\n", " ")
        print(
            f"{name:<24} {row['type']:<8} {row['message_count']:>8} "
            f"{fmt_ts(row['last_activity']):<20} {row['target']}"
        )


def print_messages(messages: Iterable, as_json: bool, total: int | None = None) -> None:
    rows = list(messages)
    if as_json:
        out = {
            "total": total if total is not None else len(rows),
            "messages": [message_to_dict(m) for m in rows],
        }
        print(json.dumps(out, default=str))
        return

    if total is not None and total != len(rows):
        print(f"# showing {len(rows)} of {total}")
    print(f"{'WHEN':<20} {'DIR':<3} {'PEER/CH':<14} {'TEXT'}")
    for m in rows:
        when = fmt_ts(m.ts)
        direction = "<<" if m.direction == "in" else ">>"
        if m.scope == "channel":
            target = m.channel_name or f"#{m.channel_idx}"
        else:
            target = m.peer or "-"
        text = m.text.replace("\n", " ")
        print(f"{when:<20} {direction:<3} {target:<14} {text}")


def print_status(s, as_json: bool) -> None:
    if as_json:
        print(json.dumps(status_to_dict(s), default=str))
        return
    connected = "connected" if s.device_connected else "disconnected"
    print(f"daemon:         {s.daemon_version}")
    print(f"device:         {connected} via {s.transport or '-'}")
    print(f"device_name:    {s.device_name or '-'}")
    print(f"public_key:     {s.public_key or '-'}")
    print(f"connected_since:{fmt_ts(s.connected_since)}")
    print(f"proxy_clients:  {s.proxy_clients}")
    print(f"packets_logged: {s.packets_logged}")
    print(f"messages_stored:{s.messages_stored}")
    print(f"contacts_stored:{s.contacts_stored}")
