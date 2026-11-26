"""Track a Transport Victoria bus departure at a stop.

Fill in the API key below before running this script.
"""

import csv
from io import BytesIO
import os
import pickle
import re
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
from zipfile import ZipFile

try:
	from google.transit import gtfs_realtime_pb2
except ImportError as error:
	raise RuntimeError(
		"Install the GTFS-Realtime parser first: "
		"python -m pip install gtfs-realtime-bindings"
	) from error

# ----------------------------- configuration -----------------------------
BASE_DIR = Path(__file__).resolve().parent
PTV_API_KEY = ""                         # Transport Victoria API key UUID
STOP_ID = 16372                                # e.g. 1127
"""
Examples
15967 Marlborough Street Reserve/Marlborough St | To Chadstone
19800 Oakleigh Station/Portman St
51645 Murrumbeena Station/Murrumbeena Rd | To Moorabbin / Sandringham
16372 Centre Rd/Second St | To Westall
6220 Murrumbeena Rd/North Rd | To Monash
"""
ROUTE_ID = None                                 # route_id, route number, or GTFS name (case-insensitive)
RUN_ID = None                                   # exact trip ID, if known
POLL_SECONDS = 30
MAX_RESULTS = 20
CSV_FILE = BASE_DIR / "ptv_punctuality.csv"
STATIC_GTFS_ZIP = BASE_DIR / "transport_victoria_gtfs.zip"
STATIC_GTFS_URL = "https://data.ptv.vic.gov.au/downloads/gtfs.zip"
STATIC_ARCHIVE_NAMES = {
	"4/google_transit.zip",
	"5/google_transit.zip",
	"6/google_transit.zip",
}
STATIC_MODE = "bus"
API_URL = (
	"https://api.opendata.transport.vic.gov.au/"
	"opendata/public-transport/gtfs/realtime/v1/bus/trip-updates"
)
API_KEY_HEADER = "KeyID"
DEBUG_REALTIME = False
CALCULATE_DELAY_FROM_TIMESTAMP = False
MELBOURNE_TZ = ZoneInfo("Australia/Melbourne")
PRINT_LONG_NAME = False
PRINT_DISRUPTION = False
CLEAR_TERMINAL = False
SHOW_MAP = True
# ---------------------------------------------------------------------------

_static_info = None
_realtime_debug_reported = False
_map_window = None


