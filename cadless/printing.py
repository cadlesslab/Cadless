"""Sending a sliced model to a 3D printer on the local network.

The device speaks the raw print protocol on TCP 9100 (JetDirect/AppSocket): a
PJL job envelope wrapping a G-code body. Status is a separate, unauthenticated
HTTP endpoint that the printer's own web UI polls.

Two rules shape this module, both inherited from how ``cadless/worker.py``
reaches its remote worker:

- **No exception escapes.** Every entry point answers with a result object whose
  ``ok`` says what happened, so a request handler never turns a cable being
  unplugged into a 500.
- **The address is resolved once and then dialled by literal.** ``resolve_target``
  refuses anything that is not on a local network, and the caller connects to the
  address it returned rather than to the name. Resolving again at connect time
  would let a name answer differently the second time.

The engine must not import ``backend`` or ``worker`` (architecture invariant 3),
so nothing here knows about requests, stores or settings files.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from typing import Any

#: The raw-print port. The printer's firmware reads PJL and G-code from it.
PRINT_PORT = 9100

#: The printer's own web UI is served here, and so is the status endpoint below.
STATUS_PORT = 80

#: What the web UI polls for job state. Answers a JavaScript call rather than
#: JSON -- ``set_status(a, b, c, ...);`` -- which is why the reply is parsed
#: positionally by :func:`parse_status`.
STATUS_PATH = "/cgi-bin/config_periodic_data.cgi"

#: Universal Exit Language: the escape that returns a printer to PJL from
#: whatever it was doing. It opens and closes every job.
UEL = b"\x1b%-12345X"

DEFAULT_CONNECT_TIMEOUT = 5.0
DEFAULT_SEND_TIMEOUT = 120.0
DEFAULT_STATUS_TIMEOUT = 5.0

#: Job states the device reports, from the field the web UI reads. Only the
#: values this code acts on are named; anything else is reported as its number.
_IDLE_STATE = 10001
_PRINTING_STATES = range(10002, 10024)


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


def _is_local(parsed: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Whether an address is one this may dial. The single rule both checks use."""
    return parsed.is_private or parsed.is_loopback or parsed.is_link_local


def _unbracket(address: str) -> str:
    """Strip the brackets a host:port string puts round an IPv6 literal."""
    host = (address or "").strip()
    if host.startswith("[") and host.endswith("]"):
        return host[1:-1]
    return host


def refuse_public_literal(address: str) -> None:
    """Refuse an address that is plainly not on a local network.

    Cheap on purpose. This runs when settings are saved, so it must not perform
    a name lookup: a save that hangs on DNS is a worse failure than a refusal
    that arrives one step later. A name is therefore allowed through here and
    checked by :func:`resolve_target` at the moment it is dialled, which refuses
    on the same rule. What this catches is the mistake worth catching early --
    someone typing a public IP into the box.
    """
    host = _unbracket(address)
    if not host:
        raise AddressRefused("no printer address is configured")
    try:
        parsed = ipaddress.ip_address(host)
    except ValueError:
        return  # a name; settled at dial time
    if not _is_local(parsed):
        raise AddressRefused(
            f"{host} is a public address; a printer address must be on your local network"
        )


def resolve_target(address: str) -> str:
    """Return the IP literal to dial for ``address``, or raise `AddressRefused`.

    A printer lives on the network the machine is already on, so a public
    address is never the right answer and is refused rather than dialled. The
    check runs over *every* address the name resolves to and fails closed: one
    public answer among several refuses the lot, because a name that sometimes
    resolves outward is exactly the case this is here to stop.

    The returned literal is what the caller connects to. Handing back the name
    would leave a second resolution between the check and the connection.
    """
    host = _unbracket(address)
    if not host:
        raise AddressRefused("no printer address is configured")

    try:
        infos = socket.getaddrinfo(host, PRINT_PORT, proto=socket.IPPROTO_TCP)
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
            raise AddressRefused(
                f"{host} resolves to the public address {literal}; a printer address "
                "must be on your local network"
            )
        literals.append(literal)

    if not literals:
        raise AddressRefused(f"{host} does not resolve to any address")
    return literals[0]


def pjl_job(gcode: bytes, *, name: str) -> bytes:
    """Wrap ``gcode`` in the PJL job envelope the raw-print port expects.

    The envelope is what separates one job from the next on a port that is
    otherwise a byte stream: UEL opens it, ``@PJL JOB`` names it so the job shows
    up on the panel, ``@PJL EOJ`` ends it, and the trailing UEL leaves the device
    back in PJL rather than mid-body.

    ``name`` is sanitised rather than escaped. PJL has no escape for a quote
    inside a quoted value, so a name containing one would end the value early and
    leave the rest to be read as commands.
    """
    safe = "".join(c for c in name if c.isalnum() or c in "._- ")[:64] or "cadless"
    header = UEL + f'@PJL JOB NAME="{safe}"\r\n'.encode("ascii", "ignore")
    footer = UEL + b"@PJL EOJ\r\n" + UEL
    return header + gcode + footer


def send_gcode(
    address: str,
    gcode: bytes,
    *,
    name: str = "cadless",
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
    send_timeout: float = DEFAULT_SEND_TIMEOUT,
) -> PrintOutcome:
    """Put one job on the printer's raw-print port.

    The port acknowledges nothing, so a clean write is the whole of the good
    news: it says the bytes left this machine, not that the print succeeded.
    Callers wanting more read :func:`fetch_status` afterwards.
    """
    if not gcode:
        return PrintOutcome(False, "empty: there is nothing to print")
    try:
        target = resolve_target(address)
    except AddressRefused as exc:
        return PrintOutcome(False, f"address: {exc}")

    payload = pjl_job(gcode, name=name)
    try:
        with socket.create_connection((target, PRINT_PORT), timeout=connect_timeout) as sock:
            sock.settimeout(send_timeout)
            sock.sendall(payload)
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

    return PrintOutcome(True, f"sent {len(payload)} bytes to {target}", bytes_sent=len(payload))


def parse_status(body: str) -> dict[str, Any]:
    """Pull the fields out of the ``set_status(...)`` reply.

    The endpoint answers a JavaScript call rather than data, so the values are
    positional and unnamed. Only the leading positions this code has a use for
    are named; the rest are kept under ``extra`` so nothing is silently dropped.
    """
    start, end = body.find("("), body.rfind(")")
    if start == -1 or end <= start:
        return {}
    parts = [p.strip().strip("'\"") for p in body[start + 1 : end].split(",")]

    def _int(index: int) -> int | None:
        try:
            return int(parts[index])
        except (IndexError, ValueError):
            return None

    state = _int(1)
    out: dict[str, Any] = {
        "estimate_seconds": _int(0),
        "state_code": state,
        "percent": _int(2),
        "bed_temp": _int(9),
        "nozzle_temp": _int(10),
        "filename": parts[11] if len(parts) > 11 else None,
        "extra": parts[12:],
    }
    if state is not None:
        out["idle"] = state == _IDLE_STATE
        out["printing"] = state in _PRINTING_STATES
    return out


def fetch_status(
    address: str,
    *,
    timeout: float = DEFAULT_STATUS_TIMEOUT,
) -> StatusOutcome:
    """Ask the printer what it is doing.

    Read-only, and the same refusal rules as :func:`send_gcode` apply to the
    address -- this reaches out over the network from the API process, so it is
    the same egress decision even though nothing is printed.
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
            chunks = []
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
                if sum(len(c) for c in chunks) > 65536:
                    break  # the reply is one short line; anything larger is not it
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
