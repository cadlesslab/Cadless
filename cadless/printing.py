"""Sending a sliced model to a 3D printer on the local network.

The device speaks the raw print protocol on TCP 9100 (JetDirect/AppSocket): a
PJL job envelope wrapping a G-code body. Status is a separate, unauthenticated
HTTP endpoint that the printer's own web UI polls.

Three rules shape this module:

- **No exception escapes.** Every entry point answers with a result object whose
  ``ok`` says what happened, so a request handler never turns a cable being
  unplugged into a 500. Name resolution is the sharp edge here: it raises
  ``UnicodeError`` as readily as ``OSError``, and only one of those looks like a
  network failure.
- **The address is checked against a list of what is allowed, not of what is
  not.** ``resolve_target`` resolves once, permits only the ranges a device on a
  local network can occupy, and hands back the literal for the caller to dial --
  resolving again at connect time would let a name answer differently.
- **The body never touches memory whole.** A sliced part runs to hundreds of
  megabytes, and this is called from a request handler.

The engine may not import the web or worker layers, so nothing here knows about
requests or stores. It does read the engine's own configuration, for the one
question an operator answers rather than a caller: what kind of printing this
deployment offers at all.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from dataclasses import dataclass, field
from typing import Any

from cadless.config import PRINTING_MODES, settings

#: The raw-print port. The printer's firmware reads PJL and G-code from it.
PRINT_PORT = 9100

#: The printer's own web UI is served here, and so is the status endpoint below.
STATUS_PORT = 80

#: What the web UI polls for job state. Answers a JavaScript call rather than
#: JSON -- ``set_status(a, b, c, ...);`` -- which is why the reply is parsed
#: positionally by :func:`parse_status`, and why :data:`STATUS_CALL` has to
#: appear before any of it is believed.
STATUS_PATH = "/cgi-bin/config_periodic_data.cgi"
STATUS_CALL = "set_status("

#: Universal Exit Language: the escape that returns a printer to PJL from
#: whatever it was doing. It opens and closes every job.
UEL = b"\x1b%-12345X"

#: The byte UEL starts with. A body that can spell it can end the job early.
ESC = 0x1B

DEFAULT_CONNECT_TIMEOUT = 5.0
DEFAULT_SEND_TIMEOUT = 120.0
DEFAULT_STATUS_TIMEOUT = 5.0

#: Read and write size for the job body, which is far too big to hold.
CHUNK_BYTES = 1 << 16

#: The status reply is one short line; anything larger is not it.
STATUS_READ_CAP = 65536

#: PJL truncates a long job name anyway, and the panel shows less than this.
NAME_MAX = 64

#: Job states the device reports. Only the values this code acts on are named.
_IDLE_STATE = 10001
_PRINTING_STATES = range(10002, 10024)


#: Where a printer can be. An allow-list rather than a list of the ways out,
#: because the ways out cannot be enumerated: ``is_private`` answers IANA's
#: "not globally reachable", which is a different question and says yes to 6to4,
#: Teredo and the local-use NAT64 prefix -- each of which carries an IPv4
#: address straight to the open internet. Measured on CPython 3.13:
#: ``ip_address("2002:0808:0808::1").is_private`` is True while its ``sixtofour``
#: is 8.8.8.8. Listing what is allowed fails closed on the next such prefix
#: instead of waiting for someone to notice it.
def _without(
    net: ipaddress.IPv4Network | ipaddress.IPv6Network,
    *holes: ipaddress.IPv4Network | ipaddress.IPv6Network,
) -> tuple:
    """``net`` minus ``holes``, as the networks that remain.

    Written as an exclusion so the rule stays a list of what is allowed. A
    deny-list consulted after an allow-list has the shape this module argues
    against two paragraphs up: it covers the addresses somebody thought of, and
    the next one a cloud platform introduces is allowed by default.
    """
    remaining = [net]
    for hole in holes:
        nxt: list = []
        for candidate in remaining:
            if hole.subnet_of(candidate):
                nxt.extend(candidate.address_exclude(hole))
            else:
                nxt.append(candidate)
        remaining = nxt
    return tuple(remaining)


_ALLOWED_V4 = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    # Link-local, less the two blocks the major clouds put instance metadata,
    # task credentials and their resolver in. The range is here for a device
    # that assigned itself an address over a direct connection; nothing anyone
    # prints to lives in those blocks, and this image runs on those hosts.
    *_without(
        ipaddress.ip_network("169.254.0.0/16"),
        ipaddress.ip_network("169.254.169.0/24"),
        ipaddress.ip_network("169.254.170.0/24"),
    ),
)
_ALLOWED_V6 = (
    ipaddress.ip_network("::1/128"),
    *_without(ipaddress.ip_network("fc00::/7"), ipaddress.ip_network("fd00:ec2::/32")),
    ipaddress.ip_network("fe80::/10"),
)


#: What a deployment offers. `Settings.printing` documents what each means and
#: refuses anything else while the settings object is being built, so by the
#: time this module reads one it has already been checked.
MODE_AUTO = "auto"
MODE_DOWNLOAD = "download"
MODE_OFF = "off"
MODES = PRINTING_MODES


@dataclass(frozen=True)
class Actions:
    """Which printing actions a deployment can actually complete."""

    send: bool
    download: bool


def mode() -> str:
    """The configured mode.

    An operator's value cannot get here unchecked -- `Settings` refuses one it
    does not know while it is being built. What this guards is the other way in:
    the singleton is mutable and assignment is not validated, so a composed
    build or a test can put anything on it. That case falls back to ``download``
    rather than ``auto``, because the difference between them is whether this
    process opens connections, and an unreadable value is not a reason to.
    """
    configured = str(settings.printing or "").strip().lower()
    return configured if configured in MODES else MODE_DOWNLOAD


def actions(*, slicer_available: bool, address_configured: bool) -> Actions:
    """What this deployment can do, given what it has.

    Sending needs somewhere to send to, so a configured address is most of the
    question. It is not all of it. A build that refuses settings writes is not
    thereby a build with no address: refusing a write does not remove what an
    earlier launch of the same data directory saved, and `require_identity` is
    a launch decision that can be turned on over a file already holding one.
    That address is on whoever ran it locally's network, and the people using
    the hosted build are not on it -- so a saved address there is somebody
    else's printer, and sending to it is the one outcome nobody wants.

    Downloading is offered wherever there is a slicer, because a file the user
    carries to the printer themselves works from everywhere.
    """
    current = mode()
    if not slicer_available or current == MODE_OFF:
        return Actions(send=False, download=False)
    sends = address_configured and current == MODE_AUTO and not settings.require_identity
    return Actions(send=sends, download=True)


class AddressRefused(ValueError):
    """The configured printer address is not one this may dial.

    Separate from a plain ``ValueError`` so a caller can tell a refusal apart
    from a malformed request body without matching on message text.
    """


@dataclass(frozen=True)
class PrintOutcome:
    """What came of trying to put a job on the wire."""

    ok: bool
    detail: str
    bytes_sent: int = 0

    @property
    def reason(self) -> str:
        """The failure kind, for a caller choosing a message. ``""`` when ok."""
        return "" if self.ok else self.detail.split(":", 1)[0]


@dataclass(frozen=True)
class StatusOutcome:
    """What the device said about itself, or why it would not say."""

    ok: bool
    detail: str = ""
    fields: dict[str, Any] = field(default_factory=dict)


def _unwrap(
    parsed: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """The IPv4 address an IPv6 one carries, when it carries one.

    ``::ffff:a.b.c.d`` and ``2002::/16`` are how an IPv4 destination gets
    written as IPv6, and the address inside is where the traffic ends up, so it
    is what the allow-list should see. Teredo is deliberately not unwrapped: its
    endpoints are public by construction, so leaving it wrapped lets the
    allow-list refuse it without a special case.
    """
    for attr in ("ipv4_mapped", "sixtofour"):
        found = getattr(parsed, attr, None)
        if found is not None:
            return found
    return parsed


def _is_local(parsed: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Whether an address is one this may dial."""
    addr = _unwrap(parsed)
    allowed = _ALLOWED_V4 if addr.version == 4 else _ALLOWED_V6
    return any(addr in net for net in allowed)


