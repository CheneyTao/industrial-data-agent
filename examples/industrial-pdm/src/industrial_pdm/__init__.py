"""Industrial predictive-maintenance analysis data source."""

from .analyzer import IndustrialAnalyzer
from .db import Database

__all__ = ["Database", "IndustrialAnalyzer"]
__version__ = "2.0.0"
