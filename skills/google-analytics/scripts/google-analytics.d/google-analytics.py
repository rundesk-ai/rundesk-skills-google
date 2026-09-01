#!/usr/bin/env python3
"""Read bounded Google Analytics 4 acquisition, audience, event, funnel, and commerce data."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


ADMIN_BASE = "https://analyticsadmin.googleapis.com/v1beta"
DATA_BASE = "https://analyticsdata.googleapis.com/v1beta"
FUNNEL_DATA_BASE = "https://analyticsdata.googleapis.com/v1alpha"
BASE_FIELD_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
# **ASCII digits, not `str.isdigit`.** `isdigit` is true for Arabic-Indic and other Unicode digits,
# and a resource ID goes into the request path: a non-ASCII "number" would pass the check and then
# raise `UnicodeEncodeError` deep inside urllib instead of being refused here.
NUMERIC_ID_RE = re.compile(r"[0-9]{1,20}")
MAX_PAGES = 100
MAX_DIMENSIONS = 9
MAX_METRICS = 10
#: GA4's longest event, event-parameter, and item-parameter name. User-property names are capped
#: lower, at 24, but the difference is Google's to enforce; this bound only keeps an unbounded
#: caller string out of a request.
MAX_GA4_NAME = 40
#: Every colon-bearing field family the Data API documents, and what the part after the colon is:
#: a GA4 parameter or user-property name, a GA4 event name, or a numeric custom channel ID. A base
#: outside this table never takes a suffix, so `date:anything` is refused rather than sent.
CUSTOM_FIELD_SUFFIXES = {
    "customEvent": "name",
    "customUser": "name",
    "customItem": "name",
    "averageCustomEvent": "name",
    "countCustomEvent": "name",
    "sessionKeyEventRate": "name",
    "userKeyEventRate": "name",
    "sessionCustomChannelGroup": "channel",
    "firstUserCustomChannelGroup": "channel",
    "customChannelGroup": "channel",
}
#: The one family whose suffix may also carry a bracketed event name, for an event-scoped custom
#: dimension registered before October 2020.
LEGACY_PARAMETER_FAMILY = "customEvent"
LEGACY_PARAMETER_RE = re.compile(r"(?P<parameter>[^\[\]]+)\[(?P<event>[^\[\]]+)\]")


class AnalyticsError(RuntimeError):
    """A safe, user-facing Analytics integration failure."""


def split_csv(value: str) -> List[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


# --- Rundesk-managed Google sign-in -------------------------------------------------------------
#
# Rundesk owns the OAuth client, the browser, the refresh token, and where those are kept. This
# package owns none of it and asks the install's own CLI for one short-lived access token, which
# arrives over one connected unnamed local socket held by nothing but these two processes rather
# than through argv, the environment, stdout, or a file. The wire format is Rundesk's hidden
# `_oauth` bridge, version 1. Rundesk refuses a pipe, a named socket, a regular file, and 0, 1, 2.
COMMAND_VARIABLE = "RUNDESK_COMMAND"
BRIDGE = "_oauth"
# Which OAuth provider Rundesk signs in to. What that name means — Google's endpoints, identity
# fields, and the scope each capability carries — is declared by this catalog's `google-auth`
# package, which this one reads nothing from and never runs.
PROVIDER = "google"
# The capability this package asks for. Rundesk turns it into the scope the provider declares, so no
# scope is chosen here.
CAPABILITY = "analytics"
BRIDGE_VERSION = 1
MAX_FRAME = 65536
# One bound for a whole child, not for each step of one: a request that spends its time reading a
# frame has that much less left to be waited on. Rundesk gives a person 180 seconds at the browser
# when a grant has to be widened, and its own calls to Google time out at 30, so this covers every
# phase and still ends.
BRIDGE_SECONDS = 300
# `rundesk login google` waits on a person at the browser first and on Google afterwards, so its
# bound is larger than the bridge's. It is still finite: a login nobody completes is stopped rather
# than left holding this command open.
SIGN_IN_SECONDS = 420
# Rundesk's own words about signing in belong beside this command's other diagnostics, never in the
# rows a caller parses.
#: How long a stopped child and its group get to actually go. Bounded like everything else here:
#: the point of this window is to end a wait, so it may not become one.
STOP_SECONDS = 5.0

#: How long an already-finished child's abandoned pipe is given to yield what it has. Short on
#: purpose: reaching that path means something else is holding the pipe open, so this is a grace
#: for bytes already in flight, never a wait for a writer nobody here owns.
LINGER_SECONDS = 0.2

#: The most partial output that is ever carried out of an abandoned pipe. A refusal is one line;
#: this is room for context and a bound on a writer nobody here owns.
MAX_SAID = 65536

DIAGNOSTIC_FD = 2
# A Rundesk released before this bridge answers as argparse does: exit 2, naming the choice it did
# not recognize. Rundesk's own refusals exit 1, so the two are never mistaken for each other.
UNSUPPORTED_EXIT = 2
UNSUPPORTED_MARKS = ("invalid choice: '_oauth'", "invalid choice: 'login'",
                     "invalid choice: 'google'", "unrecognized arguments")
MAX_REASON = 400


def login_command(profile: str) -> str:
    """The exact command that connects a Google account for one OAuth app profile."""
    return "rundesk login google" + (f" --profile {profile}" if profile else "")


def rundesk_command() -> str:
    """The rundesk this install means, which is a whole path and is not always on PATH."""
    command = os.environ.get(COMMAND_VARIABLE, "").strip()
    if command:
        return command
    found = shutil.which("rundesk")
    if not found:
        raise AnalyticsError(
            f"This skill signs in through Rundesk, and no Rundesk is reachable: {COMMAND_VARIABLE} "
            f"is unset and no rundesk command is on PATH. Run: {login_command('')}"
        )
    return found


def read_exactly(connection: socket.socket, wanted: int, deadline: float) -> bytes:
    """Exactly one bounded segment, refusing rather than waiting on an install that never answers."""
    held = bytearray()
    while len(held) < wanted:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AnalyticsError("Rundesk did not answer the Google request in time.")
        connection.settimeout(remaining)
        try:
            part = connection.recv(wanted - len(held))
        except socket.timeout as exc:
            raise AnalyticsError("Rundesk did not answer the Google request in time.") from exc
        if not part:
            raise AnalyticsError("Rundesk closed the Google response before answering.")
        held.extend(part)
    return bytes(held)


def read_frame(connection: socket.socket, deadline: float) -> Dict[str, Any]:
    """One version 1 frame: four big-endian length bytes, then that much compact UTF-8 JSON.

    `deadline` is a `time.monotonic` instant shared with the wait that follows, so reading and
    reaping cannot each spend the whole allowance.
    """
    size = struct.unpack(">I", read_exactly(connection, 4, deadline))[0]
    if size > MAX_FRAME:
        raise AnalyticsError("Rundesk sent an oversized Google response.")
    try:
        payload = json.loads(read_exactly(connection, size, deadline).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise AnalyticsError("Rundesk sent a malformed Google response.") from exc
    if not isinstance(payload, dict) or payload.get("version") != BRIDGE_VERSION:
        raise AnalyticsError("Rundesk sent a Google response version this package cannot read.")
    return payload


def framed_error(payload: Dict[str, Any]) -> str:
    """The refusal Rundesk framed, bounded. A frame carrying no reason yields nothing."""
    reason = payload.get("error")
    return reason.strip()[:MAX_REASON] if isinstance(reason, str) and reason.strip() else ""


def bridge_reason(said: str) -> str:
    """Rundesk's own refusal, bounded, and never more of another program's output than that."""
    for line in said.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        head, marker, reason = stripped.partition(" — ")
        return (reason if marker and head.endswith("FAILED") else stripped)[:MAX_REASON]
    return ""


def unsupported(code: int, said: str) -> bool:
    """Whether this Rundesk predates managed sign-in rather than having refused the request."""
    return code == UNSUPPORTED_EXIT and any(mark in said for mark in UNSUPPORTED_MARKS)


def refused(code: int, said: str, profile: str, framed: str = "",
            trouble: str = "") -> AnalyticsError:
    if unsupported(code, said):
        return AnalyticsError(
            "This Rundesk install is older than Rundesk-managed Google sign-in. Update Rundesk, "
            f"then run: {login_command(profile)}"
        )
    # Rundesk's framed reason is its structured answer and comes first; its stderr says the same
    # thing when it could not frame one; the protocol's own complaint is the last resort.
    reason = framed or bridge_reason(said) or trouble or f"rundesk exited {code}"
    # Rundesk names the login command itself when that is the fix. Appending it again turns one
    # instruction into two identical ones, which reads as a program that has lost track of itself.
    run = login_command(profile)
    also = "" if run in reason else f" Run: {run}"
    return AnalyticsError(
        f"Rundesk did not grant Google access: {reason}." + also
    )


def stop_group(process: subprocess.Popen) -> None:
    """Signal the child's whole process group, because a descendant is what holds the pipe open.

    Falls back to the child alone when there is no group to signal — it has already been reaped, or
    the platform does not have one. Every failure here is ignored on purpose: this runs while a
    deadline is already being enforced, and a command must not fail at the step whose whole job is
    to stop something failing.
    """
    try:
        # **`process.pid`, not `os.getpgid(process.pid)`.** Every spawn here uses
        # `start_new_session=True`, so the child *is* the group leader and its pid is the group ID,
        # known without asking anything. Asking has a race: `finished` reaches here having just
        # seen the leader alive, and the leader can exit in the moment between that look and this
        # call. `getpgid` would then raise `ESRCH` and nothing would be signalled, while the group
        # it led still holds live members. The pid does not go stale that way.
        os.killpg(process.pid, signal.SIGKILL)
        return
    except (ProcessLookupError, PermissionError, OSError, AttributeError):
        pass
    try:
        process.kill()
    except OSError:
        pass


def as_text(said: object) -> str:
    """Partial output as bounded text, whatever shape the interruption left it in.

    `TimeoutExpired` carries what had been read when it fired, and carries it as *bytes* even from
    a text-mode child, because decoding happens after the read this never got to finish. Bounded
    because nothing downstream needs more than the first refusal line, and an unbounded child
    should not decide how much of this one's memory it uses.
    """
    if said is None:
        return ""
    if isinstance(said, bytes):
        said = said.decode("utf-8", "replace")
    return said[:MAX_SAID] if isinstance(said, str) else ""


def abandoned(process: subprocess.Popen) -> str:
    """Whatever the pipe has to give in a moment, and then let go of it.

    Never waits on whatever is still holding the writing end: this is the path where that holder is
    somebody else's business, so the read is bounded and the pipe is closed rather than drained.
    """
    said = ""
    try:
        _, said = process.communicate(timeout=LINGER_SECONDS)
    except subprocess.TimeoutExpired as expired:
        # **What was read before the wait ran out is kept.** A child that exited non-zero after
        # launching a helper has already written why, and the helper is only holding the pipe open
        # afterwards; dropping that would turn Rundesk's actual refusal into `rundesk exited 1`.
        said = expired.stderr
    except (ValueError, OSError):
        said = ""
    if process.stderr is not None:
        try:
            process.stderr.close()
        except (OSError, ValueError):
            pass
    return as_text(said)


def finished(process: subprocess.Popen, deadline: float, doing: str) -> Tuple[str, int]:
    """What Rundesk said and how it ended, never leaving a descendant holding this command open.

    **A pipe that never reaches end-of-file is not the same as a command that never finished**, and
    telling them apart is the whole of this. `communicate` waits for end-of-file, and Rundesk's
    sign-in opens a browser that inherits the stderr being read — so on a perfectly successful
    login the pipe stays open for as long as the person leaves the browser running.

    So when the wait runs out, the question asked is *whether the child itself is still there*:

    - **It has exited.** The sign-in is over and its exit code is the answer. Something it left
      behind holds the pipe, and that something is the person's browser — killing it would turn a
      completed sign-in into a window that vanished. The pipe is abandoned, not drained, and
      whatever stderr came back in the moment allowed is kept.
    - **It is still running.** Now it really is out of time. The whole process group is signalled —
      by the pid that *is* the group, which `start_new_session=True` guarantees — reaped under a
      bound, and refused.
    """
    try:
        _, said = process.communicate(timeout=max(0.0, deadline - time.monotonic()))
        return said or "", process.returncode
    except subprocess.TimeoutExpired:
        pass
    if process.poll() is not None:
        return abandoned(process), process.returncode
    stop_group(process)
    try:
        process.communicate(timeout=STOP_SECONDS)
    except subprocess.TimeoutExpired:
        abandoned(process)
        try:
            process.wait(timeout=STOP_SECONDS)
        except subprocess.TimeoutExpired:
            pass
    raise AnalyticsError(f"Rundesk did not finish {doing} in time, and was stopped.")


def ask_rundesk(action: List[str], profile: str, seconds: Optional[float] = None) -> Dict[str, Any]:
    """One `_oauth` answer, read from a socket pair no other process holds an end of."""
    command = rundesk_command()
    # One deadline for the whole transaction: spawning, reading the frame, and waiting for the exit
    # share it, so a child that stalls in any one phase cannot extend the others.
    deadline = time.monotonic() + (BRIDGE_SECONDS if seconds is None else seconds)
    ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        try:
            process = subprocess.Popen(
                [command, BRIDGE] + action + ["--response-fd", str(theirs.fileno())],
                stdin=subprocess.DEVNULL, stdout=DIAGNOSTIC_FD, stderr=subprocess.PIPE,
                start_new_session=True,
                pass_fds=(theirs.fileno(),), text=True,
            )
        except OSError as exc:
            raise AnalyticsError(
                f"Cannot run {command} to reach Google: {exc.strerror or exc}."
            ) from exc
        finally:
            # Rundesk holds the only other end, so its refusal closes the socket instead of leaving
            # this command waiting on an end it holds open itself.
            theirs.close()
        payload: Dict[str, Any] = {}
        trouble = ""
        try:
            # Read before waiting: Rundesk writes under its own deadline, and a child blocked on
            # that write would never be reaped by a wait that came first.
            payload = read_frame(ours, deadline)
        except AnalyticsError as exc:
            trouble = str(exc)
        said, code = finished(process, deadline, "the Google request")
    finally:
        ours.close()
    if code != 0 or payload.get("ok") is not True:
        raise refused(code, said, profile, framed_error(payload), trouble)
    return payload


def profile_action(action: List[str], profile: str) -> List[str]:
    return action + (["--profile", profile] if profile else [])


def signed_in_accounts(profile: str) -> List[str]:
    """Every Google account Rundesk holds for one OAuth app profile. Local, with no network call."""
    action = profile_action(["accounts", PROVIDER], profile)
    accounts = ask_rundesk(action, profile).get("accounts")
    if not isinstance(accounts, list) or not all(isinstance(one, str) for one in accounts):
        raise AnalyticsError("Rundesk sent a malformed Google account list.")
    return accounts


def managed_token(profile: str, email: str) -> Tuple[str, str]:
    """One short-lived token and the verified account it belongs to."""
    action = profile_action(["access", PROVIDER, CAPABILITY], profile)
    payload = ask_rundesk(action + (["--email", email] if email else []), profile)
    token, expires, who = (payload.get("access_token"), payload.get("expires_at"),
                           payload.get("email"))
    subject = payload.get("subject")
    # bool is an int, and an expiry of True would otherwise pass every check below.
    if (not isinstance(token, str) or not token or not isinstance(who, str) or not who
            or not isinstance(subject, str) or not subject
            or isinstance(expires, bool) or not isinstance(expires, int)):
        raise AnalyticsError("Rundesk sent no usable Google access token.")
    # Bearer is the one scheme this package knows how to send, so anything else is refused rather
    # than sent as though it were one.
    if payload.get("token_type") != "Bearer":
        raise AnalyticsError("Rundesk sent a Google access token this package cannot send.")
    if expires <= int(time.time()):
        raise AnalyticsError(
            f"Rundesk sent an already expired Google access token. Run: {login_command(profile)}"
        )
    # **Checked here as well as inside Rundesk, and the reason is whose promise it is.** This
    # command is what told the caller which account it would use; a token for a different one would
    # read every figure out of somebody else's Google account under the address they asked for.
    # Compared case-insensitively because an address is not case-sensitive in its domain and
    # providers vary in what they echo back.
    if email and who.casefold() != email.casefold():
        raise AnalyticsError(
            f"Rundesk returned a Google account other than {email}; no Google request was made."
        )
    return token, who


def sign_in(profile: str, seconds: Optional[float] = None) -> None:
    """Rundesk's own public login, so the person sees the browser step and its result."""
    command = rundesk_command()
    action = ["login", "google"] + (["--profile", profile] if profile else [])
    deadline = time.monotonic() + (SIGN_IN_SECONDS if seconds is None else seconds)
    try:
        process = subprocess.Popen(
            [command] + action, stdin=subprocess.DEVNULL, stdout=DIAGNOSTIC_FD,
            stderr=subprocess.PIPE, text=True, start_new_session=True,
        )
    except OSError as exc:
        raise AnalyticsError(
            f"Cannot run {command} to sign in to Google: {exc.strerror or exc}."
        ) from exc
    said, code = finished(process, deadline, "signing in to Google")
    if code != 0:
        raise refused(code, said, profile)