def _unbracket(address: str) -> str:
    """Strip the brackets a host:port string puts round an IPv6 literal."""
    host = (address or "").strip()
    if host.startswith("[") and host.endswith("]"):
        return host[1:-1]
    return host


def refuse_public_literal(address: str) -> None:
    """Refuse an address that is plainly not one this may dial.

    Cheap on purpose. This runs when settings are saved, so it must not perform
    a name lookup: a save that hangs on DNS is a worse failure than a refusal
    that arrives one step later. A name is therefore allowed through here and
    checked by :func:`resolve_target` at the moment it is dialled, against the
    same list. What this catches is what someone can be told about immediately
    -- a public address typed into the box, or a port appended to it.
    """
    host = _unbracket(address)
    if not host:
        raise AddressRefused("no printer address is configured")
    try:
        parsed = ipaddress.ip_address(host)
    except ValueError:
        if ":" in host:
            # Neither a name nor an IPv6 literal. Almost always `host:9100`,
            # which would otherwise be saved happily and fail at dial time with
            # a message about the name not resolving.
            raise AddressRefused(
                f"{host} looks like it has a port on the end; give the address only"
            ) from None
        return  # a name; settled at dial time
    if not _is_local(parsed):
        raise AddressRefused(
            f"{host} is not on a local network; a printer address must be one of "
            "10.x, 172.16-31.x, 192.168.x, 127.x, 169.254.x, or an IPv6 unique-local "
            "or link-local address"
        )


