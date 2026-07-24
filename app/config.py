"""Application configuration + loaders for the YAML config files.

Everything the app needs to know about *which* universities to crawl, *how* to
classify listings, and *how* to behave as a polite crawler lives in the
``config/`` directory as data, not code. This module loads it once and caches it.
"""
from __future__ import annotations

import functools
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root = two levels up from this file (app/config.py -> repo/)
ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"


class Settings(BaseSettings):
    """Runtime settings, overridable via environment variables / .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str = "postgresql+psycopg2://jobs:jobs@localhost:5432/jobs"

    # Crawler identity + conduct (see CRAWLING.md — these are hard requirements)
    crawler_contact_email: str = "jobsboard@example.org"
    crawler_user_agent_name: str = "AusUniJobsBoard"
    request_delay_seconds: float = 4.0          # min gap between requests per domain
    request_timeout_seconds: float = 30.0
    max_retries: int = 4
    respect_robots: bool = True

    # A run that returns fewer than this fraction of the previous successful
    # run's records is treated as a probable failure, not an empty result set.
    min_yield_ratio: float = 0.5

    # Scheduling: how many times/day the full refresh runs (kept low on purpose)
    refresh_times_per_day: int = 3

    @property
    def user_agent(self) -> str:
        return (
            f"{self.crawler_user_agent_name}/{__import__('app').__version__} "
            f"(+{self.crawler_contact_email})"
        )


@functools.lru_cache
def get_settings() -> Settings:
    return Settings()


@functools.lru_cache
def _load_yaml(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / name
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_universities() -> list[dict[str, Any]]:
    """Return the list of university source records."""
    return _load_yaml("universities.yaml")["universities"]


def load_level_bands() -> dict[str, Any]:
    return _load_yaml("level_bands.yaml")


def load_role_families() -> dict[str, Any]:
    return _load_yaml("role_families.yaml")


def load_disciplines() -> dict[str, Any]:
    return _load_yaml("disciplines.yaml")


# Canonical university-group display metadata for the UI presets.
UNIVERSITY_GROUPS = {
    "go8": "Group of Eight",
    "atn": "Australian Technology Network",
    "iru": "Innovative Research Universities",
    "run": "Regional Universities Network",
}

STATES = ["ACT", "NSW", "NT", "QLD", "SA", "TAS", "VIC", "WA"]
