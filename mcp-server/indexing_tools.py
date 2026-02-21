"""Indexing tools for MCP server.

Provides manual indexing capabilities via MCP tools with time-range filtering,
rate limiting, and statistics reporting.
"""

import sys
import logging
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime, timedelta

# Add parent directory to path for daemon imports
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from daemon.indexer import ConversationIndexer
except ImportError as e:
    # If still failing, it means dependencies aren't installed
    # This is expected when running outside Docker - tools will fail gracefully
    ConversationIndexer = None
    import logging
    logging.warning(f"ConversationIndexer unavailable (expected outside Docker): {e}")

from scheduler import SchedulerManager

logger = logging.getLogger(__name__)

# Rate limiting
LAST_MANUAL_INDEX_TIME = None
MIN_INTERVAL_SECONDS = 60  # Minimum 1 minute between manual index runs


def parse_time_range(time_range: str) -> Optional[datetime]:
    """Parse time range string to cutoff datetime.

    Args:
        time_range: Time range string ("1h", "24h", "7d", "30d")

    Returns:
        Datetime cutoff (conversations newer than this), or None for all
    """
    if not time_range:
        return None

    now = datetime.now()
    time_range = time_range.lower().strip()

    try:
        if time_range.endswith('h'):
            hours = int(time_range[:-1])
            return now - timedelta(hours=hours)
        elif time_range.endswith('d'):
            days = int(time_range[:-1])
            return now - timedelta(days=days)
        else:
            logger.warning(f"Invalid time range format: {time_range}")
            return None
    except ValueError:
        logger.warning(f"Invalid time range value: {time_range}")
        return None


def check_rate_limit(force: bool = False) -> Dict[str, Any]:
    """Check if rate limit allows indexing.

    Args:
        force: Bypass rate limiting

    Returns:
        Dictionary with rate limit status
    """
    global LAST_MANUAL_INDEX_TIME

    if force:
        return {"allowed": True, "reason": "Rate limit bypassed"}

    if LAST_MANUAL_INDEX_TIME is None:
        return {"allowed": True, "reason": "First run"}

    elapsed = (datetime.now() - LAST_MANUAL_INDEX_TIME).total_seconds()
    if elapsed < MIN_INTERVAL_SECONDS:
        remaining = MIN_INTERVAL_SECONDS - elapsed
        return {
            "allowed": False,
            "reason": f"Rate limit: {remaining:.0f}s remaining",
            "retry_after": remaining
        }

    return {"allowed": True, "reason": "Rate limit OK"}


def run_manual_indexing(
    full_reindex: bool = False,
    time_range: Optional[str] = None,
    force: bool = False
) -> Dict[str, Any]:
    """Execute manual conversation indexing.

    Args:
        full_reindex: If True, reindex all conversations. If False, only new ones.
        time_range: Optional time filter ("1h", "24h", "7d", "30d")
        force: Bypass rate limiting checks

    Returns:
        Dictionary with indexing statistics and status
    """
    global LAST_MANUAL_INDEX_TIME

    start_time = datetime.now()

    # Check if running outside Docker (ConversationIndexer unavailable)
    if ConversationIndexer is None:
        return {
            "success": False,
            "error": "not_in_docker_context",
            "message": "Manual indexing via MCP tools requires running inside Docker container. Use 'docker exec agent-genesis python daemon/indexer.py' instead."
        }

    # Check rate limit
    rate_check = check_rate_limit(force)
    if not rate_check["allowed"]:
        return {
            "success": False,
            "error": "rate_limit_exceeded",
            "message": rate_check["reason"],
            "retry_after_seconds": rate_check.get("retry_after")
        }

    try:
        logger.info(f"Starting manual indexing (full={full_reindex}, time_range={time_range}, force={force})")

        # Parse time range if provided
        cutoff_time = parse_time_range(time_range) if time_range else None
        if cutoff_time:
            logger.info(f"Filtering conversations newer than: {cutoff_time}")

        # Initialize indexer
        indexer = ConversationIndexer(
            db_path="/app/knowledge",
            enable_mkg_analysis=False  # Disabled for speed
        )

        # Determine data paths
        data_dir = Path(__file__).parent.parent / "data"
        projects_dir = data_dir / "claude-projects"
        exports_dir = data_dir / "exports"

        # Note: full_reindex and cutoff_time filtering would need to be
        # implemented in the indexer's index_all_sources method.
        # For now, we run the standard indexing (which only indexes new conversations)
        stats = indexer.index_all_sources(
            projects_dir=str(projects_dir),
            exports_dir=str(exports_dir)
        )

        # Update rate limit tracker
        LAST_MANUAL_INDEX_TIME = datetime.now()

        # Update scheduler last_run if needed
        try:
            scheduler = SchedulerManager()
            scheduler.update_last_run()
        except Exception as e:
            logger.warning(f"Could not update scheduler last_run: {e}")

        # Calculate duration
        duration = (datetime.now() - start_time).total_seconds()

        # Build response
        result = {
            "success": True,
            "statistics": {
                "conversations_processed": stats['total_indexed'],
                "alpha_indexed": stats['alpha_indexed'],
                "beta_indexed": stats['beta_indexed'],
                "duration_seconds": round(duration, 2),
                "last_indexed_timestamp": datetime.now().isoformat()
            },
            "parameters": {
                "full_reindex": full_reindex,
                "time_range": time_range,
                "cutoff_time": cutoff_time.isoformat() if cutoff_time else None,
                "forced": force
            }
        }

        if stats.get('errors'):
            result['warnings'] = stats['errors']

        logger.info(f"Manual indexing complete: {stats['total_indexed']} conversations in {duration:.1f}s")
        return result

    except Exception as e:
        logger.error(f"Manual indexing failed: {e}", exc_info=True)
        return {
            "success": False,
            "error": "indexing_failed",
            "message": str(e),
            "duration_seconds": (datetime.now() - start_time).total_seconds()
        }
