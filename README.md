
# PTV Tracker

Track upcoming Public Transport Victoria (PTV) bus and Melbourne Metro train
departures, including real-time arrival/departure updates and delays.

The project uses the current Transport Victoria Open Data GTFS-Realtime API
with a UUID API key in the `KeyID` request header. It does not use the legacy
PTV `devid` parameter or HMAC signing.

## Features

- Monitor bus stops or Melbourne Metro train stations.
- Monitor Yarra Tram stops and V/Line stations with their dedicated realtime
  feeds.
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

Supported modes (some services may provide scheduled times only when no
matching realtime update is available):

- [X] Buses
- [X] Metro Trains
- [X] Trams
- [X] Coaches
- [X] V/Lines
- [X] SkyBus
- [ ] The Overland

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

Keep the real key only in your local working copy. Do not commit or publish
it. If a key has been exposed, revoke it in the Transport Victoria portal and
create a replacement.

## Running the bus tracker

Edit the configuration near the top of `ptvbus.py`:

```python
PTV_API_KEY = "your-api-key-uuid"
STOP_ID = 19800  # Oakleigh station / Portman St (For 900, 693, 704...)
ROUTE_ID = None  # Find in gtfs zip file, 49-630-aus-1 for Route 630
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

Output Example:

```text
Tracking stop 19800; refreshing every 30 seconds. Press Ctrl+C to stop.
Upcoming departures at Oakleigh Station/Portman St: 20 (realtime feed 11:28:00)
 693  | Belgrave - Oakleigh via Ferntree Gully, Brandon Park | To Belgrave Station | 11:33:00 (on time)  | realtime
 704  | Oakleigh Station - Westall Station via Clayton | To Oakleigh Station | 11:41:00 (on time)  | scheduled
 804  | Dandenong - Chadstone via Wheelers Hill, Oakleigh | To Dandenong Station | 11:42:00 (on time)  | realtime
 900  | Stud Park SC (Rowville) - Caulfield via Monash University, Chadstone (SMARTBUS Service) | To Stud Park SC (Rowville) | 11:42:00 (on time)  | realtime
 704  | Oakleigh Station - Westall Station via Clayton | To Westall Station | 11:48:48 (3m 48s late)  | realtime
```

The default bus API feed is:

```text
https://api.opendata.transport.vic.gov.au/opendata/public-transport/gtfs/realtime/v1/bus/trip-updates
```

## Running the Metro train tracker

Edit `ptvtrain.py` and set a Melbourne train station GTFS stop ID:

```python
PTV_API_KEY = "your-api-key-uuid"
STOP_ID = "vic:rail:OAK"  # Oakleigh station
```

Run it with:

```powershell
python -u .\ptvtrain.py
```

Train station parent IDs are expanded automatically to their platform child
IDs. For example, `vic:rail:OAK` includes the Oakleigh platform stops.

The Metro train tracker uses:

```text
https://api.opendata.transport.vic.gov.au/opendata/public-transport/gtfs/realtime/v1/metro/trip-updates
```

## Running the tram tracker

Use `ptvtram.py` for Yarra Trams. Set the tram stop ID and API key:

```python
PTV_API_KEY = "your-api-key-uuid"
STOP_ID = 17901 # Melbourne Town Hall / Collins St Stop #6 to Melbourne port
```

Run it with:

```powershell
python -u .\ptvtram.py
```

The tram feed is updated less frequently than the bus and Metro train feeds.
Its API uses the `KeyID` header:

```text
https://api.opendata.transport.vic.gov.au/opendata/public-transport/gtfs/realtime/v1/tram/trip-updates
```

## Running the V/Line tracker

Use `ptvline.py` for regional V/Line passenger trains. For example:

```python
PTV_API_KEY = "your-api-key-uuid"
STOP_ID = "vic:rail:BAT-V" # Ballarat V/Line station
```

Run it with:

```powershell
python -u .\ptvline.py
```

The V/Line feed uses the regional static GTFS branch and its own realtime
endpoint:

```text
https://api.opendata.transport.vic.gov.au/opendata/public-transport/gtfs/realtime/v1/vline/trip-updates
```

V/Line uses the `KeyId` request header. `ptvtrain.py` is for Metro Trains and
uses the `KeyID` header, so changing only the GTFS archive is not sufficient.

## Running the SkyBus tracker

Use `ptvskybus.py` for SkyBus services. SkyBus is distributed in the
dedicated `11/google_transit.zip` static GTFS branch and uses the bus
GTFS-Realtime trip-update endpoint.

```python
PTV_API_KEY = ""
STOP_ID = 45511  # Melbourne Airport T1 Skybus/Arrival Dr
```

Run it with:

```powershell
python -u .\ptvskybus.py
```

Useful SkyBus stop IDs include:

- `45511` - Melbourne Airport T1 Skybus/Arrival Dr
- `45513` - Melbourne Airport T3 Skybus/Arrival Dr
- `48578` - Melbourne Airport T4 Skybus/Service Rd
- `23173`, `23175`, `23183` - SkyBus Coach Terminal/Spencer St
- `40272`, `40284` - Avalon Airport/Outside Terminal

SkyBus realtime availability depends on the feed. When no matching realtime
trip update is published, the tracker still displays the static scheduled
departure.

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
- `ptv_tram_punctuality.csv` - tram observations
- `ptv_vline_punctuality.csv` - V/Line observations
- `ptv_skybus_punctuality.csv` - SkyBus observations
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
- Tram and V/Line use different realtime endpoints and authentication headers;
  use `ptvtram.py` or `ptvline.py` rather than changing only the GTFS archive
  list in `ptvtrain.py`.
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
