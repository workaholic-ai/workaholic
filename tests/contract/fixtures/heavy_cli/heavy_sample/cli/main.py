"""Intentional eager imports used to prove the startup boundary test."""

import fastapi
import psycopg

from heavy_sample.server import scheduler

__all__ = ["fastapi", "psycopg", "scheduler"]
