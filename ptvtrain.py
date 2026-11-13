"""Track Melbourne Metro train departures at a station.

Set STOP_ID to the station's GTFS stop ID and fill in the API key below.

This script tracks Metro Trains. Use `ptvvline.py` for V/Line services.
"""

import ptvbus as _common


BASE_DIR = _common.Path(__file__).resolve().parent
PTV_API_KEY = ""
STOP_ID = "vic:rail:THL"  # Melbourne train station GTFS stop ID
"""
Examples
"vic:rail:RMD" - Richmond
"vic:rail:SSS" - Southern Cross
"vic:rail:FSY" - Footscray
"vic:rail:WTL" - Westall
"vic:rail:FKN" - Frankston
"vic:rail:BAT-V" - Ballarat
"""
ROUTE_ID = None
RUN_ID = None
POLL_SECONDS = 30
MAX_RESULTS = 20
CSV_FILE = BASE_DIR / "ptv_train_punctuality.csv"
API_URL = (
	"https://api.opendata.transport.vic.gov.au/"
	"opendata/public-transport/gtfs/realtime/v1/metro/trip-updates"
)
STATIC_ARCHIVE_NAMES = {
	"2/google_transit.zip",        # Metro Train
}
STATIC_MODE = "metro"
STATIC_GTFS_ZIP = BASE_DIR / "transport_victoria_gtfs.zip"
STATIC_GTFS_URL = "https://data.ptv.vic.gov.au/downloads/gtfs.zip"


def main():
	_common.PTV_API_KEY = PTV_API_KEY
	_common.STOP_ID = STOP_ID
	_common.ROUTE_ID = ROUTE_ID
	_common.RUN_ID = RUN_ID
	_common.POLL_SECONDS = POLL_SECONDS
	_common.MAX_RESULTS = MAX_RESULTS
	_common.CSV_FILE = CSV_FILE
	_common.API_URL = API_URL
	_common.STATIC_ARCHIVE_NAMES = STATIC_ARCHIVE_NAMES
	_common.STATIC_MODE = STATIC_MODE
	_common.STATIC_GTFS_ZIP = STATIC_GTFS_ZIP
	_common.STATIC_GTFS_URL = STATIC_GTFS_URL
	_common._static_info = None
	_common.main()


if __name__ == "__main__":
	main()