def _load_static_info():
	"""Load stop names and trip destinations from the official GTFS schedule."""
	global _static_info
	if _static_info is not None:
		return _static_info
	mode = STATIC_MODE
	cache_name = re.sub(r"[^A-Za-z0-9_.-]", "_", str(STOP_ID))
	cache_dir = BASE_DIR / "cache"
	cache_file = cache_dir / f"{STATIC_GTFS_ZIP.stem}_{mode}_{cache_name}.cache"
	cache_signature = (
		8,
		STATIC_GTFS_ZIP.stat().st_mtime_ns if STATIC_GTFS_ZIP.exists() else None,
		str(STOP_ID),
		tuple(sorted(STATIC_ARCHIVE_NAMES)),
	)
	if cache_file.exists():
		try:
			with cache_file.open("rb") as file:
				cached_signature, cached_info = pickle.load(file)
			if cached_signature == cache_signature:
				_static_info = cached_info
				return _static_info
		except (OSError, EOFError, pickle.UnpicklingError, ValueError):
			pass

	if not STATIC_GTFS_ZIP.exists():
		print("Downloading the official GTFS schedule for stop and direction names...")
		request = Request(
			STATIC_GTFS_URL,
			headers={"User-Agent": "ptv-punctuality-tracker/1.0"},
		)
		with urlopen(request, timeout=120) as response:
			STATIC_GTFS_ZIP.write_bytes(response.read())

	with ZipFile(STATIC_GTFS_ZIP) as archive:
		schedule_archives = [
			ZipFile(BytesIO(archive.read(name)))
			for name in archive.namelist()
			if name in STATIC_ARCHIVE_NAMES
		]
		stops = {}
		platforms = {}
		stop_coordinates = {}
		stop_parents = {}
		routes = {}
		trips = {}
		stop_times = {}
		trip_stops = {}
		calendar = {}
		calendar_dates = {}
		route_aliases = {}
		stop_ids = set()
		try:
			for schedule in schedule_archives:
				schedule_stops = []
				with schedule.open("stops.txt") as file:
					for row in csv.DictReader(
						(line.decode("utf-8-sig") for line in file)
					):
						stops[row["stop_id"]] = row["stop_name"]
						platforms[row["stop_id"]] = row.get(
							"platform_code", ""
						).strip()
						stop_parents[row["stop_id"]] = row.get(
							"parent_station", ""
						)
						try:
							stop_coordinates[row["stop_id"]] = (
								float(row["stop_lat"]),
								float(row["stop_lon"]),
							)
						except (KeyError, ValueError):
							pass
						schedule_stops.append(row)

				stop_ids.update(
					row["stop_id"]
					for row in schedule_stops
					if row["stop_id"] == str(STOP_ID)
					or row.get("parent_station") == str(STOP_ID)
				)

				with schedule.open("routes.txt") as file:
					for row in csv.DictReader(
						(line.decode("utf-8-sig") for line in file)
					):
						routes[row["route_id"]] = {
							"name": (
								row.get("route_short_name")
								or row.get("route_long_name")
								or row["route_id"]
							),
							"short_name": row.get("route_short_name", "").strip(),
							"long_name": row.get("route_long_name", ""),
							"color": row.get("route_color", ""),
							"text_color": row.get("route_text_color", ""),
						}

				for route_id, route in routes.items():
					for alias in (
						route_id,
						route["short_name"],
						route["long_name"],
						route["name"],
					):
						normalized_alias = alias.strip().casefold()
						if normalized_alias:
							route_aliases.setdefault(
								normalized_alias, set()
							).add(route_id)

				with schedule.open("stop_times.txt") as file:
					for row in csv.DictReader(
						(line.decode("utf-8-sig") for line in file)
					):
						if row["stop_id"] in stop_ids:
							stop_times[row["trip_id"]] = {
								"arrival": row["arrival_time"],
								"departure": row["departure_time"],
								"stop_id": row["stop_id"],
							}

				relevant_trip_ids = set(stop_times)
				with schedule.open("stop_times.txt") as file:
					for row in csv.DictReader(
						(line.decode("utf-8-sig") for line in file)
					):
						if row["trip_id"] in relevant_trip_ids:
							trip_stops.setdefault(row["trip_id"], []).append(
								{
									"stop_id": row["stop_id"],
									"arrival": row["arrival_time"],
									"departure": row["departure_time"],
									"sequence": int(row["stop_sequence"]),
								}
							)

				with schedule.open("trips.txt") as file:
					for row in csv.DictReader(
						(line.decode("utf-8-sig") for line in file)
					):
						if row["trip_id"] in stop_times:
							trips[row["trip_id"]] = {
								"route_id": row["route_id"],
								"headsign": row.get("trip_headsign", ""),
								"service_id": row["service_id"],
								"shape_id": row.get("shape_id", ""),
							}

				with schedule.open("calendar.txt") as file:
					for row in csv.DictReader(
						(line.decode("utf-8-sig") for line in file)
					):
						calendar[row["service_id"]] = row

				with schedule.open("calendar_dates.txt") as file:
					for row in csv.DictReader(
						(line.decode("utf-8-sig") for line in file)
					):
						calendar_dates.setdefault(
							(row["service_id"], row["date"]), []
						).append(row["exception_type"])
		finally:
			for schedule in schedule_archives:
				schedule.close()

		station_groups = {}
		for stop_id, parent_id in stop_parents.items():
			stop_name = stops.get(stop_id, "").casefold()
			if (
				parent_id
				and stop_id in stop_coordinates
				and "replacement" not in stop_name
			):
				station_groups.setdefault(parent_id, []).append(stop_id)
		for parent_id, child_ids in station_groups.items():
			station_coordinate = stop_coordinates.get(parent_id)
			if station_coordinate is None:
				locations = [
					stop_coordinates[stop_id] for stop_id in child_ids
				]
				station_coordinate = (
					sum(point[0] for point in locations) / len(locations),
					sum(point[1] for point in locations) / len(locations),
				)
			station_coordinate = tuple(
				round(value, 5) for value in station_coordinate
			)
			for stop_id in child_ids:
				stop_coordinates[stop_id] = station_coordinate
		stop_coordinates = {
			stop_id: tuple(round(value, 5) for value in coordinate)
			for stop_id, coordinate in stop_coordinates.items()
		}

	_static_info = {
		"stops": stops,
		"platforms": platforms,
		"stop_coordinates": stop_coordinates,
		"stop_parents": stop_parents,
		"routes": routes,
		"route_aliases": route_aliases,
		"trips": trips,
		"stop_times": stop_times,
		"trip_stops": trip_stops,
		"calendar": calendar,
		"calendar_dates": calendar_dates,
		"stop_ids": stop_ids,
	}
	try:
		cache_dir.mkdir(exist_ok=True)
		with cache_file.open("wb") as file:
			pickle.dump((cache_signature, _static_info), file)
	except OSError:
		pass
	return _static_info


def _route_name(route_id, routes):
	"""Resolve the public route number from a realtime route identifier."""
	if route_id in routes:
		return routes[route_id]["name"]

	match = re.fullmatch(r"vic:(\d+):.*", route_id)
	if match:
		encoded_number = match.group(1)
		candidates = [
			info for info in routes.values()
			if info["name"].isdigit() and encoded_number.endswith(info["name"])
		]
		if candidates:
			return max(candidates, key=lambda info: len(info["name"]))["name"]
	return route_id


def _resolve_route_ids(static_info, selector):
	"""Resolve a GTFS route ID, short name, or long name case-insensitively."""
	if selector is None:
		return None
	normalized = str(selector).strip().casefold()
	aliases = static_info.get("route_aliases")
	if aliases is not None:
		return set(aliases.get(normalized, ()))
	return {
		route_id
		for route_id, route in static_info["routes"].items()
		if normalized
		in {
			route_id.strip().casefold(),
			route.get("short_name", "").strip().casefold(),
			route.get("long_name", "").strip().casefold(),
			route.get("name", "").strip().casefold(),
		}
	}


def _route_display_name(route_id, routes, selector=None):
	"""Prefer a configured human-readable route name in departure output."""
	route = routes.get(route_id, {})
	if selector is not None:
		normalized = str(selector).strip().casefold()
		for key in ("long_name", "short_name", "name"):
			name = route.get(key, "").strip()
			if name and name.casefold() == normalized:
				return name
	return _route_name(route_id, routes)


def _map_color(value, fallback):
	"""Return a valid CSS color from a GTFS six-digit color."""
	value = str(value or "").strip().lstrip("#")
	return f"#{value}" if re.fullmatch(r"[0-9a-fA-F]{6}", value) else fallback


