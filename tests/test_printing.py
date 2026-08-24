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
from pydantic import ValidationError

from cadless import printing
from cadless.config import Settings


def _listener():
    """A bound, listening loopback socket and its port."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    return sock, sock.getsockname()[1]


def _collect(sock, received: list[bytes]):
    """Accept one connection and read it to EOF."""

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

    return accept


def _job(tmp_path, body: bytes = b"G28\n"):
    path = tmp_path / "print.gcode"
    path.write_bytes(body)
    return str(path)


class TestModes:
    """Which actions a deployment offers, and why it is asked this way.

    The question a deployment is really answering is whether it can reach the
    device. It cannot be derived from where the process is running -- the same
    image, compose file and ports serve a laptop and a server -- so it is asked
    directly: is there an address to send to.
    """

    @pytest.fixture(autouse=True)
    def default_mode(self, monkeypatch):
        monkeypatch.setattr(printing.settings, "printing", "auto")

    def test_a_machine_with_a_printer_can_do_both(self):
        allowed = printing.actions(slicer_available=True, address_configured=True)
        assert (allowed.send, allowed.download) == (True, True)

    def test_a_deployment_with_no_address_can_still_hand_the_file_over(self):
        """This is every visitor to a build in a datacentre."""
        allowed = printing.actions(slicer_available=True, address_configured=False)
        assert (allowed.send, allowed.download) == (False, True)

    def test_no_slicer_means_neither(self):
        allowed = printing.actions(slicer_available=False, address_configured=True)
        assert (allowed.send, allowed.download) == (False, False)

    def test_download_mode_refuses_to_send_even_with_an_address(self, monkeypatch):
        monkeypatch.setattr(printing.settings, "printing", "download")
        allowed = printing.actions(slicer_available=True, address_configured=True)
        assert (allowed.send, allowed.download) == (False, True)

    def test_off_removes_both(self, monkeypatch):
        monkeypatch.setattr(printing.settings, "printing", "off")
        allowed = printing.actions(slicer_available=True, address_configured=True)
        assert (allowed.send, allowed.download) == (False, False)

    def test_a_hosted_build_does_not_send_even_with_an_address_on_disk(self, monkeypatch):
        """Refusing a settings *write* does not remove an address already saved.

        `require_identity` is a launch decision, so the same data directory can
        be started locally, have a printer saved, and then be started hosted.
        The address is then a device on whoever ran it locally's network, and
        the people using the hosted build are not on it.
        """
        monkeypatch.setattr(printing.settings, "require_identity", True)
        allowed = printing.actions(slicer_available=True, address_configured=True)
        assert (allowed.send, allowed.download) == (False, True)

    @pytest.mark.parametrize("configured", ["off", "OFF", " off ", "Download", " AUTO"])
    def test_a_mode_is_case_folded_and_trimmed_on_the_way_in(self, configured):
        """Checked through `Settings`, which is where the normalising happens."""
        assert Settings(printing=configured).printing == configured.strip().lower()

    @pytest.mark.parametrize("configured", ["false", "0", "no", "none", "disabled", "nonsense"])
    def test_a_value_that_is_not_a_mode_stops_the_process_starting(self, configured):
        """The four an operator is most likely to write to mean "off".

        A tolerant reading turns each of them into the most capable mode, which
        is the failure `user_settings._env_flag` already refuses by name: a
        boundary that opens when someone writes `=false` is worse than no
        boundary, because it is believed to be closed. Raising is safe because
        `Settings` is built at import, so this is a refusal to start rather than
        an error on a request.
        """
        with pytest.raises(ValidationError):
            Settings(printing=configured)

    def test_an_empty_value_reads_as_unset(self):
        """Which is how a compose file spells a default: `${CADLESS_PRINTING:-}`.

        Different from `false`, and treated differently: nobody writes an empty
        string to mean off.
        """
        assert Settings(printing="").printing == printing.MODE_AUTO

    def test_a_value_that_bypassed_validation_fails_closed(self, monkeypatch):
        """The singleton is mutable and assignment is not validated.

        The difference between the fallback and `auto` is whether this process
        opens connections, and an unreadable value is not a reason to.
        """
        monkeypatch.setattr(printing.settings, "printing", 5)
        assert printing.mode() == printing.MODE_DOWNLOAD

    def test_the_named_modes_are_the_configured_ones(self):
        """Two places spell them; a drift between them would be silent."""
        assert set(printing.MODES) == {
            printing.MODE_AUTO,
            printing.MODE_DOWNLOAD,
            printing.MODE_OFF,
        }


class TestAddressRules:
    @pytest.mark.parametrize(
        "address",
        [
            "192.168.0.42",
            "10.1.2.3",
            "172.16.5.5",
            "127.0.0.1",
            "169.254.10.10",
            "[fd00::1]",
            "fe80::1",
        ],
    )
    def test_a_local_literal_is_accepted(self, address):
        printing.refuse_public_literal(address)

    @pytest.mark.parametrize(
        "address",
        [
            "8.8.8.8",
            "1.1.1.1",
            "93.184.216.34",
            "172.32.0.1",  # just outside 172.16/12
            "[2001:4860::1]",
        ],
    )
    def test_a_public_literal_is_refused(self, address):
        with pytest.raises(printing.AddressRefused, match="local network"):
            printing.refuse_public_literal(address)

    @pytest.mark.parametrize(
        ("address", "carries"),
        [
            ("::ffff:8.8.8.8", "IPv4 written as IPv6"),
            ("2002:0808:0808::1", "6to4 wrapping the public 8.8.8.8"),
            ("2001:0000:4136:e378:8000:63bf:3fff:fdd2", "Teredo, whose endpoints are public"),
            ("64:ff9b:1::8.8.8.8", "the local-use NAT64 prefix, translating to 8.8.8.8"),
            ("100::8.8.8.8", "the discard prefix"),
            ("2001:db8::1", "documentation space"),
        ],
    )
    def test_an_ipv6_that_is_not_a_lan_address_is_refused(self, address, carries):
        """Every one of these answers ``is_private`` True except the first.

        Which is the point of listing what is allowed rather than what is not:
        ``is_private`` means "not globally reachable" to IANA, and several of
        those prefixes carry an IPv4 address straight out to the internet.
        Measured on CPython 3.13: ``ip_address("2002:0808:0808::1").is_private``
        is True while its ``sixtofour`` is 8.8.8.8.
        """
        with pytest.raises(printing.AddressRefused, match="local network"):
            printing.refuse_public_literal(address)
        assert carries  # names the case in the failure output

    @pytest.mark.parametrize("address", ["::ffff:192.168.1.1", "2002:c0a8:0101::1"])
    def test_a_local_ipv4_carried_inside_ipv6_is_still_accepted(self, address):
        """Unwrapping must not refuse a printer reached through one of these."""
        printing.refuse_public_literal(address)

    @pytest.mark.parametrize(
        "address",
        ["169.254.169.254", "[fd00:ec2::254]", "::ffff:169.254.169.254"],
    )
    def test_the_cloud_metadata_address_is_refused(self, address):
        """It sits inside an allowed range and is never a printer.

        The link-local range is allowed for a device that assigned itself an
        address over a direct connection. On the cloud hosts this same image
        runs on, that range also holds the instance metadata service — the same
        number on AWS, GCP and Azure — so it is excluded by address rather than
        by dropping a range that has a legitimate use.
        """
        with pytest.raises(printing.AddressRefused):
            printing.refuse_public_literal(address)

    def test_the_rest_of_link_local_still_works(self):
        """Excluding one address must not cost the case the range is for."""
        printing.refuse_public_literal("169.254.10.10")

    @pytest.mark.parametrize("address", ["0.0.0.0", "::", "[::]"])
    def test_the_unspecified_address_is_refused(self, address):
        with pytest.raises(printing.AddressRefused):
            printing.refuse_public_literal(address)

    def test_an_empty_address_is_refused(self):
        with pytest.raises(printing.AddressRefused):
            printing.refuse_public_literal("  ")

    def test_an_address_with_a_port_is_refused_at_save_time(self):
        """Otherwise it saves happily and fails later as "does not resolve"."""
        with pytest.raises(printing.AddressRefused, match="port"):
            printing.refuse_public_literal("192.168.1.5:9100")

    def test_a_name_is_left_for_dial_time(self):
        """Saving must not wait on DNS, so a name passes the cheap check."""
        printing.refuse_public_literal("my-printer.local")

    def test_resolve_returns_the_literal_to_dial(self):
        assert printing.resolve_target("192.168.7.7") == "192.168.7.7"

    def test_resolve_refuses_a_public_literal(self):
        with pytest.raises(printing.AddressRefused, match="local network"):
            printing.resolve_target("8.8.8.8")

    def test_a_name_that_cannot_be_encoded_is_refused_not_raised(self):
        """`getaddrinfo` raises `UnicodeError`, which is not an `OSError`.

        A label over 63 characters fails IDNA encoding before any lookup
        happens. Catching only `OSError` let that escape all three entry points,
        and since the cheap save-time check waves any non-literal through as a
        name, one saved address turned every later printer call into a 500.
        """
        too_long = "a" * 64 + ".example"
        with pytest.raises(printing.AddressRefused):
            printing.resolve_target(too_long)

    @pytest.mark.parametrize("entry", ["probe", "fetch_status"])
    def test_no_entry_point_raises_on_an_unencodable_name(self, entry):
        outcome = getattr(printing, entry)("a" * 64 + ".example")
        assert outcome.ok is False

    def test_send_does_not_raise_on_an_unencodable_name(self, tmp_path):
        outcome = printing.send_gcode("a" * 64 + ".example", _job(tmp_path))
        assert outcome.ok is False
        assert outcome.reason == "address"

    def test_resolve_refuses_a_name_that_answers_publicly(self, monkeypatch):
        """A name is only as safe as what it resolves to, and that is checked."""
        monkeypatch.setattr(
            printing.socket,
            "getaddrinfo",
            lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 9100))],
        )
        with pytest.raises(printing.AddressRefused, match="not on a local network"):
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

    def test_a_newline_in_the_name_cannot_start_a_command(self):
        framed = printing.pjl_header('a\r\n@PJL FSDELETE NAME="1:/x"')
        assert framed.count(b"\r\n") == 1
        assert b"FSDELETE" in framed  # survives as text inside the quoted name
        assert b"\r\n@PJL FSDELETE" not in framed

    def test_an_empty_name_falls_back_rather_than_emitting_nothing(self):
        assert b'NAME="cadless"' in printing.pjl_header("///")

    def test_a_body_carrying_an_escape_is_refused(self):
        """An escape is the first byte of the sequence that ends a job.

        A body able to spell it could close the envelope early and have what
        follows read as printer commands rather than as movement.
        """
        smuggled = b"G28\n" + printing.UEL + b'@PJL FSDELETE NAME="1:/x"\r\n'
        with pytest.raises(ValueError, match="escape byte"):
            printing.pjl_job(smuggled, name="part")

    def test_an_escape_is_found_wherever_it_sits_in_the_file(self, tmp_path):
        """Chunked, so a byte on a chunk boundary is not missed."""
        for offset in (0, printing.CHUNK_BYTES - 1, printing.CHUNK_BYTES, printing.CHUNK_BYTES + 1):
            path = tmp_path / f"j{offset}.gcode"
            path.write_bytes(b"G" * offset + bytes([printing.ESC]) + b"G" * 10)
            assert printing.contains_escape(str(path)) is True

    def test_a_clean_file_carries_no_escape(self, tmp_path):
        path = tmp_path / "clean.gcode"
        path.write_bytes(b"G1 X1\n" * 50_000)
        assert printing.contains_escape(str(path)) is False


class TestSending:
    def test_nothing_to_print_is_refused_before_dialling(self, tmp_path):
        outcome = printing.send_gcode("192.168.0.1", _job(tmp_path, b""))
        assert not outcome.ok
        assert outcome.reason == "empty"

    def test_a_missing_file_is_reported_as_such(self, tmp_path):
        outcome = printing.send_gcode("192.168.0.1", str(tmp_path / "gone.gcode"))
        assert not outcome.ok
        assert outcome.reason == "missing"

    def test_a_public_address_is_refused_before_dialling(self, tmp_path):
        outcome = printing.send_gcode("8.8.8.8", _job(tmp_path))
        assert not outcome.ok
        assert outcome.reason == "address"

    def test_a_smuggled_body_never_reaches_the_socket(self, tmp_path, monkeypatch):
        """The refusal has to happen before anything is dialled, not after."""

        def refuse(*_a, **_k):
            raise AssertionError("dialled a printer with an unprintable job")

        monkeypatch.setattr(printing.socket, "create_connection", refuse)
        path = _job(tmp_path, b"G28\n" + printing.UEL + b"@PJL EOJ\r\n")
        outcome = printing.send_gcode("192.168.1.5", path)
        assert not outcome.ok
        assert outcome.reason == "payload"

    def test_the_framed_job_is_what_arrives(self, tmp_path, monkeypatch):
        sock, port = _listener()
        monkeypatch.setattr(printing, "PRINT_PORT", port)
        received: list[bytes] = []
        thread = threading.Thread(target=_collect(sock, received))
        thread.start()
        try:
            outcome = printing.send_gcode("127.0.0.1", _job(tmp_path), name="part")
        finally:
            thread.join(timeout=5)
            sock.close()

        assert outcome.ok, outcome.detail
        assert received and received[0] == printing.pjl_job(b"G28\n", name="part")
        assert outcome.bytes_sent == len(received[0])

    def test_a_body_larger_than_one_chunk_arrives_intact(self, tmp_path, monkeypatch):
        """The whole point of streaming is that this is never held in memory."""
        body = b"G1 X1 Y1 F1200\n" * 20_000
        assert len(body) > printing.CHUNK_BYTES
        sock, port = _listener()
        monkeypatch.setattr(printing, "PRINT_PORT", port)
        received: list[bytes] = []
        thread = threading.Thread(target=_collect(sock, received))
        thread.start()
        try:
            outcome = printing.send_gcode("127.0.0.1", _job(tmp_path, body), name="big")
        finally:
            thread.join(timeout=10)
            sock.close()

        assert outcome.ok, outcome.detail
        assert received[0] == printing.pjl_header("big") + body + printing.PJL_FOOTER

    def test_a_closed_port_is_reported_as_refused(self, tmp_path, monkeypatch):
        sock, port = _listener()
        sock.close()  # nothing is listening on it now
        monkeypatch.setattr(printing, "PRINT_PORT", port)
        outcome = printing.send_gcode("127.0.0.1", _job(tmp_path))
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

    @pytest.mark.parametrize(
        "body",
        [
            "<html>404</html>",
            "<html><body>Not Found (404) please try again</body></html>",
            "<h1>It works! (nginx)</h1>",
            "",
        ],
    )
    def test_something_that_is_not_the_reply_yields_nothing(self, body):
        """Anchored on the call's own name.

        Before it was, any page with a bracket in it parsed: an error page
        reading "Not Found (404)" came back as an estimate of 404 seconds and a
        healthy printer, and the guard that was meant to catch that never fired.
        """
        assert printing.parse_status(body) == {}

    def test_a_real_read_returns_the_parsed_fields(self, monkeypatch):
        """Against a listener, so the HTTP framing is exercised and not assumed."""
        sock, port = _listener()
        monkeypatch.setattr(printing, "STATUS_PORT", port)
        reply = self.REPLY

        def serve():
            conn, _ = sock.accept()
            with conn:
                conn.recv(4096)
                conn.sendall(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
                    b"Connection: close\r\n\r\n" + reply.encode()
                )

        thread = threading.Thread(target=serve)
        thread.start()
        try:
            outcome = printing.fetch_status("127.0.0.1")
        finally:
            thread.join(timeout=5)
            sock.close()

        assert outcome.ok, outcome.detail
        assert outcome.fields["percent"] == 42
        assert outcome.fields["nozzle_temp"] == 205

    def test_a_web_server_that_is_not_the_printer_is_not_believed(self, monkeypatch):
        """Something is listening on port 80; that does not make it a printer."""
        sock, port = _listener()
        monkeypatch.setattr(printing, "STATUS_PORT", port)

        def serve():
            conn, _ = sock.accept()
            with conn:
                conn.recv(4096)
                conn.sendall(
                    b"HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n"
                    b"<html><body>Not Found (404)</body></html>"
                )

        thread = threading.Thread(target=serve)
        thread.start()
        try:
            outcome = printing.fetch_status("127.0.0.1")
        finally:
            thread.join(timeout=5)
            sock.close()

        assert outcome.ok is False
        assert outcome.fields == {}

    def test_a_reply_without_a_job_state_is_not_believed(self):
        """The state is what every other field is read against."""
        assert printing.parse_status("set_status(3600);") == {}
        assert printing.parse_status("set_status(3600, notanumber, 42);") == {}
