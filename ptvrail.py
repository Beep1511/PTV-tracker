"""Show Metro and V/Line departures together for a railway station."""

import time
import re
from datetime import datetime, timezone

import ptvbus as _common


PTV_API_KEY = ""
STOP_ID = "vic:rail:SSS"
ROUTE_ID = None
POLL_SECONDS = 30
MAX_RESULTS = 20
PRINT_LONG_NAME = False
PRINT_DISRUPTION = True
CLEAR_TERMINAL = True
SHOW_MAP = True

BASE_DIR = _common.Path(__file__).resolve().parent
STATIC_GTFS_ZIP = BASE_DIR / "transport_victoria_gtfs.zip"
STATIC_GTFS_URL = "https://data.ptv.vic.gov.au/downloads/gtfs.zip"

MODES = (
	{
		"name": "Metro",
		"archive_names": {"2/google_transit.zip"},
		"static_mode": "metro",
		"api_url": (
			"https://api.opendata.transport.vic.gov.au/"
			"opendata/public-transport/gtfs/realtime/v1/metro/trip-updates"
		),
		"api_key_header": "KeyID",
		"debug": False,
	},
	{
		"name": "V/Line",
		"archive_names": {"1/google_transit.zip"},
		"static_mode": "vline",
		"api_url": (
			"https://api.opendata.transport.vic.gov.au/"
			"opendata/public-transport/gtfs/realtime/v1/vline/trip-updates"
		),
		"api_key_header": "KeyId",
		"debug": True,
	},
)


def _configure(mode):
	_common.PTV_API_KEY = PTV_API_KEY
	_common.STOP_ID = STOP_ID
	_common.ROUTE_ID = ROUTE_ID
	_common.RUN_ID = None
	_common.MAX_RESULTS = MAX_RESULTS
	_common.CSV_FILE = BASE_DIR / f"ptv_{mode['static_mode']}_punctuality.csv"
	_common.STATIC_GTFS_ZIP = STATIC_GTFS_ZIP
	_common.STATIC_GTFS_URL = STATIC_GTFS_URL
	_common.STATIC_ARCHIVE_NAMES = mode["archive_names"]
	_common.STATIC_MODE = mode["static_mode"]
	_common.API_URL = mode["api_url"]
	_common.API_KEY_HEADER = mode["api_key_header"]
	_common.DEBUG_REALTIME = mode["debug"]
	_common.PRINT_LONG_NAME = PRINT_LONG_NAME
	_common.PRINT_DISRUPTION = True
	_common.CLEAR_TERMINAL = CLEAR_TERMINAL
	_common.SHOW_MAP = SHOW_MAP
	_common._static_info = None
	_common._realtime_debug_reported = False


def _rail_destination(item):
	"""Remove a loop suffix and identify services terminating at this stop."""
	direction = item.get("direction", "")
	base_direction = re.sub(r"\s+via\s+.*$", "", direction, flags=re.IGNORECASE)
	stop_name = item.get("stop_name", "").split("/", 1)[0]
	normalise = _common._normalise_location
	stop_words = normalise(stop_name)
	destination_words = normalise(base_direction)
	while stop_words and stop_words[-1] in {"station", "railway", "terminal"}:
		stop_words.pop()
	while destination_words and destination_words[-1] in {
		"station", "railway", "terminal"
	}:
		destination_words.pop()
	if destination_words == stop_words:
		return None
	return direction


def _print_departure(mode, item):
	_configure(mode)
	local_departure = item["estimated_departure_utc"].astimezone(
		_common.MELBOURNE_TZ
	)
	route_name = _common._colored_route(
		item["route_name"], item["route_id"], _common._load_static_info()["routes"]
	)
	direction = f"To {item['direction']}"
	long_name = (
		f"{item['route_long_name']} | " if PRINT_LONG_NAME else ""
	)
	print(f"{route_name} | ", end="")
	if item["platform"]:
		print(f"{item['platform']} | ", end="")
	print(
		f"{long_name}{direction} | "
		f"{local_departure.strftime('%H:%M:%S')} "
		f"({_common._colored_status(item['delay_seconds'], item['realtime'])}) | "
		f"{'realtime' if item['realtime'] else 'scheduled'}"
	)


def _save_departure(mode, item):
	"""Persist one combined-mode observation using the shared CSV format."""
	observed = datetime.now(timezone.utc).isoformat()
	_common.save_observation(
		{
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
			"estimated_departure_utc": item[
				"estimated_departure_utc"
			].isoformat(),
			"delay_seconds": item["delay_seconds"] or "",
			"arrival_delay_seconds": item["arrival_delay_seconds"] or "",
		}
	)