def _route_colors(route_id, routes):
	"""Return the GTFS route colors for a realtime route identifier."""
	info = routes.get(route_id)
	if info is not None:
		return info["color"], info["text_color"]
	match = re.fullmatch(r"vic:(\d+):.*", route_id)
	if match:
		encoded_number = match.group(1)
		candidates = [
			info for info in routes.values()
			if info["name"].isdigit() and encoded_number.endswith(info["name"])
		]
		if candidates:
			info = max(candidates, key=lambda item: len(item["name"]))
			return info["color"], info["text_color"]
	return "", ""


def _ansi_color(value, foreground=False):
	"""Convert a six-digit GTFS color to an ANSI true-color escape."""
	if not value or len(value) != 6:
		return ""
	try:
		red, green, blue = (
			int(value[index:index + 2], 16) for index in (0, 2, 4)
		)
	except ValueError:
		return ""
	channel = 38 if foreground else 48
	return f"\033[{channel};2;{red};{green};{blue}m"


def _colored_route(route_name, route_id, routes):
	"""Format a route label with its GTFS background and text colors."""
	background, foreground = _route_colors(route_id, routes)
	if not sys.stdout.isatty() or os.environ.get("NO_COLOR") is not None:
		return route_name
	prefix = _ansi_color(background) + _ansi_color(foreground, foreground=True)
	return f"{prefix} {route_name} \033[0m" if prefix else route_name


def _colored_status(delay_seconds, realtime):
	"""Color realtime status without inventing a delay."""
	status = _format_delay(delay_seconds)
	if not sys.stdout.isatty() or os.environ.get("NO_COLOR") is not None:
		return status
	if delay_seconds is not None and delay_seconds > 0:
		color = "\033[91m"
	elif delay_seconds is not None and delay_seconds < 0:
		color = "\033[38;2;255;165;0m"
	elif realtime:
		color = "\033[92m"
	else:
		return status
	return f"{color}{status}\033[0m"


def _trip_key(trip_id):
	"""Ignore the service-day number embedded in Victorian train trip IDs."""
	return re.sub(r"--\d+-", "--DAY-", trip_id)


def _trip_keys(trip_id):
	"""Return equivalent keys used by different Victorian realtime feeds."""
	keys = {trip_id, _trip_key(trip_id)}
	if "|" in trip_id:
		keys.add(trip_id.split("|", 1)[0])
	return keys


def _stop_keys(stop_id):
	"""Return equivalent keys used by static and realtime feeds."""
	value = str(stop_id)
	keys = {value}
	if ":" in value:
		keys.add(value.rsplit(":", 1)[-1])
	return keys


def _normalise_location(value):
	"""Reduce a stop or destination name to comparable location words."""
	value = re.sub(r"\([^)]*\)", " ", value.casefold())
	value = re.sub(r"[^a-z0-9]+", " ", value)
	return value.split()


def _clear_terminal():
	"""Clear the interactive terminal when enabled."""
	if CLEAR_TERMINAL and sys.stdout.isatty():
		print("\033[2J\033[H", end="", flush=True)


def _platform_label(platform_code):
	"""Format a GTFS platform code according to the configured mode."""
	if not platform_code:
		return ""
	code = platform_code.strip()
	if not code:
		return ""
	if code.casefold().startswith(("bay ", "platform ")):
		return code
	prefix = "Platform" if STATIC_MODE in {"metro", "vline"} else "Bay"
	return f"{prefix} {code}"


def _is_terminal_at_stop(headsign, static_info):
	"""Exclude trips whose destination is the stop currently being tracked."""
	if not headsign:
		return False
	stop_names = [
		static_info["stops"].get(str(STOP_ID), ""),
		static_info["stops"].get(str(STOP_ID).split(":", 1)[-1], ""),
	]
	headsign_words = _normalise_location(headsign)
	if not headsign_words:
		return False
	ignored_suffixes = {
		"airport", "arrivals", "bay", "bus", "express",
		"railway", "station", "stop", "terminal",
		"t1", "t2", "t3", "t4", "skybus",
	}
	for stop_name in stop_names:
		stop_words = _normalise_location(stop_name.split("/", 1)[0])
		if not stop_words:
			continue
		if headsign_words == stop_words:
			return True
		if (
			len(headsign_words) < len(stop_words)
			and stop_words[:len(headsign_words)] == headsign_words
			and all(word in ignored_suffixes for word in stop_words[len(headsign_words):])
		):
			return True
	return False


def _ptv_get():
	"""Fetch the authenticated GTFS-Realtime bus trip-update feed."""
	if not PTV_API_KEY:
		raise RuntimeError("PTV_API_KEY must contain the API key UUID.")

	request = Request(
		API_URL,
		headers={
			API_KEY_HEADER: PTV_API_KEY,
			"Accept": "application/x-protobuf",
			"User-Agent": "ptv-punctuality-tracker/1.0",
		},
	)
	with urlopen(request, timeout=20) as response:
		feed = gtfs_realtime_pb2.FeedMessage()
		feed.ParseFromString(response.read())
		return feed


def _disruption_url():
	"""Return the service-alerts endpoint matching the realtime mode."""
	return API_URL.rsplit("/", 1)[0] + "/service-alerts"


def _alert_text(translated):
	"""Return the first usable translated GTFS-RT alert text."""
	for item in translated.translation:
		if item.text:
			return item.text
	return ""


