import logging

LOG_LEVEL = logging.DEBUG

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=LOG_LEVEL
)

log = logging.getLogger("delugram")

# Suppress logs from deluge.core.alertmanager
logging.getLogger("deluge.core.alertmanager").setLevel(logging.INFO)