def resolve_target(address: str) -> str:
    """Return the IP literal to dial for ``address``, or raise `AddressRefused`.

    The check runs over *every* address the name resolves to and fails closed:
    one answer outside the allowed ranges refuses the lot, because a name that
    sometimes resolves outward is exactly the case this is here to stop.

    The returned literal is what the caller connects to. Handing back the name
    would leave a second resolution between the check and the connection.
    """
    host = _unbracket(address)
    if not host:
        raise AddressRefused("no printer address is configured")

    try:
        infos = socket.getaddrinfo(host, PRINT_PORT, proto=socket.IPPROTO_TCP)
    except UnicodeError as exc:
        # Not an OSError. `getaddrinfo` encodes the name as IDNA first, and a
        # label over 63 characters fails there -- so catching only OSError lets
        # a saved address turn every later call into a 500.
        raise AddressRefused(f"{host} is not a usable host name: {exc}") from exc
    except OSError as exc:
        raise AddressRefused(f"{host} does not resolve: {exc}") from exc

    literals = []
    for info in infos:
        literal = info[4][0]
        try:
            parsed = ipaddress.ip_address(literal)
        except ValueError as exc:  # a resolver answering something unparseable
            raise AddressRefused(f"{host} resolves to an unusable address") from exc
        if not _is_local(parsed):
            raise AddressRefused(f"{host} resolves to {literal}, which is not on a local network")
        literals.append(literal)

    if not literals:
        raise AddressRefused(f"{host} does not resolve to any address")
    return literals[0]


def pjl_header(name: str) -> bytes:
    """The bytes that open one job.

    ``name`` is sanitised rather than escaped. PJL has no escape for a quote
    inside a quoted value, so a name containing one would end the value early
    and leave the rest to be read as commands.
    """
    safe = "".join(c for c in name if c.isalnum() or c in "._- ")[:NAME_MAX] or "cadless"
    return UEL + f'@PJL JOB NAME="{safe}"\r\n'.encode("ascii", "ignore")