def _print_disruptions():
	"""Print current alerts affecting the configured stop, once at startup."""
	if not PRINT_DISRUPTION:
		return
	request = Request(
		_disruption_url(),
		headers={
			API_KEY_HEADER: PTV_API_KEY,
			"Accept": "application/x-protobuf",
			"User-Agent": "ptv-punctuality-tracker/1.0",
		},
	)
	try:
		with urlopen(request, timeout=20) as response:
			feed = gtfs_realtime_pb2.FeedMessage()
			feed.ParseFromString(response.read())
	except HTTPError as error:
		print(
			f"Disruption feed unavailable (HTTP {error.code}); "
			"continuing without disruption alerts.",
			file=sys.stderr,
		)
		return
	except URLError as error:
		print(
			f"Disruption feed unavailable ({error.reason}); "
			"continuing without disruption alerts.",
			file=sys.stderr,
		)
		return

	stop_keys = _stop_keys(STOP_ID)
	static_info = _load_static_info()
	selected_route_ids = _resolve_route_ids(static_info, ROUTE_ID)
	if ROUTE_ID is not None and not selected_route_ids:
		raise ValueError(
			f"ROUTE_ID {ROUTE_ID!r} did not match a route ID, short name, "
			"or long name in the selected GTFS archive."
		)
	station_route_ids = {
		trip["route_id"] for trip_id, trip in static_info["trips"].items()
		if trip_id in static_info["stop_times"]
	}
	alerts = {}
	for entity in feed.entity:
		if not entity.HasField("alert"):
			continue
		alert = entity.alert
		entities = list(alert.informed_entity)
		matches_stop = any(
			stop_keys & _stop_keys(item.stop_id)
			for item in entities
			if item.stop_id
		)
		matches_route = any(
			(
				item.route_id in station_route_ids
				and (
					selected_route_ids is None
					or item.route_id in selected_route_ids
				)
			)
			or (
				selected_route_ids is not None
				and item.route_id in selected_route_ids
			)
			for item in entities
			if item.route_id
		)
		if entities and not (matches_stop or matches_route):
			continue
		header = _alert_text(alert.header_text)
		description = _alert_text(alert.description_text)
		effect = alert.effect
		effect_name = gtfs_realtime_pb2.Alert.Effect.Name(effect)
		key = (effect_name, header or description, description)
		route_ids = {
			item.route_id
			for item in entities
			if item.route_id
			and (
				(
					item.route_id in station_route_ids
					and (
						selected_route_ids is None
						or item.route_id in selected_route_ids
					)
				)
				or (
					selected_route_ids is not None
					and item.route_id in selected_route_ids
				)
			)
		}
		if key not in alerts:
			alerts[key] = set()
		alerts[key].update(route_ids)

	if not alerts:
		return
	print("Disruptions:")
	for (effect, header, description), route_ids in alerts.items():
		message = header or "No additional details"
		if description and description != header:
			message += f" - {description}"
		if route_ids:
			line_labels = [
				_colored_route(
					_route_name(route_id, static_info["routes"]),
					route_id,
					static_info["routes"],
				)
				for route_id in sorted(route_ids)
			]
			line_prefix = " / ".join(line_labels)
		else:
			line_prefix = "Network"
		alert_text = f"[{effect}] {message}"
		if sys.stdout.isatty() and os.environ.get("NO_COLOR") is None:
			alert_color = (
				"\033[38;2;255;135;105m"
				if effect in {"NO_SERVICE", "SIGNIFICANT_DELAYS", "STOP_MOVED"}
				else "\033[38;2;255;210;100m"
			)
			print(f"{line_prefix} {alert_color}{alert_text}\033[0m")
		else:
			print(f"{line_prefix} {alert_text}")


