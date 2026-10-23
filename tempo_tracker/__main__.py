"""Start: python -m tempo_tracker"""

import logging

from .config import LOG_PATH


def main() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=LOG_PATH,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # requests/urllib3 loggen geen headers op INFO, maar voor de zekerheid stil houden.
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    from .app import TempoTrackerApp

    TempoTrackerApp().run()


if __name__ == "__main__":
    main()
