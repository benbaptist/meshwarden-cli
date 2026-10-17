import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import Mock, patch

import grpc

from meshwarden_cli.cli import main
from meshwarden_cli.client import Client
from meshwarden_cli.pb import meshcored_pb2 as pb
from meshwarden_cli.resolve import AmbiguousError, InvalidError, resolve_message_target


def contact(name="Alice", key="abcdef" * 10 + "abcd", kind=1):
    return pb.Contact(adv_name=name, public_key=key, type=kind)


class MessagesTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock(spec=Client)
        self.client.all_contacts.return_value = [contact(), contact("Room", "123abc" * 10 + "123a", 3)]
        self.client.list_channels.return_value = pb.ListChannelsResponse(
            channels=[pb.ChannelInfo(index=0, name="Public", channel_id=3)]
        )
        self.client.list_messages.return_value = pb.ListMessagesResponse()
        result = pb.SendMessageResponse(ok=True, message_id=42, expected_ack="ack")
        self.client.send_message.return_value = result
        self.client.send_channel_message.return_value = result

    def run_cli(self, *args, stdin=""):
        out, err = io.StringIO(), io.StringIO()
        with patch("meshwarden_cli.cli.Client", return_value=self.client), patch("sys.stdin", io.StringIO(stdin)), redirect_stdout(out), redirect_stderr(err):
            try:
                code = main(args)
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_bare_help_without_connection(self):
        for args in [(), ("contacts",), ("messages",)]:
            with self.subTest(args=args), patch("meshwarden_cli.cli.Client") as constructor:
                with redirect_stdout(io.StringIO()) as out:
                    self.assertEqual(main(args), 0)
                self.assertIn("usage:", out.getvalue())
                constructor.assert_not_called()

    def test_old_syntax_and_missing_arguments_fail(self):
        for args in [("send", "Alice", "hi"), ("send-channel", "0", "hi"), ("messages", "Alice"), ("messages", "read"), ("messages", "send", "Alice")]:
            with self.subTest(args=args):
                self.assertEqual(self.run_cli(*args)[0], 2)

    def test_summary_counts_latest_and_empty_conversations(self):
        self.client.all_contacts.return_value.append(contact("Repeater", kind=2))
        self.client.list_messages.side_effect = [
            pb.ListMessagesResponse(total=9000, messages=[pb.MessageRecord(ts=100)]),
            pb.ListMessagesResponse(total=8, messages=[pb.MessageRecord(ts=200)]),
            pb.ListMessagesResponse(total=0),
        ]
        code, out, err = self.run_cli("--json", "messages", "list")
        self.assertEqual((code, err), (0, ""))
        rows = json.loads(out)["conversations"]
        self.assertEqual([r["type"] for r in rows], ["pm", "channel", "room"])
        self.assertEqual([r["message_count"] for r in rows], [8, 9000, 0])
        self.assertEqual([r["last_activity"] for r in rows], [200, 100, None])
        self.assertEqual(self.client.list_messages.call_count, 3)
        for rpc in self.client.list_messages.call_args_list:
            self.assertEqual(rpc.kwargs["limit"], 1)
        self.assertEqual(self.client.list_messages.call_args_list[0].kwargs["channel_id"], 3)
        self.assertNotIn("channel_idx", self.client.list_messages.call_args_list[0].kwargs)

    def test_named_channel_reads_by_identity_and_shows_name(self):
        self.client.list_messages.return_value = pb.ListMessagesResponse(total=1, messages=[
            pb.MessageRecord(scope="channel", channel_idx=0, channel_id=3, channel_name="Public",
                             channel_source="decrypted", text="Bob: hi"),
        ])
        code, out, err = self.run_cli("messages", "read", "channel:pub")
        self.assertEqual((code, err), (0, ""))
        self.client.list_messages.assert_called_with(peer="", channel_id=3, direction="", limit=50, offset=0)
        self.assertIn("Public", out)
        code, out, _ = self.run_cli("--json", "messages", "read", "channel:pub")
        message = json.loads(out)["messages"][0]
        self.assertEqual((message["channel_id"], message["channel_source"]), (3, "decrypted"))

    def test_channels_bare_lists_and_backfill_passes_secrets(self):
        self.assertEqual(self.run_cli("channels")[0], 0)
        self.client.list_channels.assert_called_once()
        self.client.backfill_message_channels.return_value = pb.BackfillMessageChannelsResponse(
            decrypted=2, inferred=1, unresolved=0
        )
        code, out, err = self.run_cli("--json", "channels", "backfill", "--secret", "Old=" + "ab" * 16)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out), {"decrypted": 2, "inferred": 1, "unresolved": 0})
        self.client.backfill_message_channels.assert_called_with([("Old", bytes([0xAB]) * 16)])
        for bad in ["Old", "=" + "ab" * 16, "Old=abcd", "Old=zz" + "ab" * 15]:
            with self.subTest(secret=bad):
                self.assertEqual(self.run_cli("channels", "backfill", "--secret", bad)[0], 2)

    def test_summary_table(self):
        code, out, _ = self.run_cli("messages", "list")
        self.assertEqual(code, 0)
        for label in ["NAME", "TYPE", "COUNT", "LAST ACTIVITY", "channel:0", "room", "pm"]:
            self.assertIn(label, out)
        self.assertNotIn("\x1b", out)

    def test_numeric_channel_reads_do_not_access_device(self):
        for target in ["0", "channel:7"]:
            with self.subTest(target=target):
                code, out, err = self.run_cli("--json", "messages", "read", target, "--limit", "3", "--offset", "2", "--direction", "out")
                self.assertEqual((code, err), (0, ""))
                self.assertEqual(json.loads(out), {"total": 0, "messages": []})
                self.client.list_messages.assert_called_with(peer="", channel_idx=int(target.split(":")[-1]), direction="out", limit=3, offset=2)
        self.client.list_channels.assert_not_called()
        self.client.all_contacts.assert_not_called()

    def test_contact_selector_does_not_access_device(self):
        self.assertEqual(self.run_cli("messages", "read", "contact:ali")[0], 0)
        self.client.list_channels.assert_not_called()
        self.assertEqual(self.client.list_messages.call_args.kwargs["peer"], contact().public_key[:12])

    def test_send_names_keys_rooms_channels_and_stdin(self):
        for target in ["Alice", "ali", contact().public_key, "ABCDEF", "Room", "channel:pub", "0"]:
            with self.subTest(target=target):
                code, out, err = self.run_cli("--json", "messages", "send", target, "-", stdin="hello\n")
                self.assertEqual((code, err), (0, ""))
                self.assertEqual(json.loads(out)["message_id"], 42)
        self.client.send_message.assert_called_with("123abc123abc", "hello")
        self.client.send_channel_message.assert_called_with(0, "hello")

    def test_collisions_require_explicit_selector(self):
        self.client.list_channels.return_value.channels[0].name = "Alice"
        self.assertEqual(self.run_cli("messages", "send", "Alice", "hi")[0], 1)
        self.client.send_message.assert_not_called()
        self.client.send_channel_message.assert_not_called()
        for target in ["contact:Alice", "channel:Alice"]:
            self.assertEqual(self.run_cli("messages", "send", target, "hi")[0], 0)

    def test_errors_exit_nonzero_and_close_client(self):
        for target in ["missing", "contact:", "channel:", "channel:4294967296"]:
            with self.subTest(target=target):
                code, out, err = self.run_cli("--json", "messages", "send", target, "hi")
                self.assertEqual(code, 1)
                self.assertEqual(out, "")
                self.assertIn("error:", err)
        self.client.close.assert_called()
        self.assertEqual(self.run_cli("messages", "send", "Alice", "")[0], 1)
        self.client.send_message.return_value = pb.SendMessageResponse(ok=False, error="offline")
        self.assertEqual(self.run_cli("messages", "send", "Alice", "hi")[0], 1)

    def test_grpc_error_exit(self):
        error = grpc.RpcError()
        error.code = lambda: grpc.StatusCode.UNAVAILABLE
        error.details = lambda: "offline"
        self.client.list_messages.side_effect = error
        code, out, err = self.run_cli("--json", "messages", "read", "0")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("cannot reach daemon", err)
        self.client.close.assert_called_once()

    def test_invalid_pagination(self):
        for flag, value in [("--limit", "0"), ("--limit", "-1"), ("--offset", "-1"), ("--offset", "4294967296")]:
            self.assertEqual(self.run_cli("messages", "read", "0", flag, value)[0], 2)


