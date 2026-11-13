
# PTV Tracker

Track upcoming Public Transport Victoria (PTV) bus and Melbourne Metro train
departures, including real-time arrival/departure updates and delays.

The project uses the current Transport Victoria Open Data GTFS-Realtime API
with a UUID API key in the `KeyID` request header. It does not use the legacy
PTV `devid` parameter or HMAC signing.

## Features

- Monitor bus stops or Melbourne Metro train stations.
- Display the public route or train line, destination, local Melbourne time,
  and delay status.
- Show all upcoming departures at a location, not only the first one.
- Combine the official static GTFS schedule with GTFS-Realtime updates.
  Scheduled services remain visible even when the realtime feed has no update.
- Handle train station parent IDs and platform child IDs.
- Display realtime arrival and departure data separately in the CSV log.
- Cache the parsed static GTFS index for fast subsequent launches.
- Refresh the realtime feed every 30 seconds by default.
- Use GTFS route colors for terminal route labels and color-code realtime
  status: red for late, orange for early, and green for realtime on time.

Able to track:

- [X] Buses
- [X] Metro Trains
- [ ] Trams
- [ ] Coaches
- [x] V/Lines

## Requirements

- Python 3.10 or newer
- A Transport Victoria Open Data Portal account and API key
- Internet access

Install the Python dependencies:

```powershell
python -m pip install gtfs-realtime-bindings tzdata
```

`tzdata` is useful on Windows, where the IANA timezone database may not be
installed. The script uses `Australia/Melbourne` for displayed times.

## Getting an API key

1. Create an account on the
   [Transport Victoria Open Data Portal](https://opendata.transport.vic.gov.au/).
2. Create or copy the UUID API key from your profile.
3. Put the key directly in the script configuration:

```python
PTV_API_KEY = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
```

Do not commit a real API key to a public repository. If a key has ever been
published, revoke it and create a replacement immediately.

## Running the bus tracker

Edit the configuration near the top of `ptvbus.py`:

```python
PTV_API_KEY = "your-api-key-uuid"
STOP_ID = 19800
ROUTE_ID = None
RUN_ID = None
POLL_SECONDS = 30
MAX_RESULTS = 20
```

Then run:

```powershell
python -u .\ptvbus.py
```

The script prints the configured stop name, route number, destination,
departure time in Melbourne local time, delay status, and whether the result
came from realtime data or the static schedule.

Example:

```text
Tracking stop 19800; refreshing every 30 seconds. Press Ctrl+C to stop.
Upcoming departures: 3 (realtime feed 17:10:32)
Oakleigh Station/Portman St | 900 | To Stud Park SC (Rowville) | departure=17:13:00 (on time) | realtime
```

The default bus API feed is:

```text
https://api.opendata.transport.vic.gov.au/opendata/public-transport/gtfs/realtime/v1/bus/trip-updates
```

## Running the train tracker

Edit `ptvtrain.py` and set a Melbourne train station GTFS stop ID:

```python
PTV_API_KEY = "your-api-key-uuid"
STOP_ID = "vic:rail:OAK"  # Oakleigh
```

Run it with:

```powershell
python -u .\ptvtrain.py
```

Train station parent IDs are expanded automatically to their platform child
IDs. For example, `vic:rail:OAK` includes the Oakleigh platform stops.

The train tracker uses:

```text
https://api.opendata.transport.vic.gov.au/opendata/public-transport/gtfs/realtime/v1/metro/trip-updates
```

## Finding stop IDs

The static GTFS schedule contains the official stop and station identifiers.
The scripts download it automatically from:

```text
https://data.ptv.vic.gov.au/downloads/gtfs.zip
```

The archive contains mode-specific feeds. Bus stop IDs are numeric. Train
stations commonly use IDs such as `vic:rail:OAK`, while platform stops may use
numeric or platform-specific IDs.

The scripts also print the resolved stop name. Use that output to verify that
the configured ID is the location you intended.

You can look up the Stop ID for your desired location on [Moovit](https://moovitapp.com/).

- **Example:** The [Doncaster Rd/Blackburn Rd](https://moovitapp.com/index/en-gb/public_transportation-Doncaster_Rd_Blackburn_Rd-Melbourne-stop_7991625-2803) stop has an ID of `4012`.

> [!NOTE]
> **Platform & Bay Limitation:** This script tracks a single specific Stop ID at a time. It **cannot** simultaneously track buses departing from different bays or platforms within the same interchange/station, as each bay is assigned a unique Stop ID.

## Output and caching

All generated files are stored next to the scripts:

- `ptv_punctuality.csv` - bus observations
- `ptv_train_punctuality.csv` - train observations
- `transport_victoria_gtfs.zip` - downloaded static GTFS data
- `transport_victoria_gtfs_*.cache` - parsed static GTFS indexes

The generated files are ignored by Git. The GTFS archive is large and should
not be committed. The static index is invalidated automatically when the GTFS
archive changes or the configured stop/mode changes.

## Configuration notes

- `ROUTE_ID = None` monitors all routes at the stop.
- Set `ROUTE_ID` to a route ID from the static GTFS data to filter results.
- `RUN_ID = None` monitors all trips. Set it only when tracking one exact
  trip ID.
- `POLL_SECONDS` controls how often the realtime feed is requested. The API
  has rate limits; do not reduce it unnecessarily.
- `MAX_RESULTS` limits the number of upcoming departures displayed and logged.

The terminal output uses departure time because that is the time passengers
use when deciding whether they can catch the service. Arrival time and its
delay remain available in the CSV output.

When run in an interactive terminal, route labels use the `route_color` and
`route_text_color` values from GTFS. Realtime status colors are red for late,
orange for early, and green for realtime on time. Colors are automatically
disabled when output is redirected. Set the `NO_COLOR` environment variable to
disable them manually:

```powershell
$env:NO_COLOR = "1"
python -u .\ptvbus.py
```

## Data sources and API behavior

This project uses:

- [Transport Victoria Open Data Portal](https://opendata.transport.vic.gov.au/)
- [GTFS Schedule data](https://data.ptv.vic.gov.au/downloads/gtfs.zip)
- Transport Victoria GTFS-Realtime Trip Updates
- [GTFS Realtime specification](https://gtfs.org/documentation/realtime/)

GTFS-Realtime is an update feed, not a complete timetable. Therefore the
program starts with the static schedule and overlays realtime arrival and
departure values when available. A service marked `scheduled` is still a
valid scheduled departure; it simply did not have a matching realtime update
in the latest feed.

## Troubleshooting

### HTTP 401 or 403

- Confirm that the key is a new UUID key from the Transport Victoria Open Data
  Portal.
- Confirm that the key is assigned to the `KeyID` header by the script.
- Do not add `devid`, `signature`, or HMAC parameters.
- Check that the key has not been revoked or exposed publicly.

### No upcoming departures

- Check the printed stop name and ID.
- Confirm the mode-specific script is being used.
- For train stations, use the parent station ID where possible so platform
  child IDs are included.
- Remember that services may not operate every day; the static feed contains
  service calendars and date exceptions.

### Slow first launch

The first launch may take time while the static GTFS archive is parsed. The
parsed result is cached beside the script, so subsequent launches are much
faster. Delete the relevant cache file if you want to force a rebuild.

> [!WARNING]
> - **Directory Isolation:** Running this script generates numerous cache files. We strongly recommend placing the `.py` files in a dedicated directory.
> - **File Dependencies:** `ptvtrain.py` depends on functions defined in `ptvbus.py`. Ensure both files are downloaded and located in the same directory before execution.

## License

This project is released under the MIT License. See [LICENSE](./LICENSE).