#: The bytes that close one job and leave the device back in PJL.
PJL_FOOTER = UEL + b"@PJL EOJ\r\n" + UEL


def pjl_job(gcode: bytes, *, name: str) -> bytes:
    """Wrap ``gcode`` in the PJL job envelope the raw-print port expects.

    The whole payload in memory, which is why :func:`send_gcode` does not use
    it: this is for callers holding a small body already, and for tests that
    want to assert on the exact bytes.

    :raises ValueError: if ``gcode`` contains an escape byte -- see
        :func:`contains_escape` for why that is refused rather than stripped.
    """
    if ESC in gcode:
        raise ValueError("the job contains an escape byte and will not be sent")
    return pjl_header(name) + gcode + PJL_FOOTER


def contains_escape(path: str) -> bool:
    """Whether the file holds an escape byte, read a chunk at a time.

    An envelope only separates one job from the next while the body cannot
    spell the thing that ends it, and ``ESC`` is the first byte of the UEL
    sequence that does. G-code is text and a slicer has no reason to emit one,
    so a body containing one is not a job to be repaired: quietly editing bytes
    on their way to a machine that moves is the worse answer, and so is sending
    the head of a file before discovering the rest is not printable.
    """
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(CHUNK_BYTES)
            if not chunk:
                return False
            if ESC in chunk:
                return True


def send_gcode(
    address: str,
    gcode_path: str,
    *,
    name: str = "cadless",
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
    send_timeout: float = DEFAULT_SEND_TIMEOUT,
) -> PrintOutcome:
    """Stream one job from ``gcode_path`` to the printer's raw-print port.

    The port acknowledges nothing, so a clean write is the whole of the good
    news: it says the bytes left this machine, not that the print succeeded.
    Callers wanting more read :func:`fetch_status` afterwards.

    The body is checked for an escape before anything is dialled, so a job that
    will be refused is refused without the printer seeing half of it.
    """
    try:
        size = os.path.getsize(gcode_path)
    except OSError as exc:
        return PrintOutcome(False, f"missing: the sliced file could not be read ({exc})")
    if size == 0:
        return PrintOutcome(False, "empty: there is nothing to print")

    try:
        target = resolve_target(address)
    except AddressRefused as exc:
        return PrintOutcome(False, f"address: {exc}")

    try:
        if contains_escape(gcode_path):
            return PrintOutcome(
                False, "payload: the sliced job contains an escape byte and will not be sent"
            )
    except OSError as exc:
        return PrintOutcome(False, f"missing: the sliced file could not be read ({exc})")

    header, footer = pjl_header(name), PJL_FOOTER
    sent = 0
    try:
        with (
            open(gcode_path, "rb") as body,
            socket.create_connection((target, PRINT_PORT), timeout=connect_timeout) as sock,
        ):
            sock.settimeout(send_timeout)
            sock.sendall(header)
            sent += len(header)
            while True:
                chunk = body.read(CHUNK_BYTES)
                if not chunk:
                    break
                sock.sendall(chunk)
                sent += len(chunk)
            sock.sendall(footer)
            sent += len(footer)
    except TimeoutError as exc:
        return PrintOutcome(False, f"timeout: {target}:{PRINT_PORT} did not answer in time ({exc})")
    except ConnectionRefusedError:
        return PrintOutcome(
            False,
            f"refused: {target} refused port {PRINT_PORT} -- the printer is reachable "
            "but not accepting print jobs",
        )
    except OSError as exc:
        return PrintOutcome(
            False, f"unreachable: {target}:{PRINT_PORT} could not be reached ({exc})"
        )

    return PrintOutcome(True, f"sent {sent} bytes to {target}", bytes_sent=sent)


