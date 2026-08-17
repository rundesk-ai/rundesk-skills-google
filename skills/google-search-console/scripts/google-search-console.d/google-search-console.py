#!/usr/bin/env python3
"""Bounded, read-only access to Google Search Console."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


SKILL = "GOOGLE_SEARCH_CONSOLE"
FIELDS = {
    "GOOGLE_SEARCH_CONSOLE_CLIENT_ID": "CLIENT_ID",
    "GOOGLE_SEARCH_CONSOLE_CLIENT_SECRET": "CLIENT_SECRET",
    "GOOGLE_SEARCH_CONSOLE_REFRESH_TOKEN": "REFRESH_TOKEN",
    "GOOGLE_SEARCH_CONSOLE_LABEL": "LABEL",
}
REQUIRED = tuple(list(FIELDS)[:3])
ACCOUNT_RE = re.compile(r"[A-Z0-9]+(?:_[A-Z0-9]+)*")
TOKEN_URL = "https://oauth2.googleapis.com/token"
WEBMASTERS_API = "https://www.googleapis.com/webmasters/v3"
INSPECTION_API = "https://searchconsole.googleapis.com/v1"


class SearchConsoleError(RuntimeError):
    pass


@dataclass(frozen=True)
class Profile:
    name: str
    client_id: str
    client_secret: str
    refresh_token: str
    label: str


def env_candidates() -> list[Path]:
    paths: list[Path] = []
    for key in (f"{SKILL}_ENV_FILE", "RUNDESK_INTEGRATIONS_ENV"):
        if os.environ.get(key):
            paths.append(Path(os.environ[key]).expanduser())
    xdg = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")).expanduser()
    paths.extend([
        xdg / "rundesk" / "integrations" / "google-search-console" / "env",
        xdg / "google-search-console" / "env",
    ])
    return paths


def resolve_env_file(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    for path in env_candidates():
        if path.is_file():
            return path
    return env_candidates()[-1]


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        print(f"WARNING: dotenv file {path} is accessible by group or others; use chmod 600.", file=sys.stderr)
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() and key.strip() not in os.environ:
            os.environ[key.strip()] = value.strip().strip('"').strip("'")


def normalize(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").upper()


def label_for_suffix(value: str) -> str:
    return value.lower().replace("_", "-")


def is_default(name: str) -> bool:
    normalized = normalize(name)
    return normalized in ("", "DEFAULT")


def profile_value(name: str, field: str) -> str:
    suffix = normalize(name)
    if suffix:
        for key in (f"{field}__{suffix}", f"{SKILL}_{suffix}_{FIELDS[field]}"):
            if os.environ.get(key):
                return os.environ[key]
    return os.environ.get(field, "") if is_default(name) else ""


def discovered_profiles() -> list[str]:
    explicit = [item.strip() for item in os.environ.get(f"{SKILL}_PROFILES", "").split(",") if item.strip()]
    default = os.environ.get(f"{SKILL}_DEFAULT_PROFILE", "")
    if default and default not in explicit:
        explicit.insert(0, default)
    if explicit:
        return explicit
    names: set[str] = set()
    infix_found = False
    for key in os.environ:
        for field in FIELDS:
            prefix = f"{field}__"
            suffix = key[len(prefix):] if key.startswith(prefix) else ""
            if suffix and ACCOUNT_RE.fullmatch(suffix):
                names.add(label_for_suffix(suffix))
        for field, short in FIELDS.items():
            match = re.fullmatch(rf"{SKILL}_({ACCOUNT_RE.pattern})_{short}", key)
            if match and match.group(1) not in {"DEFAULT", "ENV"}:
                names.add(label_for_suffix(match.group(1)))
                infix_found = True
    if not infix_found and any(os.environ.get(field) for field in REQUIRED):
        names.add(default or "default")
    return sorted(names)


def get_profile(name: str) -> Profile:
    if name and not normalize(name):
        raise SearchConsoleError("Profile names must contain at least one letter or digit.")
    values = {field: profile_value(name, field) for field in FIELDS}
    missing = [field if is_default(name) else f"{field}__{normalize(name)}" for field in REQUIRED if not values[field]]
    if missing:
        raise SearchConsoleError("Missing required configuration: " + ", ".join(missing) + ". Run rundesk skills configure for this skill.")
    return Profile(name, values[REQUIRED[0]], values[REQUIRED[1]], values[REQUIRED[2]], values["GOOGLE_SEARCH_CONSOLE_LABEL"] or name)


def selected_profile(args: argparse.Namespace) -> Profile:
    names = discovered_profiles()
    if args.profile:
        return get_profile(args.profile)
    if not names:
        raise SearchConsoleError("No configured Google Search Console profiles. Run rundesk skills configure for this skill.")
    if len(names) != 1:
        raise SearchConsoleError("Multiple profiles are configured; select one with --profile: " + ", ".join(names))
    return get_profile(names[0])


class RejectRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse redirects so bearer tokens never cross an unexpected request boundary."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_url(request: urllib.request.Request, timeout: int = 30):
    return urllib.request.build_opener(RejectRedirectHandler()).open(request, timeout=timeout)


def request_json(url: str, *, method: str = "GET", headers: dict[str, str] | None = None, body: dict[str, Any] | None = None, opener: Callable[..., Any] = open_url) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with opener(request, timeout=30) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            message = json.loads(detail).get("error", {}).get("message", "")
        except (ValueError, AttributeError):
            message = ""
        raise SearchConsoleError(f"Google API request failed with HTTP {exc.code}" + (f": {message}" if message else ".")) from exc
    except urllib.error.URLError as exc:
        raise SearchConsoleError(f"Google API request failed: {exc.reason}") from exc
    try:
        return json.loads(payload) if payload else {}
    except ValueError as exc:
        raise SearchConsoleError("Google API returned invalid JSON.") from exc


def access_token(profile: Profile, opener: Callable[..., Any] = open_url) -> str:
    data = urllib.parse.urlencode({
        "client_id": profile.client_id,
        "client_secret": profile.client_secret,
        "refresh_token": profile.refresh_token,
        "grant_type": "refresh_token",
    }).encode("utf-8")
    request = urllib.request.Request(TOKEN_URL, data=data, method="POST", headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with opener(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError) as exc:
        raise SearchConsoleError("Google OAuth token refresh failed; verify the profile credentials and grant.") from exc
    token = result.get("access_token", "")
    if not token:
        raise SearchConsoleError("Google OAuth token refresh returned no access token.")
    return token


def api(profile: Profile, path: str, *, base: str = WEBMASTERS_API, method: str = "GET", body: dict[str, Any] | None = None) -> dict[str, Any]:
    token = access_token(profile)
    return request_json(base + path, method=method, headers={"Authorization": f"Bearer {token}"}, body=body)


def write_rows(rows: list[dict[str, Any]], columns: list[str], as_json: bool) -> None:
    if as_json:
        print(json.dumps(rows, indent=2, sort_keys=True))
        return
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    sys.stdout.write(output.getvalue())


def bounded(items: list[Any], limit: int, noun: str) -> list[Any]:
    if len(items) > limit:
        print(f"WARNING: {noun} output truncated to {limit} results.", file=sys.stderr)
    return items[:limit]


def cmd_profiles(args: argparse.Namespace) -> None:
    rows = []
    for name in discovered_profiles():
        missing = sum(not profile_value(name, field) for field in REQUIRED)
        rows.append({"profile": name, "label": profile_value(name, "GOOGLE_SEARCH_CONSOLE_LABEL") or name, "status": "ready" if missing == 0 else f"missing {missing}"})
    write_rows(rows, ["profile", "label", "status"], args.json)


def cmd_sites(args: argparse.Namespace) -> None:
    profile = selected_profile(args)
    entries = api(profile, "/sites").get("siteEntry", [])
    rows = [{"site": item.get("siteUrl", ""), "permission": item.get("permissionLevel", ""), "profile": profile.name} for item in bounded(entries, args.limit, "sites")]
    write_rows(rows, ["site", "permission", "profile"], args.json)


def date_range(args: argparse.Namespace) -> tuple[str, str]:
    if bool(args.start_date) != bool(args.end_date):
        raise SearchConsoleError("Use both --start-date and --end-date, or neither.")
    if args.start_date:
        try:
            start, end = dt.date.fromisoformat(args.start_date), dt.date.fromisoformat(args.end_date)
        except ValueError as exc:
            raise SearchConsoleError("Dates must use YYYY-MM-DD.") from exc
        if start > end:
            raise SearchConsoleError("--start-date must not be after --end-date.")
        return start.isoformat(), end.isoformat()
    end = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)
    return (end - dt.timedelta(days=args.days - 1)).isoformat(), end.isoformat()


def cmd_performance(args: argparse.Namespace) -> None:
    profile = selected_profile(args)
    start, end = date_range(args)
    body: dict[str, Any] = {"startDate": start, "endDate": end, "rowLimit": args.limit, "startRow": 0}
    if args.dimension:
        body["dimensions"] = args.dimension
    if args.search_type:
        body["type"] = args.search_type
    path = "/sites/" + urllib.parse.quote(args.site, safe="") + "/searchAnalytics/query"
    items = api(profile, path, method="POST", body=body).get("rows", [])
    if len(items) == args.limit:
        print(
            f"WARNING: performance output reached the {args.limit}-row limit and may be truncated.",
            file=sys.stderr,
        )
    rows = []
    for item in items:
        row = {dimension: value for dimension, value in zip(args.dimension, item.get("keys", []))}
        row.update({"clicks": item.get("clicks", 0), "impressions": item.get("impressions", 0), "ctr": item.get("ctr", 0), "position": item.get("position", 0), "profile": profile.name})
        rows.append(row)
    write_rows(rows, args.dimension + ["clicks", "impressions", "ctr", "position", "profile"], args.json)


def cmd_inspect(args: argparse.Namespace) -> None:
    profile = selected_profile(args)
    response = api(profile, "/urlInspection/index:inspect", base=INSPECTION_API, method="POST", body={"inspectionUrl": args.url, "siteUrl": args.site, "languageCode": "en-US"})
    result = response.get("inspectionResult")
    index = result.get("indexStatusResult") if isinstance(result, dict) else None
    if not isinstance(index, dict) or not index:
        raise SearchConsoleError("Google returned no URL inspection result for the requested URL.")
    row = {"url": args.url, "verdict": index.get("verdict", ""), "coverage_state": index.get("coverageState", ""), "indexing_state": index.get("indexingState", ""), "last_crawl": index.get("lastCrawlTime", ""), "robots_state": index.get("robotsTxtState", ""), "google_canonical": index.get("googleCanonical", ""), "user_canonical": index.get("userCanonical", ""), "profile": profile.name}
    write_rows([row], list(row), args.json)


def cmd_sitemaps(args: argparse.Namespace) -> None:
    profile = selected_profile(args)
    path = "/sites/" + urllib.parse.quote(args.site, safe="") + "/sitemaps"
    items = api(profile, path).get("sitemap", [])
    rows = [{"path": item.get("path", ""), "type": item.get("type", ""), "submitted": item.get("lastSubmitted", ""), "downloaded": item.get("lastDownloaded", ""), "pending": item.get("isPending", False), "warnings": item.get("warnings", 0), "errors": item.get("errors", 0), "profile": profile.name} for item in bounded(items, args.limit, "sitemaps")]
    write_rows(rows, ["path", "type", "submitted", "downloaded", "pending", "warnings", "errors", "profile"], args.json)


def parser() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--env-file")
    parent.add_argument("--json", action="store_true")
    profile = argparse.ArgumentParser(add_help=False)
    profile.add_argument("--profile")
    result = argparse.ArgumentParser(prog="google-search-console", description="Read bounded Google Search Console evidence.")
    subs = result.add_subparsers(dest="command", required=True)
    p = subs.add_parser("profiles", parents=[parent], help="List locally configured profiles without contacting Google.")
    p.set_defaults(func=cmd_profiles)
    p = subs.add_parser("sites", parents=[parent, profile], help="List accessible Search Console properties.")
    p.add_argument("--limit", type=int, default=25)
    p.set_defaults(func=cmd_sites)
    p = subs.add_parser("performance", parents=[parent, profile], help="Query aggregated organic search performance.")
    p.add_argument("--site", required=True)
    p.add_argument("--days", type=int, default=28)
    p.add_argument("--start-date")
    p.add_argument("--end-date")
    p.add_argument("--dimension", action="append", choices=["date", "country", "device", "page", "query", "searchAppearance"], default=[])
    p.add_argument("--search-type", choices=["web", "image", "video", "news", "discover", "googleNews"])
    p.add_argument("--limit", type=int, default=25)
    p.set_defaults(func=cmd_performance)
    p = subs.add_parser("inspect-url", parents=[parent, profile], help="Inspect Google's indexed state for one URL.")
    p.add_argument("--site", required=True)
    p.add_argument("--url", required=True)
    p.set_defaults(func=cmd_inspect)
    p = subs.add_parser("sitemaps", parents=[parent, profile], help="List submitted sitemaps for one property.")
    p.add_argument("--site", required=True)
    p.add_argument("--limit", type=int, default=25)
    p.set_defaults(func=cmd_sitemaps)
    return result


def main(argv: list[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
        load_dotenv(resolve_env_file(args.env_file))
        if hasattr(args, "limit") and not 1 <= args.limit <= 1000:
            raise SearchConsoleError("--limit must be between 1 and 1000.")
        if hasattr(args, "days") and not 1 <= args.days <= 480:
            raise SearchConsoleError("--days must be between 1 and 480.")
        args.func(args)
        return 0
    except SearchConsoleError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