def main():
	_common.CLEAR_TERMINAL = CLEAR_TERMINAL
	_common.SHOW_MAP = SHOW_MAP
	try:
		map_window = _common._ensure_map_window()
	except Exception as error:
		print(f"Map window unavailable: {error}", file=_common.sys.stderr)
		map_window = None
	tracking_message = (
		f"Tracking Metro and V/Line; refreshing every {POLL_SECONDS} seconds. "
		"Press Ctrl+C to stop."
	)
	clear_screen = CLEAR_TERMINAL and _common.sys.stdout.isatty()
	if not clear_screen:
		print(tracking_message)
		if PRINT_DISRUPTION:
			for mode in MODES:
				if mode["name"] == "V/Line":
					continue
				_configure(mode)
				_common._print_disruptions()
	first_clear_refresh = True

	while True:
		try:
			if clear_screen:
				_common._clear_terminal()
				print(tracking_message)
				if first_clear_refresh and PRINT_DISRUPTION:
					for mode in MODES:
						if mode["name"] == "V/Line":
							continue
						_configure(mode)
						_common._print_disruptions()
				first_clear_refresh = False
			all_items = []
			for mode in MODES:
				_configure(mode)
				for item in _common.departures():
					destination = _rail_destination(item)
					if destination is None:
						continue
					item["direction"] = destination
					all_items.append((item["estimated_departure_utc"], mode, item))
			all_items.sort(key=lambda value: value[0])
			realtime_items = [
				value for value in all_items if value[2]["realtime"]
			]
			scheduled_items = [
				value for value in all_items if not value[2]["realtime"]
			]
			selected = realtime_items[:MAX_RESULTS]
			selected.extend(scheduled_items[:MAX_RESULTS - len(selected)])
			all_items = sorted(selected, key=lambda value: value[0])
			if map_window is not None:
				map_data = {
					"station": None,
					"routes": [],
					"stops": [],
					"vehicles": [],
					"departures": [],
					"updated_at": datetime.now(
						_common.MELBOURNE_TZ
					).isoformat(),
				}
				for mode in MODES:
					mode_items = [
						item
						for _, item_mode, item in all_items
						if item_mode == mode
					]
					if not mode_items and map_data["station"] is not None:
						continue
					_configure(mode)
					mode_data = _common._build_map_data(mode_items)
					if mode_items and (
						map_data["station"] is None
						or map_data["station"]["lat"] is None
					):
						map_data["station"] = mode_data["station"]
					elif map_data["station"] is not None:
						map_data["station"]["stop_ids"] = list(
							dict.fromkeys(
								map_data["station"].get("stop_ids", [])
								+ mode_data["station"].get("stop_ids", [])
							)
						)
					map_data["routes"].extend(mode_data["routes"])
					map_data["stops"].extend(mode_data["stops"])
					map_data["vehicles"].extend(mode_data["vehicles"])
					map_data["departures"].extend(
						_common._map_departures(
							mode_items, _common._load_static_info()
						)
					)
				unique_stops = {
					(
						stop["id"],
						stop["lat"],
						stop["lon"],
						stop["platform"],
					): stop
					for stop in map_data["stops"]
				}
				map_data["stops"] = list(unique_stops.values())
				map_data["departures"].sort(
					key=lambda departure: departure[
						"departure_timestamp"
					]
				)
				map_window = _common._update_map_window(
					map_window, map_data
				)
			if not all_items:
				_configure(MODES[0])
				stop_name = _common._load_static_info()["stops"].get(
					str(STOP_ID), f"stop {STOP_ID}"
				)
				if stop_name == str(STOP_ID):
					print(f"{STOP_ID} not found.")
				else:
					print(f"No upcoming departures at {stop_name}.")
			else:
				stop_name = all_items[0][2]["stop_name"]
				feed_time = max(
					item["feed_fetched_at"] for _, _, item in all_items
				).astimezone(_common.MELBOURNE_TZ).strftime("%H:%M:%S")
				print(
					f"Upcoming departures at {stop_name}: "
					f"{min(len(all_items), MAX_RESULTS)} "
					f"(realtime feed {feed_time})"
				)
				for _, mode, item in all_items[:MAX_RESULTS]:
					_save_departure(mode, item)
					_print_departure(mode, item)
				print("----------------------------------------------\n")
			time.sleep(POLL_SECONDS)
		except KeyboardInterrupt:
			if map_window is not None:
				map_window.close()
			print("\nStopped.")
			return
		except Exception as error:
			if clear_screen:
				_common._clear_terminal()
				print(tracking_message)
			print(f"Error: {error}")
			time.sleep(POLL_SECONDS)


if __name__ == "__main__":
	main()
