"""Discrete warehouse simulation with explicit task assignments."""

from .instances import from_dict, load_instance
from .engine import Simulation

__all__ = ["Simulation", "from_dict", "load_instance"]