class Access:
    """A Google account Rundesk holds. This package sees a token for it and never a grant."""

    def __init__(self, profile: str, email: str) -> None:
        self.profile = profile
        self.wanted_email = email
        self.name = profile or "default"
        self.email = ""
        self._token = ""

    def token(self) -> str:
        """The one token this command uses, fetched once and kept only in memory."""
        if not self._token:
            self._token, self.email = managed_token(self.profile, self.wanted_email)
        return self._token


def listed(trouble: str) -> None:
    """Raise after a listing has been written, so the rows are seen and the exit is still earned.

    Written first and refused second on purpose: a person gets the table with the reason in it, and
    a script gets a non-zero exit instead of an empty account list it would have believed.
    """
    if trouble:
        raise AnalyticsError(trouble)


def managed_rows(profile: str) -> Tuple[List[Dict[str, Any]], str]:
    """What Rundesk holds for one app profile, and why the listing is incomplete when it is.

    **Two different answers wear the same shape, and only one of them is success.** "Nothing is
    connected yet" is a true, complete listing whose next step is a login, and it exits zero. "The
    bridge could not be reached, spoke a version this package cannot read, or sent a malformed
    list" is a listing that does not know what is connected — reported as a row so a person reading
    the table sees why, *and* as a refusal so a script does not read an empty table as an empty
    account list.
    """
    named = profile or "default"
    try:
        accounts = signed_in_accounts(profile)
    except AnalyticsError as exc:
        return [{"profile": named, "account": "", "status": str(exc)}], str(exc)
    if not accounts:
        return [{"profile": named, "account": "", "status": f"run: {login_command(profile)}"}], ""
    return [{"profile": named, "account": account, "status": "ready"}
            for account in accounts], ""