def parse_status(body: str) -> dict[str, Any]:
    """Pull the fields out of the ``set_status(...)`` reply.

    The endpoint answers a JavaScript call rather than data, so the values are
    positional and unnamed. Anchored on the call's own name: without that, any
    page with a bracket in it parses -- an HTML error page reading "Not Found
    (404)" came back as an estimate of 404 seconds and a healthy printer.
    """
    start = body.find(STATUS_CALL)
    if start == -1:
        return {}
    open_paren = start + len(STATUS_CALL) - 1
    end = body.find(")", open_paren)
    if end == -1:
        return {}
    parts = [p.strip().strip("'\"") for p in body[open_paren + 1 : end].split(",")]

    def _int(index: int) -> int | None:
        try:
            return int(parts[index])
        except (IndexError, ValueError):
            return None

    state = _int(1)
    if state is None:
        # The job state is the one field everything else is read against. A
        # reply that has not got one is not this endpoint's reply.
        return {}
    return {
        "estimate_seconds": _int(0),
        "state_code": state,
        "percent": _int(2),
        "bed_temp": _int(9),
        "nozzle_temp": _int(10),
        "filename": parts[11] if len(parts) > 11 else None,
        "extra": parts[12:],
        "idle": state == _IDLE_STATE,
        "printing": state in _PRINTING_STATES,
    }


def fetch_status(
    address: str,
    *,
    timeout: float = DEFAULT_STATUS_TIMEOUT,
) -> StatusOutcome:
    """Ask the printer what it is doing.

    Read-only, and the same rules as :func:`send_gcode` apply to the address --
    this reaches out over the network from the API process, so it is the same
    egress decision even though nothing is printed.
    """
    try:
        target = resolve_target(address)
    except AddressRefused as exc:
        return StatusOutcome(False, f"address: {exc}")

    # Built here rather than with a URL library because the literal is already
    # settled: handing a name to a fetcher would resolve it a second time.
    request = (
        f"GET {STATUS_PATH} HTTP/1.1\r\nHost: {target}\r\n"
        "Connection: close\r\nUser-Agent: cadless\r\n\r\n"
    ).encode("ascii")
    try:
        with socket.create_connection((target, STATUS_PORT), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(request)
            chunks: list[bytes] = []
            read = 0
            while read < STATUS_READ_CAP:
                chunk = sock.recv(CHUNK_BYTES)
                if not chunk:
                    break
                chunks.append(chunk)
                read += len(chunk)
    except TimeoutError as exc:
        return StatusOutcome(False, f"timeout: {target} did not answer in time ({exc})")
    except ConnectionRefusedError:
        return StatusOutcome(False, f"refused: {target} refused port {STATUS_PORT}")
    except OSError as exc:
        return StatusOutcome(False, f"unreachable: {target} could not be reached ({exc})")

    raw = b"".join(chunks).decode("utf-8", "replace")
    _, _, body = raw.partition("\r\n\r\n")
    fields = parse_status(body or raw)
    if not fields:
        return StatusOutcome(False, f"unexpected: {target} answered something this cannot read")
    return StatusOutcome(True, "", fields)


def probe(address: str, *, timeout: float = DEFAULT_STATUS_TIMEOUT) -> PrintOutcome:
    """Check that the raw-print port is open, without sending a job.

    This is what a "test connection" button wants: opening and closing the
    socket proves the path all the way to the port that matters, and printing
    nothing is the point.
    """
    try:
        target = resolve_target(address)
    except AddressRefused as exc:
        return PrintOutcome(False, f"address: {exc}")
    try:
        with socket.create_connection((target, PRINT_PORT), timeout=timeout):
            pass
    except TimeoutError:
        return PrintOutcome(False, f"timeout: {target}:{PRINT_PORT} did not answer in time")
    except ConnectionRefusedError:
        return PrintOutcome(False, f"refused: {target} refused port {PRINT_PORT}")
    except OSError as exc:
        return PrintOutcome(False, f"unreachable: {target} could not be reached ({exc})")
    return PrintOutcome(True, f"{target}:{PRINT_PORT} is accepting connections")
