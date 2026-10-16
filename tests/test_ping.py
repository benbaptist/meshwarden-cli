import io
import json
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import Mock, patch

import grpc

from meshcli.cli import main
from meshcli.client import Client
from meshcli.pb import meshcored_pb2 as pb


class PingTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock(spec=Client)
        self.contact = pb.Contact(adv_name="Repeater", public_key="abcdef01" * 8, type=2)
        self.client.all_contacts.return_value = [self.contact]
        self.client.ping.return_value = pb.PingResponse(elapsed_ms=12.5, snr=[2, -1])

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with patch("meshcli.cli.Client", return_value=self.client), redirect_stdout(out), redirect_stderr(err):
            try:
                code = main(args)
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_default_widths_and_output(self):
        self.assertEqual(self.run_cli("ping", "Repeater")[0], 2)  # no root alias
        code, out, err = self.run_cli("contacts", "ping", "Repeater")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("12.5 ms", out)
        self.client.ping.assert_called_with(self.contact.public_key, 1)
        self.contact.type = 3
        for width in (1, 2, 4):
            code, out, err = self.run_cli("--json", "contacts", "ping", "abcdef", "--hash-size", str(width))
            self.assertEqual((code, err), (0, ""))
            self.assertEqual(json.loads(out)["hash_size"], width)
            self.client.ping.assert_called_with(self.contact.public_key, width)

    def test_default_width_follows_typed_prefix(self):
        # Largest trace width (1/2/4) not exceeding the bytes typed; names default to 1.
        for query, width in (("a", 1), ("ab", 1), ("abc", 1), ("abcd", 2), ("abcdef", 2),
                             ("abcdef0", 2), ("abcdef01", 4), (self.contact.public_key, 4),
                             ("Repeater", 1)):
            with self.subTest(query=query):
                self.assertEqual(self.run_cli("contacts", "ping", query)[0], 0)
                self.client.ping.assert_called_with(self.contact.public_key, width)
        self.run_cli("contacts", "ping", "abcdef01", "--hash-size", "1")
        self.client.ping.assert_called_with(self.contact.public_key, 1)

    def test_bad_width_chat_unknown_ambiguous_do_not_transmit(self):
        self.assertEqual(self.run_cli("contacts", "ping", "Repeater", "--hash-size", "3")[0], 2)
        self.assertEqual(self.run_cli("contacts", "ping", "missing")[0], 1)
        self.contact.type = 1
        self.assertEqual(self.run_cli("contacts", "ping", "Repeater")[0], 1)
        self.contact.type = 2
        self.client.all_contacts.return_value.append(pb.Contact(adv_name="Repeater", public_key="a" * 64, type=3))
        self.assertEqual(self.run_cli("contacts", "ping", "Repeater")[0], 1)
        self.client.ping.assert_not_called()

    def test_short_hex_prefix_and_pingable_types_only(self):
        room = pb.Contact(adv_name="SW Michigan Chat", public_key="6a7b8fc9" * 8, type=3)
        chat = pb.Contact(adv_name="WT-AAAC986A", public_key="aaac986a" * 8, type=1)
        decoy = pb.Contact(adv_name="WB-450E9457", public_key="450e9457" * 8, type=1)
        self.client.all_contacts.return_value = [self.contact, room, chat, decoy]
        for query, width in (("6a", 1), ("6a7b", 2)):
            self.assertEqual(self.run_cli("contacts", "ping", query)[0], 0)
            self.client.ping.assert_called_with(room.public_key, width)
        self.client.ping.reset_mock()
        # Chat contacts are never candidates, even by name substring.
        self.assertEqual(self.run_cli("contacts", "ping", "50")[0], 1)
        self.client.ping.assert_not_called()

    def test_deadline_failure_exit(self):
        # A concrete exception is needed by the CLI's except grpc.RpcError.
        class Deadline(grpc.RpcError):
            def code(self):
                return grpc.StatusCode.DEADLINE_EXCEEDED
            def details(self):
                return "no matching trace within five seconds"
        self.client.ping.side_effect = Deadline()
        code, out, err = self.run_cli("contacts", "ping", "Repeater")
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("five seconds", err)

    def test_client_typed_rpc_deadline(self):
        client = Client.__new__(Client)
        client.stub = Mock()
        client.ping(self.contact.public_key, 4)
        req = client.stub.Ping.call_args.args[0]
        self.assertEqual(req.hash_size, 4)
        self.assertEqual(req.public_key, self.contact.public_key)
        self.assertEqual(client.stub.Ping.call_args.kwargs, {"timeout": 5.0})