def departures():
	"""Return scheduled departures, overlaid with available realtime updates."""
	global _realtime_debug_reported
	feed = _ptv_get()
	feed_fetched_at = datetime.now(timezone.utc)
	static_info = _load_static_info()
	selected_route_ids = _resolve_route_ids(static_info, ROUTE_ID)
	if ROUTE_ID is not None and not selected_route_ids:
		raise ValueError(
			f"ROUTE_ID {ROUTE_ID!r} did not match a route ID, short name, "
			"or long name in the selected GTFS archive."
		)
	now_utc = datetime.now(timezone.utc)
	now_local = now_utc.astimezone(MELBOURNE_TZ)
	today = now_local.date()
	service_dates = (
		today - timedelta(days=1),
		today,
		today + timedelta(days=1),
	)
	realtime_by_date = {}
	realtime_without_date = {}
	realtime_trip_ids = set()
	feed_trip_count = 0
	feed_stop_update_count = 0
	matching_stop_update_count = 0
	static_stop_keys = {
		key
		for stop_id in static_info["stop_ids"]
		for key in _stop_keys(stop_id)
	}
	for entity in feed.entity:
		if not entity.HasField("trip_update"):
			continue
		feed_trip_count += 1
		trip = entity.trip_update.trip
		realtime_trip_ids.add(trip.trip_id)
		if RUN_ID is not None and trip.trip_id != str(RUN_ID):
			continue
		if selected_route_ids is not None and not any(
			route_id.casefold() == str(trip.route_id).casefold()
			for route_id in selected_route_ids
		):
			continue
		for update in entity.trip_update.stop_time_update:
			feed_stop_update_count += 1
			if not _stop_keys(update.stop_id) & static_stop_keys:
				continue
			matching_stop_update_count += 1
			arrival = (
				(
					datetime.fromtimestamp(
						update.arrival.time, timezone.utc
					),
					None
					if CALCULATE_DELAY_FROM_TIMESTAMP
					else (
						update.arrival.delay
						if update.arrival.HasField("delay")
						else None
					),
				)
				if update.HasField("arrival")
				and update.arrival.HasField("time")
				else None
			)
			departure = (
				(
					datetime.fromtimestamp(
						update.departure.time, timezone.utc
					),
					None
					if CALCULATE_DELAY_FROM_TIMESTAMP
					else (
						update.departure.delay
						if update.departure.HasField("delay")
						else None
					),
				)
				if update.HasField("departure")
				and update.departure.HasField("time")
				else None
			)
			if arrival is None and departure is None:
				continue
			service_date = (
				trip.start_date if trip.HasField("start_date") else None
			)
			for key in _trip_keys(trip.trip_id):
				if service_date:
					realtime_by_date[
						(key, service_date, str(update.stop_id))
					] = (
						arrival,
						departure,
					)
				else:
					realtime_without_date.setdefault(
						(key, str(update.stop_id)), []
					).append((arrival, departure))

	if DEBUG_REALTIME and not _realtime_debug_reported:
		static_trip_ids = set(static_info["stop_times"])
		matching_trip_ids = {
			trip_id for trip_id in realtime_trip_ids
			if _trip_keys(trip_id) & static_trip_ids
		}
		print(
			"Realtime diagnostic: "
			f"entities={len(feed.entity)}, trip_updates={feed_trip_count}, "
			f"stop_updates={feed_stop_update_count}, "
			f"matching_stop_updates={matching_stop_update_count}, "
			f"matching_trip_ids={len(matching_trip_ids)}"
		)
		_realtime_debug_reported = True

	static_items = []
	for service_day in service_dates:
		service_date = service_day.strftime("%Y%m%d")
		for trip_id, departure_time in static_info["stop_times"].items():
			trip = static_info["trips"].get(trip_id)
			if trip is None:
				continue
			if _is_terminal_at_stop(trip["headsign"], static_info):
				continue
			if (
				selected_route_ids is not None
				and trip["route_id"] not in selected_route_ids
			):
				continue
			if RUN_ID is not None and trip_id != str(RUN_ID):
				continue
			if not _service_active(
				static_info, trip["service_id"], service_day
			):
				continue
			scheduled_arrival = _gtfs_time(
				service_day, departure_time["arrival"]
			).astimezone(timezone.utc)
			scheduled_departure = _gtfs_time(
				service_day, departure_time["departure"]
			).astimezone(timezone.utc)
			realtime_arrival, realtime_departure = _matching_realtime_update(
				trip_id,
				service_date,
				scheduled_departure,
				departure_time["stop_id"],
				realtime_by_date,
				realtime_without_date,
			)
			arrival, arrival_delay = realtime_arrival or (
				scheduled_arrival,
				None,
			)
			departure, delay = realtime_departure or (
				scheduled_departure,
				None,
			)
			if CALCULATE_DELAY_FROM_TIMESTAMP:
				arrival_delay = (
					int((arrival - scheduled_arrival).total_seconds())
					if realtime_arrival is not None
					else None
				)
				delay = (
					int((departure - scheduled_departure).total_seconds())
					if realtime_departure is not None
					else None
				)
			if CALCULATE_DELAY_FROM_TIMESTAMP:
				estimated = (
					departure
					if realtime_departure is not None
					else arrival
					if realtime_arrival is not None
					else scheduled_departure
				)
				if realtime_departure is None and realtime_arrival is not None:
					delay = arrival_delay
			else:
				estimated = departure
			if estimated < now_utc:
				continue
			static_items.append(
				{
					"route_id": trip["route_id"],
					"route_name": _route_display_name(
						trip["route_id"],
						static_info["routes"],
						ROUTE_ID,
					),
					"route_long_name": static_info["routes"].get(
						trip["route_id"], {}
					).get("long_name", ""),
					"run_id": trip_id,
					"service_date": service_date,
					"stop_name": static_info["stops"].get(
						str(STOP_ID), f"stop {STOP_ID}"
					),
					"stop_id": departure_time["stop_id"],
					"platform": _platform_label(
						static_info["platforms"].get(
							departure_time["stop_id"], ""
						)
					),
					"direction": trip["headsign"] or "direction unavailable",
					"estimated_departure_utc": estimated,
					"arrival_utc": arrival,
					"scheduled_arrival_utc": scheduled_arrival,
					"scheduled_departure_utc": scheduled_departure,
					"delay_seconds": delay,
					"arrival_delay_seconds": arrival_delay,
					"realtime": (
						realtime_departure is not None
						or realtime_arrival is not None
					),
					"feed_fetched_at": feed_fetched_at,
				}
			)
	unique = {}
	for item in static_items:
		key = (
			item["route_name"],
			item["direction"],
			item["estimated_departure_utc"],
			item["scheduled_departure_utc"],
			item["platform"],
		)
		previous = unique.get(key)
		if previous is None or item["realtime"] and not previous["realtime"]:
			unique[key] = item
	realtime_items = sorted(
		(item for item in unique.values() if item["realtime"]),
		key=lambda item: item["estimated_departure_utc"],
	)
	scheduled_items = sorted(
		(item for item in unique.values() if not item["realtime"]),
		key=lambda item: item["estimated_departure_utc"],
	)
	selected = realtime_items[:MAX_RESULTS]
	selected.extend(scheduled_items[:MAX_RESULTS - len(selected)])
	return sorted(
		selected, key=lambda item: item["estimated_departure_utc"]
	)