class ResolutionTests(unittest.TestCase):
    def test_ambiguity_not_hidden_by_other_namespace(self):
        channels = [pb.ChannelInfo(name="Public A", index=0), pb.ChannelInfo(name="Public B", index=1)]
        with self.assertRaises(AmbiguousError):
            resolve_message_target("Public", [contact("Public")], channels)
        with self.assertRaises(AmbiguousError):
            resolve_message_target("contact:abcdef", [contact(), contact("Bob", "abcdef" + "1" * 58)])

    def test_exact_name_wins_over_substring_and_numeric_contact(self):
        result = resolve_message_target("contact:Alice", [contact(), contact("Alice Other")])
        self.assertEqual(result.name, "Alice")
        self.assertEqual(resolve_message_target("contact:123", [contact("123")]).type, "pm")

    def test_repeater_rejected(self):
        with self.assertRaises(InvalidError):
            resolve_message_target("contact:Repeater", [contact("Repeater", kind=2)])

    def test_contact_pagination_respects_actual_page_size(self):
        client = Client.__new__(Client)
        client.stub = Mock()
        client.stub.ListContacts.side_effect = [
            pb.ListContactsResponse(contacts=[contact()], total=2),
            pb.ListContactsResponse(contacts=[contact("Bob")], total=2),
        ]
        self.assertEqual([c.adv_name for c in client.all_contacts()], ["Alice", "Bob"])
        requests = [c.args[0] for c in client.stub.ListContacts.call_args_list]
        self.assertEqual([r.offset for r in requests], [0, 1])
        self.assertTrue(all(r.limit == 1000 and not r.device_only for r in requests))


if __name__ == "__main__":
    unittest.main()