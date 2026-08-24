"""The printer transport: which addresses are dialled, and what goes on the wire.

The socket tests bind a real listener on loopback rather than patching
``socket``. What is being checked is framing and failure classification, and a
mock of the module under test's own dependency would assert the shape of the
call instead of the bytes that arrive.
"""

from __future__ import annotations

import socket
import threading

import pytest

from cadless import printing


def _listener():
    """A bound, listening loopback socket and its port."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    return sock, sock.getsockname()[1]


class TestAddressRules:
    @pytest.mark.parametrize(
        "address",
        ["192.168.0.42", "10.1.2.3", "172.16.5.5", "127.0.0.1", "169.254.10.10", "[fd00::1]"],
    )
    def test_a_local_literal_is_accepted(self, address):
        printing.refuse_public_literal(address)

    @pytest.mark.parametrize("address", ["8.8.8.8", "1.1.1.1", "93.184.216.34", "[2001:4860::1]"])
    def test_a_public_literal_is_refused(self, address):
        with pytest.raises(printing.AddressRefused, match="local network"):
            printing.refuse_public_literal(address)

    def test_an_empty_address_is_refused(self):
        with pytest.raises(printing.AddressRefused):
            printing.refuse_public_literal("  ")

    def test_a_name_is_left_for_dial_time(self):
        """Saving must not wait on DNS, so a name passes the cheap check."""
        printing.refuse_public_literal("my-printer.local")

    def test_resolve_returns_the_literal_to_dial(self):
        assert printing.resolve_target("192.168.7.7") == "192.168.7.7"

    def test_resolve_refuses_a_public_literal(self):
        with pytest.raises(printing.AddressRefused, match="local network"):
            printing.resolve_target("8.8.8.8")

    def test_resolve_refuses_a_name_that_answers_publicly(self, monkeypatch):
        """A name is only as safe as what it resolves to, and that is checked."""
        monkeypatch.setattr(
            printing.socket,
            "getaddrinfo",
            lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 9100))],
        )
        with pytest.raises(printing.AddressRefused, match="public address"):
            printing.resolve_target("looks-local.example")

    def test_one_public_answer_refuses_the_whole_name(self, monkeypatch):
        """Fails closed: a name that sometimes points outward is the case guarded."""
        monkeypatch.setattr(
            printing.socket,
            "getaddrinfo",
            lambda *a, **k: [
                (2, 1, 6, "", ("192.168.1.9", 9100)),
                (2, 1, 6, "", ("93.184.216.34", 9100)),
            ],
        )
        with pytest.raises(printing.AddressRefused):
            printing.resolve_target("split.example")


class TestJobFraming:
    def test_the_envelope_opens_and_closes_with_uel(self):
        framed = printing.pjl_job(b"G28\n", name="part")
        assert framed.startswith(printing.UEL)
        assert framed.endswith(printing.UEL)
        assert b"@PJL JOB NAME=" in framed
        assert b"@PJL EOJ" in framed

    def test_the_body_is_carried_untouched(self):
        body = b"G28\nG1 X10 Y10 F1200\n"
        assert body in printing.pjl_job(body, name="part")

    def test_a_quote_in_the_name_cannot_close_the_value(self):
        """PJL has no escape inside a quoted value, so the quote must not survive."""
        framed = printing.pjl_job(b"G28\n", name='evil" @PJL FSDELETE NAME="x')
        header = framed.split(b"\r\n", 1)[0]
        assert header.count(b'"') == 2

    def test_an_empty_name_falls_back_rather_than_emitting_nothing(self):
        framed = printing.pjl_job(b"G28\n", name="///")
        assert b'NAME="cadless"' in framed


class TestSending:
    def test_nothing_to_print_is_refused_before_dialling(self):
        outcome = printing.send_gcode("192.168.0.1", b"")
        assert not outcome.ok
        assert outcome.reason == "empty"

    def test_a_public_address_is_refused_before_dialling(self):
        outcome = printing.send_gcode("8.8.8.8", b"G28\n")
        assert not outcome.ok
        assert outcome.reason == "address"

    def test_the_framed_job_is_what_arrives(self, monkeypatch):
        sock, port = _listener()
        monkeypatch.setattr(printing, "PRINT_PORT", port)
        received: list[bytes] = []

        def accept():
            conn, _ = sock.accept()
            with conn:
                chunks = []
                while True:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    chunks.append(chunk)
            received.append(b"".join(chunks))

        thread = threading.Thread(target=accept)
        thread.start()
        try:
            outcome = printing.send_gcode("127.0.0.1", b"G28\n", name="part")
        finally:
            thread.join(timeout=5)
            sock.close()

        assert outcome.ok, outcome.detail
        assert received and received[0] == printing.pjl_job(b"G28\n", name="part")
        assert outcome.bytes_sent == len(received[0])

    def test_a_closed_port_is_reported_as_refused(self, monkeypatch):
        sock, port = _listener()
        sock.close()  # nothing is listening on it now
        monkeypatch.setattr(printing, "PRINT_PORT", port)
        outcome = printing.send_gcode("127.0.0.1", b"G28\n")
        assert not outcome.ok
        assert outcome.reason in {"refused", "unreachable"}

    def test_probe_sends_no_job(self, monkeypatch):
        sock, port = _listener()
        monkeypatch.setattr(printing, "PRINT_PORT", port)
        received: list[bytes] = []

        def accept():
            conn, _ = sock.accept()
            with conn:
                conn.settimeout(2)
                try:
                    received.append(conn.recv(4096))
                except OSError:
                    received.append(b"")

        thread = threading.Thread(target=accept)
        thread.start()
        try:
            outcome = printing.probe("127.0.0.1")
        finally:
            thread.join(timeout=5)
            sock.close()

        assert outcome.ok, outcome.detail
        assert received == [b""]


class TestStatusParsing:
    #: The shape the printer's web UI receives: a JavaScript call, not JSON.
    REPLY = "set_status(3600, 10005, 42, 80, 0, ff, 00, 00, 1, 60, 205, part.gcode);"

    def test_the_fields_the_ui_needs_are_read(self):
        fields = printing.parse_status(self.REPLY)
        assert fields["estimate_seconds"] == 3600
        assert fields["state_code"] == 10005
        assert fields["percent"] == 42
        assert fields["bed_temp"] == 60
        assert fields["nozzle_temp"] == 205
        assert fields["filename"] == "part.gcode"

    def test_a_printing_state_is_recognised(self):
        assert printing.parse_status(self.REPLY)["printing"] is True
        assert printing.parse_status(self.REPLY)["idle"] is False

    def test_an_idle_state_is_recognised(self):
        idle = self.REPLY.replace("10005", "10001")
        assert printing.parse_status(idle)["idle"] is True

    def test_something_that_is_not_the_reply_yields_nothing(self):
        assert printing.parse_status("<html>404</html>") == {}

    def test_a_truncated_reply_does_not_raise(self):
        assert printing.parse_status("set_status(3600);")["state_code"] is None