def _build_map_data(items):
	"""Build an offline, coordinate-based map for selected departures."""
	static_info = _load_static_info()
	coordinates = static_info["stop_coordinates"]
	station_ids = [
		str(STOP_ID),
		*(
			stop_id
			for stop_id, parent in static_info["stop_parents"].items()
			if parent == str(STOP_ID)
		),
	]
	station_ids = list(dict.fromkeys(station_ids))
	station_coordinate = next(
		(coordinates[stop_id] for stop_id in station_ids if stop_id in coordinates),
		None,
	)
	if station_coordinate is None:
		child_coordinates = [
			coordinates[stop_id]
			for stop_id in station_ids[1:]
			if stop_id in coordinates
		]
		if child_coordinates:
			station_coordinate = (
				sum(point[0] for point in child_coordinates) / len(child_coordinates),
				sum(point[1] for point in child_coordinates) / len(child_coordinates),
			)

	now = datetime.now(timezone.utc)
	route_lines = {}
	map_stops = {}
	vehicles = []
	seen_vehicles = set()
	for item in items:
		trip_id = item["run_id"]
		trip = static_info["trips"].get(trip_id)
		trip_stops = static_info["trip_stops"].get(trip_id, [])
		if trip is None or len(trip_stops) < 2:
			continue

		stop_points = []
		for scheduled_stop in trip_stops:
			stop_id = scheduled_stop["stop_id"]
			location = coordinates.get(stop_id)
			if location is None:
				continue
			stop_name = (
				static_info["stops"].get(stop_id, "").strip() or stop_id
			)
			platform = _platform_label(
				static_info["platforms"].get(stop_id, "")
			)
			stop_points.append(
				{
					"id": stop_id,
					"name": stop_name,
					"platform": platform,
					"lat": location[0],
					"lon": location[1],
					"arrival": scheduled_stop["arrival"],
					"departure": scheduled_stop["departure"],
				}
			)
			map_stops.setdefault(
				stop_id,
				{
					"id": stop_id,
					"name": stop_name,
					"platform": platform,
					"lat": location[0],
					"lon": location[1],
				},
			)
		if len(stop_points) < 2:
			continue

		route_id = item["route_id"]
		route = static_info["routes"].get(route_id, {})
		line_key = (
			route_id,
			item["direction"],
			tuple(point["id"] for point in stop_points),
		)
		route_lines.setdefault(
			line_key,
			{
				"id": f"{route_id}:{item['direction']}:{len(route_lines)}",
				"name": item["route_name"],
				"direction": item["direction"],
				"color": _map_color(route.get("color"), "#456A8A"),
				"stops": stop_points,
			},
		)

		vehicle_key = (trip_id, item["service_date"])
		if vehicle_key in seen_vehicles:
			continue
		seen_vehicles.add(vehicle_key)
		try:
			service_day = datetime.strptime(
				item["service_date"], "%Y%m%d"
			).date()
			delay = item.get("delay_seconds") or 0
			delay = int(delay)
		except (TypeError, ValueError):
			delay = 0
			service_day = item["scheduled_departure_utc"].astimezone(
				MELBOURNE_TZ
			).date()
		position = _estimate_trip_location(
			stop_points, service_day, now, timedelta(seconds=delay)
		)
		if position is None:
			continue
		vehicles.append(
			{
				"trip_id": trip_id,
				"route": item["route_name"],
				"route_color": _map_color(
					route.get("color"), "#456A8A"
				),
				"route_text_color": _map_color(
					route.get("text_color"), "#FFFFFF"
				),
				"direction": item["direction"],
				"platform": item["platform"],
				"realtime": item["realtime"],
				"lat": position[0],
				"lon": position[1],
			}
		)

	return {
		"station": {
			"name": items[0]["stop_name"] if items else str(STOP_ID),
			"lat": station_coordinate[0] if station_coordinate else None,
			"lon": station_coordinate[1] if station_coordinate else None,
			"stop_ids": station_ids,
		},
		"routes": list(route_lines.values()),
		"stops": list(map_stops.values()),
		"vehicles": vehicles,
		"departures": _map_departures(items, static_info),
		"updated_at": now.astimezone(MELBOURNE_TZ).isoformat(),
	}


def _map_departures(items, static_info=None):
	"""Format every displayed departure for the map sidebar."""
	if static_info is None:
		static_info = _load_static_info()
	departures = []
	for item in items:
		route = static_info["routes"].get(item["route_id"], {})
		delay = item["delay_seconds"]
		status = _format_delay(delay)
		if delay is not None and delay > 0:
			status_color = "#b42318"
		elif delay is not None and delay < 0:
			status_color = "#b54708"
		elif item["realtime"]:
			status_color = "#18794e"
		else:
			status_color = "#52616b"
		departures.append(
			{
				"route": item["route_name"],
				"route_color": _map_color(
					route.get("color"), "#456a8a"
				),
				"route_text_color": _map_color(
					route.get("text_color"), "#ffffff"
				),
				"destination": item["direction"],
				"departure_time": item[
					"estimated_departure_utc"
				].astimezone(MELBOURNE_TZ).strftime("%H:%M:%S"),
				"departure_timestamp": item[
					"estimated_departure_utc"
				].timestamp(),
				"status": status,
				"status_color": status_color,
				"realtime": item["realtime"],
				"platform": item["platform"],
				"run_id": item["run_id"],
			}
		)
	return departures


