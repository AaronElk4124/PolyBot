import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Kalshi credentials — key ID (short) + RSA private key (long)
    api_key:    str = os.getenv("KALSHI_API_KEY_ID", "")
    api_secret: str = os.getenv("KALSHI_PRIVATE_KEY", "")

    # Risk controls
    max_bet_usdc:       float = float(os.getenv("MAX_BET_USDC", "25.0"))
    max_open_positions: int   = int(os.getenv("MAX_OPEN_POSITIONS", "5"))
    edge_threshold:     float = float(os.getenv("EDGE_THRESHOLD", "0.08"))
    kelly_fraction:     float = float(os.getenv("KELLY_FRACTION", "0.25"))
    max_bankroll_usdc:  float = float(os.getenv("MAX_BANKROLL_USDC", "500.0"))
    poll_interval:      int   = int(os.getenv("POLL_INTERVAL", "3600"))

    def validate_live(self) -> None:
        for key in ("KALSHI_API_KEY", "KALSHI_API_SECRET"):
            if not os.getenv(key):
                raise EnvironmentError(f"Missing required env var: {key}")
