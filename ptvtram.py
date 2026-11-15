"""Track Yarra Trams departures at a tram stop."""

import ptvbus as _common


BASE_DIR = _common.Path(__file__).resolve().parent
PTV_API_KEY = ""
STOP_ID = 17911  # Tram stop GTFS ID
ROUTE_ID = None
RUN_ID = None
POLL_SECONDS = 33
MAX_RESULTS = 20
CSV_FILE = BASE_DIR / "ptv_tram_punctuality.csv"
STATIC_GTFS_ZIP = BASE_DIR / "transport_victoria_gtfs.zip"
STATIC_GTFS_URL = "https://data.ptv.vic.gov.au/downloads/gtfs.zip"
STATIC_ARCHIVE_NAMES = {"3/google_transit.zip"}
STATIC_MODE = "tram"
API_URL = (
	"https://api.opendata.transport.vic.gov.au/"
	"opendata/public-transport/gtfs/realtime/v1/tram/trip-updates"
)
API_KEY_HEADER = "KeyID"
DEBUG_REALTIME = True
PRINT_LONG_NAME = False

def main():
	_common.PTV_API_KEY = PTV_API_KEY
	_common.STOP_ID = STOP_ID
	_common.ROUTE_ID = ROUTE_ID
	_common.RUN_ID = RUN_ID
	_common.POLL_SECONDS = POLL_SECONDS
	_common.MAX_RESULTS = MAX_RESULTS
	_common.CSV_FILE = CSV_FILE
	_common.STATIC_GTFS_ZIP = STATIC_GTFS_ZIP
	_common.STATIC_GTFS_URL = STATIC_GTFS_URL
	_common.STATIC_ARCHIVE_NAMES = STATIC_ARCHIVE_NAMES
	_common.STATIC_MODE = STATIC_MODE
	_common.API_URL = API_URL
	_common.API_KEY_HEADER = API_KEY_HEADER
	_common.CALCULATE_DELAY_FROM_TIMESTAMP = True
	_common.DEBUG_REALTIME = DEBUG_REALTIME
	_common._static_info = None
	_common._realtime_debug_reported = False
	_common.PRINT_LONG_NAME = PRINT_LONG_NAME
	_common.main()


if __name__ == "__main__":
	main()