def _estimate_trip_location(stops, service_day, now, delay):
	"""Interpolate a scheduled vehicle position between stops."""
	for stop in stops:
		try:
			arrival = _gtfs_time(
				service_day, stop["arrival"]
			).astimezone(timezone.utc) + delay
			departure = _gtfs_time(
				service_day, stop["departure"]
			).astimezone(timezone.utc) + delay
		except (TypeError, ValueError):
			continue
		if arrival <= now <= departure:
			return stop["lat"], stop["lon"]

	for previous, following in zip(stops, stops[1:]):
		try:
			segment_start = _gtfs_time(
				service_day, previous["departure"]
			).astimezone(timezone.utc) + delay
			segment_end = _gtfs_time(
				service_day, following["arrival"]
			).astimezone(timezone.utc) + delay
		except (TypeError, ValueError):
			continue
		if segment_end <= segment_start or not (
			segment_start <= now <= segment_end
		):
			continue
		progress = (now - segment_start).total_seconds() / (
			segment_end - segment_start
		).total_seconds()
		return (
			previous["lat"]
			+ (following["lat"] - previous["lat"]) * progress,
			previous["lon"]
			+ (following["lon"] - previous["lon"]) * progress,
		)
	return None


def _ensure_map_window():
	"""Start the optional local map browser window once."""
	global _map_window
	if not SHOW_MAP:
		return None
	if _map_window is None:
		from ptvmap import MapWindow

		_map_window = MapWindow(
			f"Transport map - {STATIC_MODE} - {STOP_ID}"
		)
		_map_window.start()
	return _map_window


def _update_map_window(map_window, data):
	"""Report map failures without preventing the terminal tracker from running."""
	if map_window is None:
		return None
	try:
		map_window.update(data)
	except Exception as error:
		print(f"Map update failed: {error}", file=sys.stderr)
		map_window.close()
		return None
	return map_window


def _matching_realtime_update(
	trip_id,
	service_date,
	scheduled_departure,
	scheduled_stop_id,
	realtime_by_date,
	realtime_without_date,
):
	"""Match an update to its service date, including feeds without start_date."""
	trip_keys = _trip_keys(trip_id)
	stop_keys = _stop_keys(scheduled_stop_id)
	stop_ids = [str(scheduled_stop_id)] + sorted(
		stop_keys - {str(scheduled_stop_id)}
	)
	exact = [
		realtime_by_date[(key, service_date, stop_id)]
		for key in trip_keys
		for stop_id in stop_ids
		if (key, service_date, stop_id) in realtime_by_date
	]
	if exact:
		return min(exact, key=lambda update: _realtime_distance(
			update, scheduled_departure
		))

	candidates = [
		update
		for key in trip_keys
		for (candidate_key, candidate_stop_id), updates
		in realtime_without_date.items()
		if candidate_key == key and candidate_stop_id in stop_keys
		for update in updates
	]
	if not candidates:
		candidates = [
			update
			for key in trip_keys
			for (candidate_key, candidate_date, candidate_stop_id), update
			in realtime_by_date.items()
			if candidate_key == key
			and candidate_date == service_date
			and candidate_stop_id in stop_keys
		]
	if not candidates:
		return None, None

	update = min(
		candidates,
		key=lambda item: _realtime_distance(item, scheduled_departure),
	)
	if _realtime_distance(update, scheduled_departure) > timedelta(hours=12):
		return None, None
	return update


def _realtime_distance(update, scheduled_departure):
	times = [value[0] for value in update if value is not None]
	return min(
		(abs(value - scheduled_departure) for value in times),
		default=timedelta.max,
	)


def _gtfs_time(day, value):
	"""Convert a GTFS time, which may exceed 24:00:00, to local datetime."""
	hours, minutes, seconds = (int(part) for part in value.split(":"))
	return datetime.combine(day, datetime.min.time(), MELBOURNE_TZ) + timedelta(
		hours=hours, minutes=minutes, seconds=seconds
	)


def _service_active(static_info, service_id, day):
	"""Check weekly GTFS service and date-specific exceptions."""
	date_key = day.strftime("%Y%m%d")
	exceptions = static_info["calendar_dates"].get((service_id, date_key), [])
	if "1" in exceptions:
		return True
	if "2" in exceptions:
		return False
	row = static_info["calendar"].get(service_id)
	if row is None:
		return False
	weekday = day.strftime("%A").lower()
	return (
		row[weekday] == "1"
		and row["start_date"] <= date_key <= row["end_date"]
	)


def _format_delay(delay_seconds):
	"""Format a GTFS delay in a way that distinguishes early and late buses."""
	if delay_seconds is None or delay_seconds == 0:
		return "on time"
	amount = abs(delay_seconds)
	minutes, seconds = divmod(amount, 60)
	duration = f"{minutes}m {seconds:02d}s" if minutes else f"{seconds}s"
	return f"{duration} early" if delay_seconds < 0 else f"{duration} late"


def save_observation(row):
	if not CSV_FILE.exists():
		with CSV_FILE.open("w", newline="", encoding="utf-8") as file:
			writer = csv.DictWriter(file, fieldnames=row.keys())
			writer.writeheader()
			writer.writerow(row)
		return

	with CSV_FILE.open("r", newline="", encoding="utf-8") as file:
		reader = csv.reader(file)
		fieldnames = next(reader, [])
		max_extra_columns = max(
			(
				len(previous_row) - len(fieldnames)
				for previous_row in reader
			),
			default=0,
		)
	if set(row).issubset(fieldnames):
		with CSV_FILE.open("a", newline="", encoding="utf-8") as file:
			csv.DictWriter(file, fieldnames=fieldnames).writerow(row)
		return

	original_fieldnames = list(fieldnames)
	fieldnames.extend(key for key in row if key not in fieldnames)
	legacy_fieldnames = []
	for index in range(1, max_extra_columns + 1):
		while f"legacy_extra_{index}" in fieldnames:
			index += 1
		name = f"legacy_extra_{index}"
		legacy_fieldnames.append(name)
		fieldnames.append(name)
	temporary_path = None
	try:
		with tempfile.NamedTemporaryFile(
			"w",
			newline="",
			encoding="utf-8",
			dir=CSV_FILE.parent,
			delete=False,
		) as temporary_file:
			temporary_path = Path(temporary_file.name)
			writer = csv.DictWriter(temporary_file, fieldnames=fieldnames)
			writer.writeheader()
			with CSV_FILE.open("r", newline="", encoding="utf-8") as source:
				reader = csv.reader(source)
				next(reader, None)
				for previous_values in reader:
					previous_row = dict(
						zip(original_fieldnames, previous_values)
					)
					extra_values = previous_values[len(original_fieldnames):]
					previous_row.update(
						zip(legacy_fieldnames, extra_values)
					)
					writer.writerow(previous_row)
			writer.writerow(row)
		os.replace(temporary_path, CSV_FILE)
	finally:
		if temporary_path is not None and temporary_path.exists():
			temporary_path.unlink()