def selected_access(args: argparse.Namespace) -> Access:
    """The one Google account this command will use, named before anything is asked of Google."""
    profile = (getattr(args, "profile", "") or "").strip()
    if getattr(args, "auth", False):
        sign_in(profile)
    return Access(profile, (getattr(args, "email", "") or "").strip())


class RejectRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse redirects so credentials never cross an unexpected request boundary."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_url(request: urllib.request.Request, timeout: int = 30):
    return urllib.request.build_opener(RejectRedirectHandler()).open(request, timeout=timeout)


def expect_object(value: Any, noun: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise AnalyticsError(f"Google Analytics returned a malformed {noun}.")
    return value


def expect_objects(container: Dict[str, Any], key: str, noun: str) -> List[Dict[str, Any]]:
    items = container.get(key, [])
    if not isinstance(items, list):
        raise AnalyticsError(f"Google Analytics returned a malformed {noun} collection.")
    return [expect_object(item, noun) for item in items]


def decode_response(response: Any, noun: str = "API response") -> Dict[str, Any]:
    raw = response.read()
    if not raw:
        return {}
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AnalyticsError(f"Google Analytics returned a malformed {noun}: the body is not valid JSON.") from exc
    # A list or scalar body would otherwise surface as an attribute error several frames later.
    return expect_object(payload, noun)


#: The most of a refusal body that is ever read. Google's error payloads are short, and an
#: unbounded read here lets a remote party choose how much memory this command uses.
MAX_ERROR_BODY = 65536


def safe_error(exc: urllib.error.HTTPError) -> str:
    """Google's own reason for refusing, or the status when it did not give one.

    **Two shapes, and both are real.** A Google API error is `{"error": {"message": "..."}}`. An
    OAuth token endpoint error is `{"error": "invalid_grant", "error_description": "..."}` — a
    *string* where the other shape has an object. Reaching for `.get("message")` on that string
    raises `AttributeError`, and the handler that swallowed it reported `HTTP 400` for a revoked
    grant: true, and not the thing anybody needed to read. The body is read once, bounded, and
    closed, because an `HTTPError` read twice yields nothing the second time.
    """
    try:
        raw = exc.read(MAX_ERROR_BODY)
    except OSError:
        return f"HTTP {exc.code}"
    finally:
        exc.close()
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return f"HTTP {exc.code}"
    if not isinstance(body, dict):
        return f"HTTP {exc.code}"
    error = body.get("error")
    message = error.get("message") if isinstance(error, dict) else None
    if not message and isinstance(body.get("error_description"), str):
        message = body["error_description"]
    if not message and isinstance(error, str):
        message = error
    return str(message).strip() if message and str(message).strip() else f"HTTP {exc.code}"


def api_request(
    access_token: str,
    method: str,
    url: str,
    params: Optional[Dict[str, Any]] = None,
    payload: Optional[Dict[str, Any]] = None,
    retries: int = 2,
) -> Dict[str, Any]:
    if not any(url.startswith(base + "/") for base in (ADMIN_BASE, DATA_BASE, FUNNEL_DATA_BASE)):
        raise AnalyticsError("Refused an unexpected Google Analytics API origin.")
    if params:
        encoded = urllib.parse.urlencode(params, doseq=True)
        url += ("&" if "?" in url else "?") + encoded
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    for attempt in range(retries + 1):
        try:
            return decode_response(open_url(request))
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < retries:
                delay = min(8, 2**attempt)
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                if retry_after and retry_after.isdigit():
                    delay = min(30, int(retry_after))
                exc.read()
                time.sleep(delay)
                continue
            raise AnalyticsError(f"Google Analytics API request failed: {safe_error(exc)}.") from exc
        except urllib.error.URLError as exc:
            raise AnalyticsError(f"Google Analytics API request failed: {exc.reason}.") from exc
    raise AnalyticsError("Google Analytics API request failed.")


def resource_id(value: Any, prefix: str) -> str:
    if not isinstance(value, str):
        raise AnalyticsError(f"Expected a numeric {prefix[:-1]} ID, got {value!r}.")
    cleaned = value.strip()
    if cleaned.startswith(prefix + "/"):
        cleaned = cleaned.split("/", 1)[1]
    if NUMERIC_ID_RE.fullmatch(cleaned) is None:
        raise AnalyticsError(f"Expected a numeric {prefix[:-1]} ID, got {value!r}.")
    return cleaned


def bounded_limit(value: int, maximum: int = 10000) -> int:
    if value < 1 or value > maximum:
        raise AnalyticsError(f"--limit must be between 1 and {maximum}.")
    return value


def account_summaries(token: str, limit: int) -> Tuple[List[Dict[str, Any]], bool]:
    rows: List[Dict[str, Any]] = []
    page_token = ""
    truncated = False
    seen_tokens = set()
    for _ in range(MAX_PAGES):
        params: Dict[str, Any] = {"pageSize": min(200, limit - len(rows))}
        if page_token:
            params["pageToken"] = page_token
        response = api_request(token, "GET", f"{ADMIN_BASE}/accountSummaries", params=params)
        page = expect_objects(response, "accountSummaries", "account summary")
        remaining = limit - len(rows)
        if len(page) > remaining:
            truncated = True
        rows.extend(page[:remaining])
        next_token = response.get("nextPageToken", "")
        if not isinstance(next_token, str):
            raise AnalyticsError("Google Analytics returned a malformed page token.")
        if len(rows) >= limit:
            return rows, truncated or bool(next_token) or len(page) > remaining
        if not next_token:
            return rows, truncated
        if not page:
            return rows, True
        if next_token in seen_tokens:
            raise AnalyticsError("Google Analytics pagination did not advance.")
        seen_tokens.add(next_token)
        page_token = next_token
    raise AnalyticsError(f"Google Analytics pagination exceeded {MAX_PAGES} pages.")


def emit_csv(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> None:
    writer = csv.writer(sys.stdout, lineterminator="\n")
    writer.writerow(headers)
    writer.writerows(rows)


def emit_json(value: Any) -> None:
    json.dump(value, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def warn_truncated(truncated: bool, limit: int) -> None:
    if truncated:
        print(f"WARNING: Results were truncated at --limit {limit}.", file=sys.stderr)


def warn_possibly_truncated(possible: bool, limit: int) -> None:
    if possible:
        print(f"WARNING: Results may be truncated at --limit {limit}.", file=sys.stderr)


def response_row_count(response: Dict[str, Any], returned: int) -> int:
    value = response.get("rowCount", returned)
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise AnalyticsError("Google Analytics returned an invalid row count.") from exc


def command_profiles(args: argparse.Namespace) -> None:
    """Which Google accounts Rundesk holds for one OAuth app profile, without asking Google."""
    profile = (args.profile or "").strip()
    if args.auth:
        sign_in(profile)
    rows, trouble = managed_rows(profile)
    columns = ("profile", "account", "status")
    if args.json:
        emit_json(rows)
    else:
        emit_csv(columns, (tuple(row[column] for column in columns) for row in rows))
    listed(trouble)


def command_accounts(args: argparse.Namespace) -> None:
    access = selected_access(args)
    limit = bounded_limit(args.limit, 2000)
    token = access.token()
    summaries, truncated = account_summaries(token, limit)
    rows = [
        {
            "account_id": resource_id(item.get("account", ""), "accounts"),
            "display_name": item.get("displayName", ""),
            "property_count": len(expect_objects(item, "propertySummaries", "property summary")),
            "profile": access.name,
        }
        for item in summaries
    ]
    if args.json:
        emit_json(rows)
    else:
        emit_csv(("account_id", "display_name", "property_count", "profile"), ((r["account_id"], r["display_name"], r["property_count"], r["profile"]) for r in rows))
    warn_truncated(truncated, limit)


def command_properties(args: argparse.Namespace) -> None:
    access = selected_access(args)
    limit = bounded_limit(args.limit, 5000)
    account_filter = resource_id(args.account, "accounts") if args.account else ""
    token = access.token()
    # Account summaries are bounded separately; each returned account may carry many properties.
    summaries, account_truncated = account_summaries(token, 2000)
    rows: List[Dict[str, Any]] = []
    more_properties = False
    for account in summaries:
        account_id = resource_id(account.get("account", ""), "accounts")
        if account_filter and account_id != account_filter:
            continue
        for item in expect_objects(account, "propertySummaries", "property summary"):
            if len(rows) >= limit:
                more_properties = True
                break
            rows.append(
                {
                    "property_id": resource_id(item.get("property", ""), "properties"),
                    "display_name": item.get("displayName", ""),
                    "property_type": item.get("propertyType", ""),
                    "parent": item.get("parent", account.get("account", "")),
                    "account_id": account_id,
                    "profile": access.name,
                }
            )
    if args.json:
        emit_json(rows)
    else:
        emit_csv(("property_id", "display_name", "property_type", "parent", "account_id", "profile"), ((r["property_id"], r["display_name"], r["property_type"], r["parent"], r["account_id"], r["profile"]) for r in rows))
    if account_truncated:
        print("WARNING: Account discovery was truncated at 2000 accounts.", file=sys.stderr)
    warn_truncated(more_properties, limit)


def dimension_metric_names(args: argparse.Namespace) -> Tuple[List[str], List[str]]:
    dimensions = split_csv(args.dimensions or "")
    metrics = split_csv(args.metrics or "")
    if not metrics:
        raise AnalyticsError("At least one metric is required.")
    if len(dimensions) > MAX_DIMENSIONS or len(metrics) > MAX_METRICS:
        raise AnalyticsError(
            f"Google Analytics reports support at most {MAX_DIMENSIONS} dimensions and {MAX_METRICS} metrics."
        )
    for value in dimensions + metrics:
        if not valid_field_name(value):
            raise AnalyticsError(f"Invalid Analytics field name: {value!r}.")
    return dimensions, metrics


def valid_name_component(value: str, maximum: int = MAX_GA4_NAME) -> bool:
    """A Unicode letter-led GA identifier component without punctuation or whitespace.

    Unicode-aware because GA4 accepts non-English event, parameter, and user-property names, so an
    ASCII-only rule would refuse names Google itself collects.
    """
    return (
        isinstance(value, str)
        and 1 <= len(value) <= maximum
        and value[0].isalpha()
        and all(character.isalnum() or character == "_" for character in value[1:])
    )


def valid_field_name(value: str) -> bool:
    """A standard field name, or one of the Data API's documented custom field forms.

    **Only a documented family may carry a colon.** The suffix is not a free-form string Google
    happens to accept: each family declares what follows the colon, so `customEvent:` takes a
    parameter name, `userKeyEventRate:` an event name, and `sessionCustomChannelGroup:` a numeric
    channel ID — which a letter-led rule would refuse. Anything outside this table is rejected here
    rather than sent, so an unrecognized spelling cannot reach a request body as a field name.
    """
    if not isinstance(value, str):
        return False
    base, colon, suffix = value.partition(":")
    if BASE_FIELD_NAME_RE.fullmatch(base) is None:
        return False
    if not colon:
        return True
    kind = CUSTOM_FIELD_SUFFIXES.get(base)
    if kind is None:
        return False
    if kind == "channel":
        return NUMERIC_ID_RE.fullmatch(suffix) is not None
    # An event-scoped custom dimension registered before October 2020 is only addressable with its
    # event name in brackets, so that spelling is a documented form rather than a malformed one.
    if base == LEGACY_PARAMETER_FAMILY:
        legacy = LEGACY_PARAMETER_RE.fullmatch(suffix)
        if legacy is not None:
            return valid_name_component(legacy.group("parameter")) and valid_name_component(
                legacy.group("event")
            )
    return valid_name_component(suffix)


def normalized_report(response: Dict[str, Any], dimensions: List[str], metrics: List[str], access: Access, property_id: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for item in expect_objects(response, "rows", "report row"):
        dimension_values = expect_objects(item, "dimensionValues", "report dimension value")
        metric_values = expect_objects(item, "metricValues", "report metric value")
        result: Dict[str, Any] = {}
        for index, name in enumerate(dimensions):
            result[name] = dimension_values[index].get("value", "") if index < len(dimension_values) else ""
        for index, name in enumerate(metrics):
            result[name] = metric_values[index].get("value", "") if index < len(metric_values) else ""
        result["profile"] = access.name
        result["property_id"] = property_id
        rows.append(result)
    return rows


def emit_report(rows: List[Dict[str, Any]], dimensions: List[str], metrics: List[str], args: argparse.Namespace) -> None:
    headers = dimensions + metrics + ["profile", "property_id"]
    if args.json:
        emit_json(rows)
    else:
        emit_csv(headers, ([row.get(header, "") for header in headers] for row in rows))


def command_report(args: argparse.Namespace) -> None:
    access = selected_access(args)
    property_id = resource_id(args.property, "properties")
    limit = bounded_limit(args.limit)
    dimensions, metrics = dimension_metric_names(args)
    payload: Dict[str, Any] = {
        "dateRanges": [{"startDate": args.start_date, "endDate": args.end_date}],
        "metrics": [{"name": name} for name in metrics],
        "limit": str(limit),
    }
    if dimensions:
        payload["dimensions"] = [{"name": name} for name in dimensions]
    response = api_request(access.token(), "POST", f"{DATA_BASE}/properties/{property_id}:runReport", payload=payload)
    rows = normalized_report(response, dimensions, metrics, access, property_id)
    emit_report(rows, dimensions, metrics, args)
    processing_notices(args.end_date, metrics)
    report_notices(response, metrics)
    warn_truncated(response_row_count(response, len(rows)) > len(rows), limit)


def command_realtime(args: argparse.Namespace) -> None:
    access = selected_access(args)
    property_id = resource_id(args.property, "properties")
    limit = bounded_limit(args.limit, 250000)
    dimensions, metrics = dimension_metric_names(args)
    payload: Dict[str, Any] = {"metrics": [{"name": name} for name in metrics], "limit": str(limit)}
    if dimensions:
        payload["dimensions"] = [{"name": name} for name in dimensions]
    response = api_request(access.token(), "POST", f"{DATA_BASE}/properties/{property_id}:runRealtimeReport", payload=payload)
    rows = normalized_report(response, dimensions, metrics, access, property_id)
    emit_report(rows, dimensions, metrics, args)
    warn_truncated(response_row_count(response, len(rows)) > len(rows), limit)


# --- Bounded traffic, audience, key-event, and commerce reporting -------------------
#
# Every dimension and metric below is a current GA4 Data API v1beta name taken from
# Google's own predefined report definitions and schema. GA4 renamed conversions to
# key events in May 2024, so this package uses `isKeyEvent` and `keyEvents` only.
# These commands report what a property already collects; a property that never sent
# ecommerce or key events returns empty rows rather than an error.

# ASCII digits and a bounded day count, for the same reason resource IDs are: `\d` also matches
# Unicode digits Google cannot read, and an unbounded run of them is a caller-chosen request size.
DATE_FORM_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}|today|yesterday|[0-9]{1,5}daysAgo")
DAYS_AGO_RE = re.compile(r"([0-9]{1,5})daysAgo")
MAX_EVENT_FILTER_VALUES = 25
TRAFFIC_SEGMENT_CHOICES = ("all", "organic-search", "google-organic")

TRAFFIC_METRICS = (
    "sessions",
    "activeUsers",
    "newUsers",
    "engagedSessions",
    "engagementRate",
    "averageEngagementTimePerSession",
    "keyEvents",
    "totalRevenue",
)
# Google's own demographic and technology reports pair these metrics with these
# dimensions, so a user-scoped breakdown such as age stays within a combination
# Google already publishes.
AUDIENCE_METRICS = (
    "activeUsers",
    "newUsers",
    "engagedSessions",
    "engagementRate",
    "eventCount",
    "keyEvents",
    "totalRevenue",
)
KEY_EVENT_METRICS = ("keyEvents", "eventCount", "activeUsers", "totalRevenue")
LEAD_LIFECYCLE_METRICS = ("eventCount", "activeUsers", "sessions")
LEAD_LIFECYCLE_EVENTS = (
    "generate_lead",
    "working_lead",
    "qualify_lead",
    "disqualify_lead",
    "close_convert_lead",
    "close_unconvert_lead",
)
ITEM_METRICS = (
    "itemsViewed",
    "itemsAddedToCart",
    "itemsCheckedOut",
    "itemsPurchased",
    "cartToViewRate",
    "purchaseToViewRate",
    "grossItemRevenue",
    "itemRefundAmount",
    "itemRevenue",
)
PURCHASE_METRICS = (
    "ecommercePurchases",
    "grossPurchaseRevenue",
    "refundAmount",
    "purchaseRevenue",
    "totalRevenue",
    "totalPurchasers",
    "purchaserRate",
)
REVENUE_METRICS = frozenset({
    "totalRevenue", "grossPurchaseRevenue", "refundAmount", "purchaseRevenue",
    "grossItemRevenue", "itemRefundAmount", "itemRevenue",
})
DERIVED_METRIC_EXPRESSIONS = {
    "averageEngagementTimePerSession": "userEngagementDuration/sessions",
}

# Acquisition dimensions are paired with an explicit scope because session-scoped and
# first-user-scoped attribution answer different questions and are separate API names.
TRAFFIC_BREAKDOWN_CHOICES = ("channel", "source", "medium", "source-medium", "campaign", "landing-page", "date")
TRAFFIC_SCOPE_CHOICES = ("session", "first-user")
TRAFFIC_DIMENSIONS = {
    ("channel", "session"): ("sessionDefaultChannelGroup",),
    ("channel", "first-user"): ("firstUserDefaultChannelGroup",),
    ("source", "session"): ("sessionSource",),
    ("source", "first-user"): ("firstUserSource",),
    ("medium", "session"): ("sessionMedium",),
    ("medium", "first-user"): ("firstUserMedium",),
    ("source-medium", "session"): ("sessionSource", "sessionMedium"),
    ("source-medium", "first-user"): ("firstUserSource", "firstUserMedium"),
    ("campaign", "session"): ("sessionCampaignName",),
    ("campaign", "first-user"): ("firstUserCampaignName",),
    ("landing-page", "session"): ("landingPage",),
    ("date", "session"): ("date",),
}

AUDIENCE_BREAKDOWN_CHOICES = (
    "audience", "country", "region", "city", "language", "device", "browser", "operating-system", "platform", "age", "gender",
)
AUDIENCE_DIMENSIONS = {
    "audience": ("audienceName",),
    "country": ("country",),
    "region": ("region",),
    "city": ("city",),
    "language": ("language",),
    "device": ("deviceCategory",),
    "browser": ("browser",),
    "operating-system": ("operatingSystem",),
    "platform": ("platform",),
    "age": ("userAgeBracket",),
    "gender": ("userGender",),
}
# Google withholds small groups for these dimensions, so a caller must not read a
# short result as the property's whole audience.
THRESHOLDED_BREAKDOWNS = frozenset({"age", "gender"})

KEY_EVENT_BREAKDOWN_CHOICES = ("event", "date", "channel")
KEY_EVENT_DIMENSIONS = {
    "event": ("eventName",),
    "date": ("date",),
    "channel": ("sessionDefaultChannelGroup",),
}
LEAD_LIFECYCLE_DIMENSIONS = KEY_EVENT_DIMENSIONS

# Only ecommerce ships as a funnel: Google publishes that step order with the events. The
# recommended-events table names the lead lifecycle events but no order for them, so a lead
# funnel would encode an ordering Google never stated. `lead-lifecycle` reports them unordered.
FUNNEL_CHOICES = ("ecommerce",)
FUNNEL_STEPS = {
    "ecommerce": (
        ("View item", "view_item"),
        ("Add to cart", "add_to_cart"),
        ("Begin checkout", "begin_checkout"),
        ("Purchase", "purchase"),
    ),
}

COMMERCE_BREAKDOWN_CHOICES = ("item", "item-id", "brand", "category", "list", "date", "channel")
COMMERCE_DIMENSIONS = {
    "item": ("itemName",),
    "item-id": ("itemId",),
    "brand": ("itemBrand",),
    "category": ("itemCategory",),
    "list": ("itemListName",),
    "date": ("date",),
    "channel": ("sessionDefaultChannelGroup",),
}
# Item-scoped metrics only combine with item-scoped dimensions, so a product breakdown
# and a purchase breakdown carry different metric sets by construction.
COMMERCE_ITEM_BREAKDOWNS = frozenset({"item", "item-id", "brand", "category", "list"})


@dataclass(frozen=True)
class Breakdown:
    """One bounded report shape: what to group by, what to measure, and how to rank."""

    dimensions: Tuple[str, ...]
    metrics: Tuple[str, ...]
    order_metric: str = ""
    purchase_metric: str = ""


def build_breakdown(
    dimensions: Tuple[str, ...],
    metrics: Tuple[str, ...],
    order_metric: str,
    purchase_metric: str = "",
) -> Breakdown:
    # A day-by-day breakdown reads as a time series, so it sorts by date instead of size.
    ranked_by = "" if dimensions[0] == "date" else order_metric
    return Breakdown(dimensions, metrics, ranked_by, purchase_metric)


def bounded_date(value: str, option: str) -> str:
    """Accept only the date forms the Data API's DateRange documents."""
    if not DATE_FORM_RE.fullmatch(value or ""):
        raise AnalyticsError(
            f"{option} must be YYYY-MM-DD, today, yesterday, or NdaysAgo, got {value!r}."
        )
    return value


def validated_fields(dimensions: Sequence[str], metrics: Sequence[str]) -> None:
    """Guard the request the same way caller-supplied report fields are guarded."""
    if not metrics:
        raise AnalyticsError("At least one metric is required.")
    if len(dimensions) > MAX_DIMENSIONS or len(metrics) > MAX_METRICS:
        raise AnalyticsError(
            f"Google Analytics reports support at most {MAX_DIMENSIONS} dimensions and {MAX_METRICS} metrics."
        )
    for value in list(dimensions) + list(metrics):
        if not valid_field_name(value):
            raise AnalyticsError(f"Invalid Analytics field name: {value!r}.")


def metric_requests(metrics: Sequence[str]) -> List[Dict[str, str]]:
    """Build bounded metric fields, including Google's documented derived metrics."""
    requests = []
    for name in metrics:
        metric = {"name": name}
        expression = DERIVED_METRIC_EXPRESSIONS.get(name)
        if expression is not None:
            metric["expression"] = expression
        requests.append(metric)
    return requests


def key_event_filter() -> Dict[str, Any]:
    """Restrict a report to events the property marks as key events."""
    return {"filter": {"fieldName": "isKeyEvent", "stringFilter": {"matchType": "EXACT", "value": "true"}}}


def event_name_filter(names: Sequence[str]) -> Dict[str, Any]:
    """Restrict a report to named events, matched exactly because GA4 event names are case sensitive."""
    if not names:
        raise AnalyticsError("--event needs at least one GA4 event name.")
    if len(names) > MAX_EVENT_FILTER_VALUES:
        raise AnalyticsError(f"--event accepts at most {MAX_EVENT_FILTER_VALUES} event names.")
    for name in names:
        if not valid_name_component(name, maximum=40):
            raise AnalyticsError(
                f"Invalid GA4 event name: {name!r}. Event names start with a letter and use letters, digits, or underscores."
            )
    return {"filter": {"fieldName": "eventName", "inListFilter": {"values": list(names), "caseSensitive": True}}}


def all_of(expressions: List[Dict[str, Any]]) -> Dict[str, Any]:
    if len(expressions) == 1:
        return expressions[0]
    return {"andGroup": {"expressions": expressions}}


def traffic_segment_filter(segment: str, scope: str = "session") -> Optional[Dict[str, Any]]:
    """A fixed acquisition subset; arbitrary caller-supplied filters stay out of bounded reports."""
    if segment == "all":
        return None
    prefix = "firstUser" if scope == "first-user" else "session"
    if segment == "organic-search":
        return {
            "filter": {
                "fieldName": prefix + "DefaultChannelGroup",
                "stringFilter": {"matchType": "EXACT", "value": "Organic Search"},
            }
        }
    if segment == "google-organic":
        return all_of([
            {
                "filter": {
                    "fieldName": prefix + "Source",
                    "stringFilter": {"matchType": "EXACT", "value": "google"},
                }
            },
            {
                "filter": {
                    "fieldName": prefix + "Medium",
                    "stringFilter": {"matchType": "EXACT", "value": "organic"},
                }
            },
        ])
    raise AnalyticsError(f"Unsupported traffic segment: {segment!r}.")


def combine_filters(*filters: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    expressions = [one for one in filters if one is not None]
    return all_of(expressions) if expressions else None


def positive_metric_filter(metric: str) -> Dict[str, Any]:
    """Drop rows that recorded none of the measured purchases."""
    return {
        "filter": {
            "fieldName": metric,
            "numericFilter": {"operation": "GREATER_THAN", "value": {"int64Value": "0"}},
        }
    }


def order_bys(breakdown: Breakdown) -> List[Dict[str, Any]]:
    if breakdown.order_metric:
        return [{"metric": {"metricName": breakdown.order_metric}, "desc": True}]
    return [{"dimension": {"dimensionName": breakdown.dimensions[0]}, "desc": False}]


def report_notices(response: Dict[str, Any], metrics: Sequence[str]) -> None:
    """Repeat Google's own caveats so a bounded row set is not read as the whole truth."""
    metadata = expect_object(response.get("metadata", {}), "report metadata")
    if metadata.get("subjectToThresholding"):
        print(
            "WARNING: Google withheld rows below its aggregation thresholds; small groups are missing.",
            file=sys.stderr,
        )
    if metadata.get("dataLossFromOtherRow"):
        print('WARNING: Google rolled low-volume rows into an "(other)" row.', file=sys.stderr)
    if expect_objects(metadata, "samplingMetadatas", "sampling metadata"):
        print("WARNING: Google sampled this report; values are estimates.", file=sys.stderr)
    reason = metadata.get("emptyReason")
    if isinstance(reason, str) and reason:
        print(f"NOTE: Google returned no rows: {reason}", file=sys.stderr)
    currency = metadata.get("currencyCode")
    if isinstance(currency, str) and currency and any(metric in REVENUE_METRICS for metric in metrics):
        print(f"NOTE: Revenue is reported in {currency}.", file=sys.stderr)


def ends_today(end_date: str) -> bool:
    """Whether a window runs to today under any spelling the Data API's DateRange documents.

    **`NdaysAgo` counts back from today, so `0daysAgo` is today under another name** and carries
    exactly the same processing caveat. Matching only the literal `today` left the freshest window
    a caller can ask for as the one window that went out unwarned.

    A literal `YYYY-MM-DD` that happens to be today is deliberately not resolved: "today" is today
    in the property's own reporting time zone, which this command never learns, so guessing it from
    the local clock would warn — or stay silent — on the wrong day either side of the boundary.
    """
    if end_date == "today":
        return True
    days = DAYS_AGO_RE.fullmatch(end_date or "")
    return days is not None and int(days.group(1)) == 0


def processing_notices(end_date: str, metrics: Sequence[str]) -> None:
    """State temporal limits that Google cannot encode in report response metadata."""
    if ends_today(end_date):
        print(
            "NOTE: Today's data can be incomplete while Google Analytics processes events; "
            "standard reports can take 24–48 hours to settle.",
            file=sys.stderr,
        )
    if "keyEvents" in metrics:
        print(
            "NOTE: Attributed and modeled key-event results can change for up to 12 days.",
            file=sys.stderr,
        )


def run_breakdown_report(
    args: argparse.Namespace,
    breakdown: Breakdown,
    dimension_filter: Optional[Dict[str, Any]] = None,
    metric_filter: Optional[Dict[str, Any]] = None,
    notes: Sequence[str] = (),
) -> None:
    access = selected_access(args)
    property_id = resource_id(args.property, "properties")
    limit = bounded_limit(args.limit)
    dimensions = list(breakdown.dimensions)
    metrics = list(breakdown.metrics)
    validated_fields(dimensions, metrics)
    payload: Dict[str, Any] = {
        "dateRanges": [
            {
                "startDate": bounded_date(args.start_date, "--start-date"),
                "endDate": bounded_date(args.end_date, "--end-date"),
            }
        ],
        "dimensions": [{"name": name} for name in dimensions],
        "metrics": metric_requests(metrics),
        "orderBys": order_bys(breakdown),
        "limit": str(limit),
    }
    if dimension_filter is not None:
        payload["dimensionFilter"] = dimension_filter
    if metric_filter is not None:
        payload["metricFilter"] = metric_filter
    response = api_request(
        access.token(),
        "POST",
        f"{DATA_BASE}/properties/{property_id}:runReport",
        payload=payload,
    )
    rows = normalized_report(response, dimensions, metrics, access, property_id)
    emit_report(rows, dimensions, metrics, args)
    for note in notes:
        print(f"NOTE: {note}", file=sys.stderr)
    processing_notices(args.end_date, metrics)
    report_notices(response, metrics)
    warn_truncated(response_row_count(response, len(rows)) > len(rows), limit)


def command_traffic(args: argparse.Namespace) -> None:
    dimensions = TRAFFIC_DIMENSIONS.get((args.breakdown, args.scope))
    if dimensions is None:
        raise AnalyticsError(
            f"--breakdown {args.breakdown} has no {args.scope} form; run it with --scope session."
        )
    run_breakdown_report(
        args,
        build_breakdown(dimensions, TRAFFIC_METRICS, "sessions"),
        dimension_filter=traffic_segment_filter(getattr(args, "segment", "all"), args.scope),
    )


def command_audience(args: argparse.Namespace) -> None:
    breakdown = build_breakdown(AUDIENCE_DIMENSIONS[args.breakdown], AUDIENCE_METRICS, "activeUsers")
    notes = ()
    if args.breakdown in THRESHOLDED_BREAKDOWNS:
        notes = (
            "Google applies aggregation thresholds to age and gender, and reports them only for "
            "properties that enabled Google signals.",
        )
    run_breakdown_report(
        args,
        breakdown,
        dimension_filter=traffic_segment_filter(getattr(args, "segment", "all")),
        notes=notes,
    )


def command_key_events(args: argparse.Namespace) -> None:
    breakdown = build_breakdown(KEY_EVENT_DIMENSIONS[args.breakdown], KEY_EVENT_METRICS, "keyEvents")
    expressions = [key_event_filter()]
    if args.event is not None:
        expressions.append(event_name_filter(split_csv(args.event)))
    run_breakdown_report(
        args,
        breakdown,
        dimension_filter=combine_filters(
            all_of(expressions),
            traffic_segment_filter(getattr(args, "segment", "all")),
        ),
    )


def command_lead_lifecycle(args: argparse.Namespace) -> None:
    """Report Google's recommended lead states even when they are not property key events."""
    breakdown = build_breakdown(
        LEAD_LIFECYCLE_DIMENSIONS[args.breakdown], LEAD_LIFECYCLE_METRICS, "eventCount"
    )
    run_breakdown_report(
        args,
        breakdown,
        dimension_filter=combine_filters(
            event_name_filter(LEAD_LIFECYCLE_EVENTS),
            traffic_segment_filter(getattr(args, "segment", "all")),
        ),
        notes=(
            "Lead lifecycle rows reflect events received by Analytics, not CRM truth; reconcile "
            "them to the lead system of record.",
        ),
    )


def command_commerce(args: argparse.Namespace) -> None:
    if args.breakdown in COMMERCE_ITEM_BREAKDOWNS:
        breakdown = build_breakdown(
            COMMERCE_DIMENSIONS[args.breakdown], ITEM_METRICS, "itemRevenue", "itemsPurchased"
        )
    else:
        breakdown = build_breakdown(
            COMMERCE_DIMENSIONS[args.breakdown], PURCHASE_METRICS, "purchaseRevenue", "ecommercePurchases"
        )
    metric_filter = positive_metric_filter(breakdown.purchase_metric) if args.purchased_only else None
    run_breakdown_report(
        args,
        breakdown,
        dimension_filter=traffic_segment_filter(getattr(args, "segment", "all")),
        metric_filter=metric_filter,
    )


def metadata_rows(response: Dict[str, Any], kind: str, query: str) -> List[Dict[str, Any]]:
    """Normalize property-specific fields without hiding compatibility or deprecation metadata."""
    wanted = (query or "").casefold()
    groups = ("dimensions", "metrics") if kind == "all" else (kind,)
    rows: List[Dict[str, Any]] = []
    for group in groups:
        singular = group[:-1]
        for item in expect_objects(response, group, f"{singular} metadata"):
            api_name = item.get("apiName", "")
            if not isinstance(api_name, str) or not api_name:
                raise AnalyticsError(f"Google Analytics returned malformed {singular} metadata.")
            searchable = " ".join(
                str(item.get(field, "")) for field in ("apiName", "uiName", "description", "category")
            ).casefold()
            if wanted and wanted not in searchable:
                continue
            deprecated = item.get("deprecatedApiNames", [])
            blocked = item.get("blockedReasons", [])
            if not isinstance(deprecated, list) or not all(isinstance(one, str) for one in deprecated):
                raise AnalyticsError(f"Google Analytics returned malformed {singular} metadata.")
            if not isinstance(blocked, list) or not all(isinstance(one, str) for one in blocked):
                raise AnalyticsError(f"Google Analytics returned malformed {singular} metadata.")
            rows.append({
                "kind": singular,
                "api_name": api_name,
                "ui_name": item.get("uiName", ""),
                "category": item.get("category", ""),
                "type": item.get("type", ""),
                "custom_definition": bool(item.get("customDefinition", False)),
                "deprecated_api_names": ",".join(deprecated),
                "blocked_reasons": ",".join(blocked),
                "description": item.get("description", ""),
            })
    return rows


def command_metadata(args: argparse.Namespace) -> None:
    access = selected_access(args)
    property_id = resource_id(args.property, "properties")
    limit = bounded_limit(args.limit)
    response = api_request(
        access.token(), "GET", f"{DATA_BASE}/properties/{property_id}/metadata"
    )
    available = metadata_rows(response, args.kind, args.query)
    rows = available[:limit]
    for row in rows:
        row["profile"] = access.name
        row["property_id"] = property_id
    headers = (
        "kind", "api_name", "ui_name", "category", "type", "custom_definition",
        "deprecated_api_names", "blocked_reasons", "description", "profile", "property_id",
    )
    if args.json:
        emit_json(rows)
    else:
        emit_csv(headers, ([row.get(header, "") for header in headers] for row in rows))
    warn_truncated(len(available) > len(rows), limit)


def compatibility_rows(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for kind, collection, metadata_key in (
        ("dimension", "dimensionCompatibilities", "dimensionMetadata"),
        ("metric", "metricCompatibilities", "metricMetadata"),
    ):
        for item in expect_objects(response, collection, f"{kind} compatibility"):
            metadata = expect_object(item.get(metadata_key, {}), f"{kind} metadata")
            api_name = metadata.get("apiName", "")
            compatibility = item.get("compatibility", "")
            if not isinstance(api_name, str) or not api_name or compatibility not in (
                "COMPATIBLE", "INCOMPATIBLE", "COMPATIBILITY_UNSPECIFIED"
            ):
                raise AnalyticsError(f"Google Analytics returned malformed {kind} compatibility.")
            rows.append({
                "kind": kind,
                "api_name": api_name,
                "compatibility": compatibility,
                "ui_name": metadata.get("uiName", ""),
                "description": metadata.get("description", ""),
            })
    return rows


def compatibility_notice() -> None:
    """Say what the rows answer, because they are not a verdict on the fields that were sent.

    Google's method "lists dimensions and metrics that can be added to a report request and
    maintain compatibility" and "fails if the request's dimensions and metrics are incompatible".
    Two consequences a caller cannot see in the rows: each row rates a *candidate* field against
    the supplied context rather than the supplied combination, and a response arriving at all
    already means that starting set was compatible. Google also documents that this method checks
    Core reports only, and that Realtime reports have different compatibility rules.
    """
    print(
        "NOTE: Each row says whether that field can be added to the supplied Core report context, "
        "not whether the supplied combination is valid; Google refuses the whole request when the "
        "supplied fields are already incompatible, so a returned table means they were not. "
        "Realtime reports follow different compatibility rules and are not covered.",
        file=sys.stderr,
    )


def command_compatibility(args: argparse.Namespace) -> None:
    access = selected_access(args)
    property_id = resource_id(args.property, "properties")
    limit = bounded_limit(args.limit)
    dimensions, metrics = dimension_metric_names(args)
    payload: Dict[str, Any] = {
        "dimensions": [{"name": name} for name in dimensions],
        "metrics": [{"name": name} for name in metrics],
    }
    if args.compatibility != "all":
        payload["compatibilityFilter"] = args.compatibility.upper()
    response = api_request(
        access.token(),
        "POST",
        f"{DATA_BASE}/properties/{property_id}:checkCompatibility",
        payload=payload,
    )
    available = compatibility_rows(response)
    rows = available[:limit]
    for row in rows:
        row["profile"] = access.name
        row["property_id"] = property_id
    headers = (
        "kind", "api_name", "compatibility", "ui_name", "description", "profile", "property_id",
    )
    if args.json:
        emit_json(rows)
    else:
        emit_csv(headers, ([row.get(header, "") for header in headers] for row in rows))
    compatibility_notice()
    warn_truncated(len(available) > len(rows), limit)


def funnel_step(name: str, event_name: str) -> Dict[str, Any]:
    return {
        "name": name,
        "filterExpression": {"funnelEventFilter": {"eventName": event_name}},
    }


def normalized_funnel_table(
    response: Dict[str, Any], access: Access, property_id: str
) -> Tuple[List[str], List[str], List[Dict[str, Any]], Dict[str, Any]]:
    table = expect_object(response.get("funnelTable", {}), "funnel table")
    dimension_headers = expect_objects(table, "dimensionHeaders", "funnel dimension header")
    metric_headers = expect_objects(table, "metricHeaders", "funnel metric header")
    dimensions = [item.get("name", "") for item in dimension_headers]
    metrics = [item.get("name", "") for item in metric_headers]
    if not dimensions or not metrics or not all(
        isinstance(name, str) and name for name in dimensions + metrics
    ):
        raise AnalyticsError("Google Analytics returned malformed funnel headers.")
    rows = normalized_report(table, dimensions, metrics, access, property_id)
    metadata = expect_object(table.get("metadata", {}), "funnel metadata")
    return dimensions, metrics, rows, metadata


def command_funnel(args: argparse.Namespace) -> None:
    """Run one fixed, closed, ordered funnel made from Google's recommended events."""
    access = selected_access(args)
    property_id = resource_id(args.property, "properties")
    limit = bounded_limit(args.limit, 250000)
    payload: Dict[str, Any] = {
        "dateRanges": [{
            "startDate": bounded_date(args.start_date, "--start-date"),
            "endDate": bounded_date(args.end_date, "--end-date"),
        }],
        "funnel": {
            "isOpenFunnel": False,
            "steps": [funnel_step(name, event) for name, event in FUNNEL_STEPS[args.funnel]],
        },
        "limit": str(limit),
    }
    segment = traffic_segment_filter(getattr(args, "segment", "all"))
    if segment is not None:
        payload["dimensionFilter"] = segment
    response = api_request(
        access.token(),
        "POST",
        f"{FUNNEL_DATA_BASE}/properties/{property_id}:runFunnelReport",
        payload=payload,
    )
    dimensions, metrics, rows, metadata = normalized_funnel_table(response, access, property_id)
    emit_report(rows, dimensions, metrics, args)
    processing_notices(args.end_date, ())
    if expect_objects(metadata, "samplingMetadatas", "sampling metadata"):
        print("WARNING: Google sampled this funnel; values are estimates.", file=sys.stderr)
    print("NOTE: Funnel reporting uses Google's alpha Data API surface.", file=sys.stderr)
    warn_possibly_truncated(len(rows) >= limit, limit)


def add_profile_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", help="Which Google OAuth app profile Rundesk signed in with")
    parser.add_argument("--email", help="Which signed-in Google account to use when Rundesk holds more than one")
    parser.add_argument("--auth", action="store_true", help="Run `rundesk login google` first, with --profile when given")
    parser.add_argument("--json", action="store_true", help="Emit normalized JSON")


def add_report_window(parser: argparse.ArgumentParser, default_limit: int) -> None:
    parser.add_argument("--property", required=True, help="Numeric GA4 property ID")
    parser.add_argument("--start-date", default="28daysAgo", help="YYYY-MM-DD, today, yesterday, or NdaysAgo")
    parser.add_argument("--end-date", default="today", help="YYYY-MM-DD, today, yesterday, or NdaysAgo")
    parser.add_argument("--limit", type=int, default=default_limit)


def add_traffic_segment(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--segment",
        choices=TRAFFIC_SEGMENT_CHOICES,
        default="all",
        help="All traffic, all Organic Search, or specifically google / organic",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="google-analytics", description="Read bounded Google Analytics 4 data.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    profiles = subparsers.add_parser("profiles", help="List the Google accounts Rundesk holds, without a network request")
    profiles.add_argument("--profile", help="OAuth app profile to list Rundesk's signed-in accounts for")
    profiles.add_argument("--auth", action="store_true", help="Run `rundesk login google` first, with --profile when given")
    profiles.add_argument("--json", action="store_true", help="Emit normalized JSON")
    profiles.set_defaults(handler=command_profiles)

    accounts = subparsers.add_parser("accounts", help="List accessible Analytics accounts")
    add_profile_option(accounts)
    accounts.add_argument("--limit", type=int, default=25)
    accounts.set_defaults(handler=command_accounts)

    properties = subparsers.add_parser("properties", help="List accessible GA4 properties")
    add_profile_option(properties)
    properties.add_argument("--account", help="Restrict results to one numeric account ID")
    properties.add_argument("--limit", type=int, default=50)
    properties.set_defaults(handler=command_properties)

    report = subparsers.add_parser("report", help="Run a bounded historical GA4 report")
    add_profile_option(report)
    report.add_argument("--property", required=True, help="Numeric GA4 property ID")
    report.add_argument("--start-date", default="28daysAgo")
    report.add_argument("--end-date", default="today")
    report.add_argument("--metrics", default="sessions,activeUsers")
    report.add_argument("--dimensions", default="date")
    report.add_argument("--limit", type=int, default=100)
    report.set_defaults(handler=command_report)

    realtime = subparsers.add_parser("realtime", help="Run a bounded realtime GA4 report")
    add_profile_option(realtime)
    realtime.add_argument("--property", required=True, help="Numeric GA4 property ID")
    realtime.add_argument("--metrics", default="activeUsers")
    realtime.add_argument("--dimensions", default="")
    realtime.add_argument("--limit", type=int, default=25)
    realtime.set_defaults(handler=command_realtime)

    traffic = subparsers.add_parser("traffic", help="Report where sessions came from")
    add_profile_option(traffic)
    add_report_window(traffic, 25)
    traffic.add_argument("--breakdown", choices=TRAFFIC_BREAKDOWN_CHOICES, default="channel")
    traffic.add_argument(
        "--scope", choices=TRAFFIC_SCOPE_CHOICES, default="session",
        help="Group by session acquisition or by the user's first acquisition",
    )
    add_traffic_segment(traffic)
    traffic.set_defaults(handler=command_traffic)

    audience = subparsers.add_parser("audience", help="Report aggregated audience, geography, and technology")
    add_profile_option(audience)
    add_report_window(audience, 25)
    audience.add_argument("--breakdown", choices=AUDIENCE_BREAKDOWN_CHOICES, default="country")
    add_traffic_segment(audience)
    audience.set_defaults(handler=command_audience)

    key_events = subparsers.add_parser("key-events", help="Report key events, the GA4 name for conversions and leads")
    add_profile_option(key_events)
    add_report_window(key_events, 25)
    key_events.add_argument("--breakdown", choices=KEY_EVENT_BREAKDOWN_CHOICES, default="event")
    key_events.add_argument("--event", help="Comma-separated GA4 event names to isolate")
    add_traffic_segment(key_events)
    key_events.set_defaults(handler=command_key_events)

    lead_lifecycle = subparsers.add_parser(
        "lead-lifecycle",
        help="Report Google's recommended lead lifecycle and disposition events",
    )
    add_profile_option(lead_lifecycle)
    add_report_window(lead_lifecycle, 25)
    lead_lifecycle.add_argument(
        "--breakdown", choices=KEY_EVENT_BREAKDOWN_CHOICES, default="event"
    )
    add_traffic_segment(lead_lifecycle)
    lead_lifecycle.set_defaults(handler=command_lead_lifecycle)

    commerce = subparsers.add_parser("commerce", help="Report ecommerce item, purchase, and revenue behavior")
    add_profile_option(commerce)
    add_report_window(commerce, 25)
    commerce.add_argument("--breakdown", choices=COMMERCE_BREAKDOWN_CHOICES, default="item")
    commerce.add_argument(
        "--purchased-only", action="store_true", help="Drop rows with no purchase in the window",
    )
    add_traffic_segment(commerce)
    commerce.set_defaults(handler=command_commerce)

    funnel = subparsers.add_parser(
        "funnel", help="Run the fixed ordered ecommerce funnel"
    )
    add_profile_option(funnel)
    add_report_window(funnel, 25)
    funnel.add_argument("--funnel", choices=FUNNEL_CHOICES, default="ecommerce")
    add_traffic_segment(funnel)
    funnel.set_defaults(handler=command_funnel)

    metadata = subparsers.add_parser(
        "metadata", help="List report fields available to one GA4 property"
    )
    add_profile_option(metadata)
    metadata.add_argument("--property", required=True, help="Numeric GA4 property ID")
    metadata.add_argument("--kind", choices=("all", "dimensions", "metrics"), default="all")
    metadata.add_argument("--query", default="", help="Case-insensitive name or description search")
    metadata.add_argument("--limit", type=int, default=100)
    metadata.set_defaults(handler=command_metadata)

    compatibility = subparsers.add_parser(
        "compatibility",
        help="List Core report fields that can be added to the supplied dimensions and metrics",
        description=(
            "List the Core report dimensions and metrics that can be added to the supplied fields "
            "while staying compatible. The supplied fields are the starting report context, not "
            "the thing being rated: Google refuses the request outright when they are already "
            "incompatible with each other. Realtime reports have different rules."
        ),
    )
    add_profile_option(compatibility)
    compatibility.add_argument("--property", required=True, help="Numeric GA4 property ID")
    compatibility.add_argument(
        "--metrics", default="sessions,activeUsers",
        help="Metrics of the Core report to use as the starting context",
    )
    compatibility.add_argument(
        "--dimensions", default="date",
        help="Dimensions of the Core report to use as the starting context",
    )
    compatibility.add_argument(
        "--compatibility", choices=("all", "compatible", "incompatible"), default="all",
        help="Restrict the candidate fields Google returns to one compatibility",
    )
    compatibility.add_argument("--limit", type=int, default=25)
    compatibility.set_defaults(handler=command_compatibility)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.handler(args)
    except AnalyticsError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
