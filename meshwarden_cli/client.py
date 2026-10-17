"""Thin gRPC client wrapper for meshwarden-daemon."""

from __future__ import annotations

import os
import sys
from typing import Any

import grpc

from .pb import meshcored_pb2 as pb
from .pb import meshcored_pb2_grpc


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 50051


def _env(name: str, default: str) -> str:
    return os.environ.get(name) or default


def dial() -> grpc.Channel:
    host = _env("MESHWARDEN_HOST", DEFAULT_HOST)
    port = _env("MESHWARDEN_PORT", str(DEFAULT_PORT))
    target = f"{host}:{port}"
    # Insecure by default; the daemon binds insecure. TLS can be added later
    # by setting MESHWARDEN_TLS=1 and pointing at CA/cert/key env vars.
    if _env("MESHWARDEN_TLS", "") in ("1", "true", "yes"):
        creds = grpc.ssl_channel_credentials(
            root_certificates=os.environ.get("MESHWARDEN_CA", "").encode() or None,
            private_key=os.environ.get("MESHWARDEN_KEY", "").encode() or None,
            certificate_chain=os.environ.get("MESHWARDEN_CERT", "").encode() or None,
        )
        return grpc.secure_channel(target, creds)
    return grpc.insecure_channel(target)


class Client:
    """Convenience wrapper. One stub, one channel."""

    def __init__(self, channel: grpc.Channel | None = None):
        self.channel = channel or dial()
        self.stub = meshcored_pb2_grpc.MeshCoreDaemonStub(self.channel)

    def close(self) -> None:
        self.channel.close()

    # ---- status ----------------------------------------------------------

    def get_status(self) -> pb.StatusResponse:
        return self.stub.GetStatus(pb.Empty())

    # ---- contacts --------------------------------------------------------

    def list_contacts(
        self,
        query: str = "",
        device_only: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> pb.ListContactsResponse:
        return self.stub.ListContacts(
            pb.ListContactsRequest(
                query=query,
                device_only=device_only,
                limit=limit,
                offset=offset,
            )
        )

    def all_contacts(self) -> list[pb.Contact]:
        """Read the complete stored directory, respecting server page limits."""
        contacts = []
        while True:
            page = self.list_contacts(limit=1000, offset=len(contacts))
            contacts.extend(page.contacts)
            if not page.contacts or len(contacts) >= page.total:
                return contacts

    # ---- channels --------------------------------------------------------

    def ping(self, public_key: str, hash_size: int = 1) -> pb.PingResponse:
        return self.stub.Ping(
            pb.PingRequest(public_key=public_key, hash_size=hash_size), timeout=5.0
        )

    def list_channels(self) -> pb.ListChannelsResponse:
        return self.stub.ListChannels(pb.Empty())

    def backfill_message_channels(
        self, extra_channels: list[tuple[str, bytes]] = ()
    ) -> pb.BackfillMessageChannelsResponse:
        return self.stub.BackfillMessageChannels(pb.BackfillMessageChannelsRequest(
            extra_channels=[pb.ChannelSecret(name=n, secret=s) for n, s in extra_channels]
        ))

    # ---- messages --------------------------------------------------------

    def list_messages(
        self,
        peer: str = "",
        channel_idx: int | None = None,
        channel_id: int | None = None,
        direction: str = "",
        limit: int = 100,
        offset: int = 0,
    ) -> pb.ListMessagesResponse:
        req = pb.ListMessagesRequest(
            peer=peer,
            direction=direction,
            limit=limit,
            offset=offset,
        )
        if channel_idx is not None:
            req.channel_idx = channel_idx
        if channel_id is not None:
            req.channel_id = channel_id
        return self.stub.ListMessages(req)

    def send_message(self, destination: str, text: str) -> pb.SendMessageResponse:
        return self.stub.SendMessage(
            pb.SendMessageRequest(destination=destination, text=text)
        )

    def send_channel_message(
        self, channel_idx: int, text: str
    ) -> pb.SendMessageResponse:
        return self.stub.SendChannelMessage(
            pb.SendChannelMessageRequest(channel_idx=channel_idx, text=text)
        )


def call(fn):
    """Run a gRPC call, translate common errors into clean CLI exits."""
    try:
        return fn()
    except grpc.RpcError as exc:
        code = exc.code()
        details = exc.details() or ""
        if code == grpc.StatusCode.UNAVAILABLE:
            print(
                f"error: cannot reach daemon at "
                f"{_env('MESHWARDEN_HOST', DEFAULT_HOST)}:"
                f"{_env('MESHWARDEN_PORT', str(DEFAULT_PORT))} "
                f"({details or 'connection refused'})",
                file=sys.stderr,
            )
        elif code == grpc.StatusCode.DEADLINE_EXCEEDED:
            print(f"error: daemon timed out ({details})", file=sys.stderr)
        elif code == grpc.StatusCode.INVALID_ARGUMENT:
            print(f"error: bad request ({details})", file=sys.stderr)
        else:
            print(f"error: gRPC {code.name}: {details}", file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        sys.exit(130)
