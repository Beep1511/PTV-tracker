"""Track a Transport Victoria bus departure at a stop.

Fill in the API key below before running this script.
"""

import csv
from io import BytesIO
import os
import pickle
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
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
STOP_ID = 19800                                # e.g. 1127
"""
Examples
15967 Marlborough Street Reserve/Marlborough St | To Chadstone
19800 Oakleigh Station/Portman St
51645 Murrumbeena Station/Murrumbeena Rd | To Moorabbin / Sandringham
16372 Centre Rd/Second St | To Westall
6220 Murrumbeena Rd/North Rd | To Monash
"""
ROUTE_ID = None                                 # e.g. None = all buses     Route ID 49-630-aus-1(for 630), not route number like 630
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
MELBOURNE_TZ = ZoneInfo("Australia/Melbourne")
# ---------------------------------------------------------------------------

_static_info = None
_realtime_debug_reported = False


def _load_static_info():
	"""Load stop names and trip destinations from the official GTFS schedule."""
	global _static_info
	if _static_info is not None:
		return _static_info
	mode = STATIC_MODE
	cache_name = re.sub(r"[^A-Za-z0-9_.-]", "_", str(STOP_ID))
	cache_file = BASE_DIR / f"{STATIC_GTFS_ZIP.stem}_{mode}_{cache_name}.cache"
	cache_signature = (
		4,
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
		routes = {}
		trips = {}
		stop_times = {}
		calendar = {}
		calendar_dates = {}
		stop_ids = set()
		try:
			for schedule in schedule_archives:
				schedule_stops = []
				with schedule.open("stops.txt") as file:
					for row in csv.DictReader(
						(line.decode("utf-8-sig") for line in file)
					):
						stops[row["stop_id"]] = row["stop_name"]
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
							"long_name": row.get("route_long_name", ""),
							"color": row.get("route_color", ""),
							"text_color": row.get("route_text_color", ""),
						}

				with schedule.open("stop_times.txt") as file:
					for row in csv.DictReader(
						(line.decode("utf-8-sig") for line in file)
					):
						if row["stop_id"] in stop_ids:
							stop_times[row["trip_id"]] = {
								"arrival": row["arrival_time"],
								"departure": row["departure_time"],
							}

				with schedule.open("trips.txt") as file:
					for row in csv.DictReader(
						(line.decode("utf-8-sig") for line in file)
					):
						if row["trip_id"] in stop_times:
							trips[row["trip_id"]] = {
								"route_id": row["route_id"],
								"headsign": row.get("trip_headsign", ""),
								"service_id": row["service_id"],
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

	_static_info = {
		"stops": stops,
		"routes": routes,
		"trips": trips,
		"stop_times": stop_times,
		"calendar": calendar,
		"calendar_dates": calendar_dates,
		"stop_ids": stop_ids,
	}
	try:
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
	"""Color realtime status: late red, early orange, on-time green."""
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
		"airport", "arrivals", "bay", "bus", "dr", "drive", "express",
		"railway", "road", "station", "st", "street", "stop", "terminal",
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


def departures():
	"""Return scheduled departures, overlaid with available realtime updates."""
	global _realtime_debug_reported
	feed = _ptv_get()
	feed_fetched_at = datetime.now(timezone.utc)
	static_info = _load_static_info()
	now_local = datetime.now(MELBOURNE_TZ)
	today = now_local.date()
	realtime = {}
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
		if ROUTE_ID is not None and trip.route_id != str(ROUTE_ID):
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
					update.arrival.delay
					if update.arrival.HasField("delay")
					else None,
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
					update.departure.delay
					if update.departure.HasField("delay")
					else None,
				)
				if update.HasField("departure")
				and update.departure.HasField("time")
				else None
			)
			if arrival is None and departure is None:
				continue
			for key in _trip_keys(trip.trip_id):
				realtime[key] = (
					arrival,
					departure,
				)

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
	for trip_id, departure_time in static_info["stop_times"].items():
		trip = static_info["trips"].get(trip_id)
		if trip is None:
			continue
		if _is_terminal_at_stop(trip["headsign"], static_info):
			continue
		if ROUTE_ID is not None and trip["route_id"] != str(ROUTE_ID):
			continue
		if RUN_ID is not None and trip_id != str(RUN_ID):
			continue
		if not _service_active(static_info, trip["service_id"], today):
			continue
		scheduled_arrival = _gtfs_time(
			today, departure_time["arrival"]
		).astimezone(timezone.utc)
		scheduled_departure = _gtfs_time(
			today, departure_time["departure"]
		).astimezone(timezone.utc)
		realtime_arrival, realtime_departure = next(
			(
				realtime[key]
				for key in _trip_keys(trip_id)
				if key in realtime
			),
			(None, None),
		)
		arrival, arrival_delay = realtime_arrival or (
			scheduled_arrival,
			None,
		)
		departure, delay = realtime_departure or (
			scheduled_departure,
			None,
		)
		estimated = departure
		if estimated < datetime.now(timezone.utc):
			continue
		static_items.append(
			{
				"route_id": trip["route_id"],
				"route_name": _route_name(trip["route_id"], static_info["routes"]),
				"route_long_name": static_info["routes"].get(
					trip["route_id"], {}
				).get("long_name", ""),
				"run_id": trip_id,
				"stop_name": static_info["stops"].get(
					str(STOP_ID), f"stop {STOP_ID}"
				),
				"direction": trip["headsign"] or "direction unavailable",
				"estimated_departure_utc": estimated,
				"arrival_utc": arrival,
				"scheduled_arrival_utc": scheduled_arrival,
				"scheduled_departure_utc": scheduled_departure,
				"delay_seconds": delay,
				"arrival_delay_seconds": arrival_delay,
				"realtime": realtime_departure is not None,
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
		)
		previous = unique.get(key)
		if previous is None or item["realtime"] and not previous["realtime"]:
			unique[key] = item
	return sorted(
		unique.values(), key=lambda item: item["estimated_departure_utc"]
	)[:MAX_RESULTS]


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
	exists = CSV_FILE.exists()
	with CSV_FILE.open("a", newline="", encoding="utf-8") as file:
		writer = csv.DictWriter(file, fieldnames=row.keys())
		if not exists:
			writer.writeheader()
		writer.writerow(row)


def main():
	print(
		f"Tracking stop {STOP_ID}; refreshing every {POLL_SECONDS} seconds. "
		"Press Ctrl+C to stop."
	)
	while True:
		try:
			items = departures()
			if not items:
				stop_name = _load_static_info()["stops"].get(
					str(STOP_ID), f"stop {STOP_ID}"
				)
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
					if row['route_name'] == "Skybus - Melbourne Airport - Sunshine Railway Station":
						row['route_name'] = "SSX"
					if row['route_name'] == "Skybus - Melbourne Airport - Frankston":
						row['route_name'] = "PEX"
					if row['route_name'] == "SkyBus Eastern Express":
						row['route_name'] = "EEX"
					if row['route_name'] == "Skybus - Avalon Airport - Melbourne City":
						row['route_name'] = "AVV"
					print(
						f"{_colored_route(row['route_name'], item['route_id'], _load_static_info()['routes'])} | "
						f"{item['route_long_name']} | "
						f"To {row['direction']} | "
						f"{local_departure.strftime('%H:%M:%S')} "
						f"({_colored_status(item['delay_seconds'], item['realtime'])}) "
						f" | {'realtime' if item['realtime'] else 'scheduled'}"
					)
				print("----------------------------------------------\n")
			time.sleep(POLL_SECONDS)
		except KeyboardInterrupt:
			print("\nStopped.")
			return
		except Exception as error:
			print(f"Error: {error}", file=sys.stderr)
			time.sleep(POLL_SECONDS)


if __name__ == "__main__":
	main()