def main():
	try:
		map_window = _ensure_map_window()
	except Exception as error:
		print(f"Map window unavailable: {error}", file=sys.stderr)
		map_window = None
	tracking_message = (
		f"Tracking stop {STOP_ID}; refreshing every {POLL_SECONDS} seconds. "
		"Press Ctrl+C to stop."
	)
	clear_screen = CLEAR_TERMINAL and sys.stdout.isatty()
	if not clear_screen:
		print(tracking_message)
		_print_disruptions()
	first_clear_refresh = True
	while True:
		try:
			if clear_screen:
				_clear_terminal()
				print(tracking_message)
				if first_clear_refresh:
					_print_disruptions()
					first_clear_refresh = False
			items = departures()
			if map_window is not None:
				map_window = _update_map_window(
					map_window, _build_map_data(items)
				)
			if not items:
				stop_name = _load_static_info()["stops"].get(
					str(STOP_ID), f"stop {STOP_ID}"
				)
				if stop_name == str(STOP_ID):
					print(f"{STOP_ID} not found.")
				else:
					print(
						f"No upcoming departures at {stop_name} "
						f"(stop {STOP_ID})."
					)
			else:
				stop_name = items[0]["stop_name"]
				print(
					f"Upcoming departures at {stop_name}: {len(items)} "
					f"(realtime feed {items[0]['feed_fetched_at'].astimezone(MELBOURNE_TZ).strftime('%H:%M:%S')})"
				)
				for item in items:
					estimated = item["estimated_departure_utc"]
					observed = datetime.now(timezone.utc).isoformat()
					local_departure = estimated.astimezone(MELBOURNE_TZ)
					row = {
						"observed_utc": observed,
						"stop_id": STOP_ID,
						"stop_name": item["stop_name"],
						"platform": item["platform"],
						"route_id": item.get("route_id"),
						"route_name": item.get("route_name"),
						"run_id": item.get("run_id"),
						"direction": item.get("direction"),
						"scheduled_departure_utc": item[
							"scheduled_departure_utc"
						].isoformat(),
						"arrival_utc": item["arrival_utc"].isoformat(),
						"scheduled_arrival_utc": item[
							"scheduled_arrival_utc"
						].isoformat(),
						"estimated_departure_utc": estimated.isoformat(),
						"delay_seconds": (
							item["delay_seconds"]
							if item["delay_seconds"] is not None
							else ""
						),
						"arrival_delay_seconds": (
							item["arrival_delay_seconds"]
							if item["arrival_delay_seconds"] is not None
							else ""
						),
					}
					save_observation(row)
					if row['route_name'] == "Skybus - Melbourne Airport - Melbourne City":
						row['route_name'] = "MCE"
						if row['direction'] != "Melbourne Airport":
							row['direction'] = "Melbourne City Express"
					elif row['route_name'] == "Skybus - Melbourne Airport - Sunshine Railway Station":
						row['route_name'] = "SSX"
						if row['direction'] != "Melbourne Airport":
							row['direction'] = "Sunshine Express"
					elif row['route_name'] == "Skybus - Melbourne Airport - Frankston":
						row['route_name'] = "PEX"
						if row['direction'] != "Melbourne Airport":
							row['direction'] = "PENINSULA EXPRESS to " + row['direction']
					elif row['route_name'] == "SkyBus Eastern Express":
						row['route_name'] = "EEX"
						if row['direction'] != "Melbourne Airport":
							row['direction'] = "EASTERN EXPRESS to " + row['direction']
					elif row['route_name'] == "Skybus - Avalon Airport - Melbourne City":
						row['route_name'] = "AVV"
					else:
						row['direction'] = "To " + row['direction']
					print(
						f"{_colored_route(row['route_name'], item['route_id'], _load_static_info()['routes'])} | ",
						end = ""
					)
					if item["platform"]:
						print(f"{item['platform']} | ", end="")
					print(
						f"{item['route_long_name']} | " if PRINT_LONG_NAME else "",
						end = ""
					)
					print(
						f"{row['direction']} | "
						f"{local_departure.strftime('%H:%M:%S')} "
						f"({_colored_status(item['delay_seconds'], item['realtime'])}) "
						f" | {'realtime' if item['realtime'] else 'scheduled'}"
					)
				print("----------------------------------------------\n")
			time.sleep(POLL_SECONDS)
		except KeyboardInterrupt:
			if map_window is not None:
				map_window.close()
			print("\nStopped.")
			return
		except Exception as error:
			if clear_screen:
				_clear_terminal()
				print(tracking_message)
			print(f"Error: {error}", file=sys.stderr)
			time.sleep(POLL_SECONDS)


if __name__ == "__main__":
	main()
