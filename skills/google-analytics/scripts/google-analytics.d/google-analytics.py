#!/usr/bin/env python3
"""Read bounded Google Analytics 4 account, traffic, audience, key-event, commerce, and realtime data."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
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
FIELD_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
MAX_PAGES = 100
MAX_DIMENSIONS = 9
MAX_METRICS = 10


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
# `_google` bridge, version 1. Rundesk refuses a pipe, a named socket, a regular file, and 0, 1, 2.
COMMAND_VARIABLE = "RUNDESK_COMMAND"
# Which fixed Google scope Rundesk attaches to a token for this package. Rundesk maps the name; the
# scope itself is never chosen here.
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
DIAGNOSTIC_FD = 2
# A Rundesk released before this bridge answers as argparse does: exit 2, naming the choice it did
# not recognize. Rundesk's own refusals exit 1, so the two are never mistaken for each other.
UNSUPPORTED_EXIT = 2
UNSUPPORTED_MARKS = ("invalid choice: '_google'", "invalid choice: 'login'",
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
    if payload.get("ok") is not True:
        raise AnalyticsError("Rundesk refused the Google request.")
    return payload


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


def refused(code: int, said: str, profile: str, trouble: str = "") -> AnalyticsError:
    if unsupported(code, said):
        return AnalyticsError(
            "This Rundesk install is older than Rundesk-managed Google sign-in. Update Rundesk, "
            f"then run: {login_command(profile)}"
        )
    reason = bridge_reason(said) or trouble or f"rundesk exited {code}"
    return AnalyticsError(
        f"Rundesk did not grant Google access: {reason}. Run: {login_command(profile)}"
    )


def finished(process: subprocess.Popen, deadline: float, doing: str) -> Tuple[str, int]:
    """What Rundesk said and how it ended, never leaving a child of this command behind.

    A child still running at the deadline is killed and reaped here, so no command of this package
    can be held open by one that stopped answering.
    """
    try:
        _, said = process.communicate(timeout=max(0.0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise AnalyticsError(f"Rundesk did not finish {doing} in time, and was stopped.")
    return said or "", process.returncode


def ask_rundesk(action: List[str], profile: str, seconds: Optional[float] = None) -> Dict[str, Any]:
    """One `_google` answer, read from a socket pair no other process holds an end of."""
    command = rundesk_command()
    # One deadline for the whole transaction: spawning, reading the frame, and waiting for the exit
    # share it, so a child that stalls in any one phase cannot extend the others.
    deadline = time.monotonic() + (BRIDGE_SECONDS if seconds is None else seconds)
    ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        try:
            process = subprocess.Popen(
                [command, "_google"] + action + ["--response-fd", str(theirs.fileno())],
                stdin=subprocess.DEVNULL, stdout=DIAGNOSTIC_FD, stderr=subprocess.PIPE,
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
    if code != 0 or not payload:
        raise refused(code, said, profile, trouble)
    return payload


def profile_action(action: List[str], profile: str) -> List[str]:
    return action + (["--profile", profile] if profile else [])


def signed_in_accounts(profile: str) -> List[str]:
    """Every Google account Rundesk holds for one OAuth app profile. Local, with no network call."""
    accounts = ask_rundesk(profile_action(["accounts"], profile), profile).get("accounts")
    if not isinstance(accounts, list) or not all(isinstance(one, str) for one in accounts):
        raise AnalyticsError("Rundesk sent a malformed Google account list.")
    return accounts


def managed_token(profile: str, email: str) -> Tuple[str, str]:
    """One short-lived token and the verified account it belongs to."""
    action = profile_action(["access", CAPABILITY], profile)
    payload = ask_rundesk(action + (["--email", email] if email else []), profile)
    token, expires, who = (payload.get("access_token"), payload.get("expires_at"),
                           payload.get("email"))
    # bool is an int, and an expiry of True would otherwise pass every check below.
    if (not isinstance(token, str) or not token or not isinstance(who, str) or not who
            or isinstance(expires, bool) or not isinstance(expires, int)):
        raise AnalyticsError("Rundesk sent no usable Google access token.")
    if expires <= int(time.time()):
        raise AnalyticsError(
            f"Rundesk sent an already expired Google access token. Run: {login_command(profile)}"
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
            stderr=subprocess.PIPE, text=True,
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


def managed_rows(profile: str) -> List[Dict[str, Any]]:
    """What Rundesk holds for one app profile, saying why rather than failing the whole listing."""
    named = profile or "default"
    try:
        accounts = signed_in_accounts(profile)
    except AnalyticsError as exc:
        return [{"profile": named, "account": "", "status": str(exc)}]
    if not accounts:
        return [{"profile": named, "account": "", "status": f"run: {login_command(profile)}"}]
    return [{"profile": named, "account": account, "status": "ready"} for account in accounts]


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


def safe_error(exc: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(exc.read().decode("utf-8"))
        message = body.get("error", {}).get("message") or body.get("error_description")
        if message:
            return str(message)
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        pass
    return f"HTTP {exc.code}"


def api_request(
    access_token: str,
    method: str,
    url: str,
    params: Optional[Dict[str, Any]] = None,
    payload: Optional[Dict[str, Any]] = None,
    retries: int = 2,
) -> Dict[str, Any]:
    if not (url.startswith(ADMIN_BASE + "/") or url.startswith(DATA_BASE + "/")):
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
    if not cleaned.isdigit():
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
    rows = managed_rows(profile)
    columns = ("profile", "account", "status")
    if args.json:
        emit_json(rows)
    else:
        emit_csv(columns, (tuple(row[column] for column in columns) for row in rows))


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
        if not FIELD_NAME_RE.fullmatch(value):
            raise AnalyticsError(f"Invalid Analytics field name: {value!r}.")
    return dimensions, metrics


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

DATE_FORM_RE = re.compile(r"\d{4}-\d{2}-\d{2}|today|yesterday|\d+daysAgo")
# GA4 event names: start with a letter, then letters, digits, or underscores, max 40.
EVENT_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,39}")
MAX_EVENT_FILTER_VALUES = 25

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
ITEM_METRICS = ("itemsViewed", "itemsAddedToCart", "itemsCheckedOut", "itemsPurchased", "itemRevenue")
PURCHASE_METRICS = ("ecommercePurchases", "purchaseRevenue", "totalRevenue")
REVENUE_METRICS = frozenset({"totalRevenue", "purchaseRevenue", "itemRevenue"})
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
        if not FIELD_NAME_RE.fullmatch(value):
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
        if not EVENT_NAME_RE.fullmatch(name):
            raise AnalyticsError(
                f"Invalid GA4 event name: {name!r}. Event names start with a letter and use letters, digits, or underscores."
            )
    return {"filter": {"fieldName": "eventName", "inListFilter": {"values": list(names), "caseSensitive": True}}}


def all_of(expressions: List[Dict[str, Any]]) -> Dict[str, Any]:
    if len(expressions) == 1:
        return expressions[0]
    return {"andGroup": {"expressions": expressions}}


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
    report_notices(response, metrics)
    warn_truncated(response_row_count(response, len(rows)) > len(rows), limit)


def command_traffic(args: argparse.Namespace) -> None:
    dimensions = TRAFFIC_DIMENSIONS.get((args.breakdown, args.scope))
    if dimensions is None:
        raise AnalyticsError(
            f"--breakdown {args.breakdown} has no {args.scope} form; run it with --scope session."
        )
    run_breakdown_report(args, build_breakdown(dimensions, TRAFFIC_METRICS, "sessions"))


def command_audience(args: argparse.Namespace) -> None:
    breakdown = build_breakdown(AUDIENCE_DIMENSIONS[args.breakdown], AUDIENCE_METRICS, "activeUsers")
    notes = ()
    if args.breakdown in THRESHOLDED_BREAKDOWNS:
        notes = (
            "Google applies aggregation thresholds to age and gender, and reports them only for "
            "properties that enabled Google signals.",
        )
    run_breakdown_report(args, breakdown, notes=notes)


def command_key_events(args: argparse.Namespace) -> None:
    breakdown = build_breakdown(KEY_EVENT_DIMENSIONS[args.breakdown], KEY_EVENT_METRICS, "keyEvents")
    expressions = [key_event_filter()]
    if args.event is not None:
        expressions.append(event_name_filter(split_csv(args.event)))
    run_breakdown_report(args, breakdown, dimension_filter=all_of(expressions))


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
    run_breakdown_report(args, breakdown, metric_filter=metric_filter)


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
        help="Attribute to the session or to the user's first visit",
    )
    traffic.set_defaults(handler=command_traffic)

    audience = subparsers.add_parser("audience", help="Report aggregated audience, geography, and technology")
    add_profile_option(audience)
    add_report_window(audience, 25)
    audience.add_argument("--breakdown", choices=AUDIENCE_BREAKDOWN_CHOICES, default="country")
    audience.set_defaults(handler=command_audience)

    key_events = subparsers.add_parser("key-events", help="Report key events, the GA4 name for conversions and leads")
    add_profile_option(key_events)
    add_report_window(key_events, 25)
    key_events.add_argument("--breakdown", choices=KEY_EVENT_BREAKDOWN_CHOICES, default="event")
    key_events.add_argument("--event", help="Comma-separated GA4 event names to isolate")
    key_events.set_defaults(handler=command_key_events)

    commerce = subparsers.add_parser("commerce", help="Report ecommerce item, purchase, and revenue behavior")
    add_profile_option(commerce)
    add_report_window(commerce, 25)
    commerce.add_argument("--breakdown", choices=COMMERCE_BREAKDOWN_CHOICES, default="item")
    commerce.add_argument(
        "--purchased-only", action="store_true", help="Drop rows with no purchase in the window",
    )
    commerce.set_defaults(handler=command_commerce)

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
