"""Resolve user-supplied identifiers (names, prefixes, channel names) to
the canonical form the daemon expects (pubkey prefix, channel index).

Resolution rules:
- pubkey: any hex string. Used as a prefix match (e.g. a 1-byte hash like "6a").
- name: substring match against adv_name (case-insensitive).
- channel: integer index, or exact/partial name match (case-insensitive).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Collection, Iterable, NoReturn

from .pb import meshcored_pb2 as pb


class AmbiguousError(Exception):
    """More than one contact matched the query."""


class NotFoundError(Exception):
    """No contact matched the query."""


class InvalidError(Exception):
    """The query was malformed (e.g. bad hex)."""


def _is_hex(s: str) -> bool:
    if not s:
        return False
    return all(c in "0123456789abcdefABCDEF" for c in s)


def key_prefix_bytes(query: str, contact: pb.Contact) -> int:
    """Whole bytes of `contact`'s public key the query spelled out, or 0 if it wasn't a key prefix."""
    if _is_hex(query) and contact.public_key.lower().startswith(query.lower()):
        return len(query) // 2
    return 0


def resolve_contact(
    query: str,
    contacts: Iterable[pb.Contact],
    *,
    types: Collection[int] | None = None,
) -> pb.Contact:
    """Find exactly one contact matching `query`.

    Order of attempts:
      1. exact public_key match
      2. public_key prefix match (if query is hex)
      3. exact adv_name match (case-insensitive)
      4. substring adv_name match (case-insensitive)

    `types` restricts candidates to those contact types (e.g. {2, 3}).
    """
    if not query:
        raise InvalidError("empty contact query")

    candidates = list(contacts)
    if types is not None:
        candidates = [c for c in candidates if c.type in types]

    # 1. exact pubkey
    for c in candidates:
        if c.public_key.lower() == query.lower():
            return c

    # 2. pubkey prefix; a hex query that matches a key never falls through to names
    if _is_hex(query):
        prefix_matches = [c for c in candidates if c.public_key.lower().startswith(query.lower())]
        if len(prefix_matches) == 1:
            return prefix_matches[0]
        if len(prefix_matches) > 1:
            raise AmbiguousError(
                f"pubkey prefix {query!r} matches {len(prefix_matches)} contacts; "
                f"use a longer prefix"
            )

    # 3. exact name (case-insensitive)
    q_lower = query.lower()
    exact = [c for c in candidates if (c.adv_name or "").lower() == q_lower]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise AmbiguousError(
            f"name {query!r} matches {len(exact)} contacts exactly"
        )

    # 4. substring name (case-insensitive)
    sub = [c for c in candidates if q_lower in (c.adv_name or "").lower()]
    if len(sub) == 1:
        return sub[0]
    if len(sub) > 1:
        names = ", ".join(sorted({c.adv_name for c in sub}))
        raise AmbiguousError(
            f"name {query!r} matches {len(sub)} contacts: {names}"
        )

    raise NotFoundError(f"no contact matched {query!r}")


def resolve_channel(query: str, channels: Iterable[pb.ChannelInfo]) -> pb.ChannelInfo:
    """Find a channel by integer index or exact/partial name."""
    if not query:
        raise InvalidError("empty channel query")
    channels = list(channels)

    # integer index
    if query.isdigit():
        idx = int(query)
        for c in channels:
            if c.index == idx:
                return c
        raise NotFoundError(f"no channel with index {idx}")

    # exact name (case-insensitive)
    q_lower = query.lower()
    exact = [c for c in channels if (c.name or "").lower() == q_lower]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise AmbiguousError(f"channel name {query!r} matches {len(exact)} channels")

    # substring name
    sub = [c for c in channels if q_lower in (c.name or "").lower()]
    if len(sub) == 1:
        return sub[0]
    if len(sub) > 1:
        names = ", ".join(f"{c.name}#{c.index}" for c in sub)
        raise AmbiguousError(f"channel name {query!r} matches: {names}")

    raise NotFoundError(f"no channel matched {query!r}")


@dataclass(frozen=True)
class MessageTarget:
    name: str
    type: str
    public_key: str = ""
    channel_idx: int | None = None
    #: daemon channel id; set when resolved from the device's channel list
    channel_id: int | None = None

    @property
    def selector(self) -> str:
        if self.channel_idx is not None:
            return f"channel:{self.channel_idx}"
        return f"contact:{self.public_key}"

    @property
    def peer(self) -> str:
        return self.public_key[:12]

    def history_filter(self) -> dict:
        """ListMessages filter for this conversation.

        A resolved channel is matched by identity, so history survives its
        slot being reused; a bare index matches whatever used that slot.
        """
        if self.channel_id is not None:
            return {"peer": "", "channel_id": self.channel_id}
        return {"peer": self.peer, "channel_idx": self.channel_idx}


def contact_target(contact: pb.Contact) -> MessageTarget:
    if contact.type not in (1, 3):
        raise InvalidError(f"contact {contact.adv_name!r} is not chat or room")
    return MessageTarget(
        contact.adv_name or contact.public_key[:12],
        "room" if contact.type == 3 else "pm",
        public_key=contact.public_key,
    )


def channel_target(channel: pb.ChannelInfo) -> MessageTarget:
    return MessageTarget(
        channel.name or f"#{channel.index}", "channel",
        channel_idx=channel.index, channel_id=channel.channel_id or None,
    )


def split_target(target: str) -> tuple[str, str]:
    kind, sep, query = target.partition(":")
    if not sep:
        kind, query = "", target
    elif kind not in ("contact", "channel"):
        raise InvalidError("use contact:QUERY or channel:QUERY")
    if not query.strip():
        raise InvalidError("empty message target")
    return kind, query


def numeric_channel(query: str) -> int | None:
    if not query.isascii() or not query.isdigit():
        return None
    index = int(query)
    if index > 0xFFFFFFFF:
        raise InvalidError("channel index must fit uint32")
    return index


def resolve_message_target(
    target: str,
    contacts: Iterable[pb.Contact] = (),
    channels: Iterable[pb.ChannelInfo] = (),
) -> MessageTarget:
    """Resolve across both namespaces; never silently choose a collision."""
    kind, query = split_target(target)
    if kind != "contact":
        index = numeric_channel(query)
        if index is not None:
            return MessageTarget(f"#{index}", "channel", channel_idx=index)
    matches = []
    try:
        if kind != "channel":
            try:
                contact = resolve_contact(query, contacts)
                matches.append(contact)
            except NotFoundError:
                pass
        if kind != "contact":
            try:
                matches.append(resolve_channel(query, channels))
            except NotFoundError:
                pass
    except AmbiguousError as exc:
        raise AmbiguousError(f"{exc}; use contact: or channel: and a more specific target") from exc
    if not matches:
        raise NotFoundError(f"no message target matched {target!r}")
    if len(matches) > 1:
        raise AmbiguousError(f"{target!r} matches a contact and channel; use contact: or channel:")
    match = matches[0]
    return contact_target(match) if isinstance(match, pb.Contact) else channel_target(match)


def fail(e: Exception) -> NoReturn:
    """Print a resolution error and exit non-zero."""
    print(f"error: {e}", file=sys.stderr)
    sys.exit(1)
