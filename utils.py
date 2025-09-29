# utils.py – Optimized Announcement Scheduler with Telethon (JSON Store Only)
# ------------------------------------------------------------------------
import json
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple, Any, Union
from functools import wraps
import time
import os
import traceback

from telethon import TelegramClient, utils, events
from telethon.errors import FloodWaitError, RPCError, SessionPasswordNeededError
from telethon.tl.functions.messages import GetDialogFiltersRequest
from telethon.tl.types import DialogFilter
from telethon.network import ConnectionTcpFull
from telethon.sessions import MemorySession, StringSession
from aiogram import Bot

from announcement_store import announcement_store, AnnouncementData
from driver_store import driver_store, Driver
from timing_manager import get_timing

# Load settings from config file
with open('config.json', 'r', encoding='utf-8') as f:
    settings = json.load(f)

# Create global bot instance to avoid session leaks
bot = Bot(token=settings['BOT_TOKEN'])

# Get encoding settings
ENCODING_SETTINGS = settings.get('ENCODING_SETTINGS', {
    'DEFAULT_ENCODING': 'utf-8',
    'UNICODE_NORMALIZATION': 'NFC',
    'ENSURE_ASCII': False
})

# No filesystem sessions: StringSession only; prevent multi-process sharing
SESSION_DIR = None

# Configure logging to write to both console and file
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('announcement_debug.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# Global variables for client management
_client_locks: Dict[int, asyncio.Lock] = {}
_client_api_locks: Dict[int, asyncio.Lock] = {}  # prevents concurrent API calls per driver
CLIENT_CACHE: Dict[int, TelegramClient] = {}
_client_last_used: Dict[int, datetime] = {}
_driver_rate_window: Dict[int, List[float]] = {}  # timestamps of recent sends per driver
_driver_send_queues: Dict[int, asyncio.Queue] = {}
_driver_workers_started: Dict[int, bool] = {}
_MAX_MSGS_PER_WINDOW = get_timing('max_messages_per_window', 8)
_RATE_WINDOW_SECONDS = get_timing('rate_limit_window_seconds', 60)
_BATCH_DELAY_SECONDS = get_timing('batch_delay_seconds', 3)
_PEER_BATCH_SIZE = get_timing('peer_batch_size', 5)
_MAX_CONCURRENT_ANNOUNCEMENTS = get_timing('max_concurrent_announcements', 3)
_FOLDER_CACHE_TTL_SECONDS = int(settings.get('FOLDER_CACHE_TTL_SECONDS', 7200))  # default 2 hours
_folder_cache: Dict[str, Tuple[float, List[Dict]]] = {}

class ClientCacheEntry:
    def __init__(self, client: TelegramClient):
        self.client = client
        self.last_used = datetime.now()

class PeerError:
    def __init__(self, peer_info: Dict, error: str):
        self.peer_info = peer_info
        self.error = error

class AnnouncementState:
    def __init__(self):
        self.is_running = False
        self.last_run = None
        self.next_run = None
        self.errors = []
        self.success_count = 0
        self.failure_count = 0

    def should_run(self, interval_min: int) -> bool:
        """Check if announcement should run based on interval."""
        if not self.is_running:
            log.debug(f"Announcement not running")
            return False
            
        now = datetime.now()
        if self.last_run is None:
            log.debug(f"First run for announcement")
            return True
            
        # Calculate next run time based on interval
        next_run = self.last_run + timedelta(minutes=interval_min)
        should_run = now >= next_run
        log.debug(f"Announcement should_run check: last_run={self.last_run}, next_run={next_run}, now={now}, should_run={should_run}")
        return should_run

    def update_run_time(self):
        """Update last run time."""
        self.last_run = datetime.now()

# Create global state instance
state = AnnouncementState()

# Banned channels tracking (now handled in SUCCESS_RATE_STATS)
SUCCESS_RATE_STATS = {
    'total_attempts': 0,
    'successful_sends': 0,
    'failed_sends': 0,
    'banned_channels': set()
}

class DBCache:
    def __init__(self):
        self._cache = {}
        self._locks = {}

    async def get(self, key: str) -> Optional[Any]:
        return self._cache.get(key)

    async def set(self, key: str, value: Any) -> None:
        self._cache[key] = value

    async def delete(self, key: str) -> None:
        self._cache.pop(key, None)

    async def get_lock(self, key: str) -> asyncio.Lock:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

# Create global cache instance
db_cache = DBCache()


WORKER_ID   = int(os.getenv("WORKER_ID", "1"))
SHARD_COUNT = int(os.getenv("SHARD_COUNT", "1"))

def _is_mine(driver_id: int) -> bool:
    # sticky partition: each driver handled by exactly one worker
    return (driver_id % SHARD_COUNT) == ((WORKER_ID - 1) % SHARD_COUNT)


def db_operation(func=None, *, query=None, cache_key: Optional[str] = None, invalidate_keys: Optional[List[str]] = None):
    if func is None:
        return lambda f: db_operation(f, query=query, cache_key=cache_key, invalidate_keys=invalidate_keys)

    @wraps(func)
    async def wrapper(*args, **kwargs):
        # Get cache key if provided
        key = None
        if cache_key:
            if callable(cache_key):
                key = cache_key(*args, **kwargs)
            else:
                key = cache_key

        # Try to get from cache first
        if key:
            cached_value = await db_cache.get(key)
            if cached_value is not None:
                return cached_value

        # Execute the function
        result = await func(*args, **kwargs)

        # Cache the result if we have a key
        if key:
            await db_cache.set(key, result)

        # Invalidate other cache keys if needed
        if invalidate_keys:
            for invalid_key in invalidate_keys:
                await db_cache.delete(invalid_key)

        return result

    return wrapper

async def get_folder_peers(client: TelegramClient, folder_title: str) -> List[Dict]:
    """Get all peers from a specific folder."""
    try:
        # Cache by user + folder name
        driver_id = None
        for uid, cached in CLIENT_CACHE.items():
            if cached is client:
                driver_id = uid
                break
        
        # Normalize folder title for cache key
        import unicodedata
        normalized_folder_title = unicodedata.normalize('NFC', folder_title.lower())
        cache_key = f"folder:{driver_id}:{normalized_folder_title}"
        now = time.time()
        cached_entry = _folder_cache.get(cache_key)
        if cached_entry and now - cached_entry[0] < _FOLDER_CACHE_TTL_SECONDS:
            # Return normalized peers from cache
            log.info(f"Using cached peers for folder '{folder_title}' ({len(cached_entry[1])} peers)")
            return cached_entry[1]

        # Get all dialog filters (folders)
        result = await client(GetDialogFiltersRequest())
        filters = getattr(result, 'filters', [])

        # Find the folder by name
        folder = None
        for f in filters:
            if not isinstance(f, DialogFilter):
                continue
            current_title = getattr(f, 'title', '')
            if hasattr(current_title, 'text'):
                current_title = current_title.text
            else:
                current_title = str(current_title)
            
            # Normalize both titles for proper Unicode comparison
            normalized_current = unicodedata.normalize('NFC', current_title.strip().lower())
            normalized_folder = unicodedata.normalize('NFC', folder_title.lower())
            
            if normalized_current == normalized_folder:
                folder = f
                log.info(f"Found folder: '{current_title}' (ID: {f.id})")
                break

        if not folder:
            log.error(f"Folder '{folder_title}' not found")
            # Log available folders for debugging
            available_folders = []
            for f in filters:
                if isinstance(f, DialogFilter):
                    current_title = getattr(f, 'title', '')
                    if hasattr(current_title, 'text'):
                        current_title = current_title.text
                    else:
                        current_title = str(current_title)
                    available_folders.append(current_title)
            log.info(f"Available folders: {available_folders}")
            log.error(f"Requested folder '{folder_title}' not found in available folders")
            return []

        # Get peers from the folder
        peers = []
        include_peers = getattr(folder, 'include_peers', [])
        log.info(f"Found {len(include_peers)} peers in folder")
        
        if not include_peers:
            log.warning(f"Folder '{folder_title}' has no peers")
            return []
        
        for i, peer in enumerate(include_peers):
            try:
                entity = await client.get_entity(peer)
                title = getattr(entity, 'title', None) or getattr(entity, 'first_name', None) or str(entity.id)
                
                # Normalize title for proper Unicode handling
                if title and isinstance(title, str):
                    title = unicodedata.normalize('NFC', title)
                
                peer_info = {
                    'id': entity.id,
                    'type': type(entity).__name__,
                    'title': title
                }
                peers.append(peer_info)
                log.info(f"Added peer {i+1}/{len(include_peers)}: {title} (ID: {entity.id}, Type: {type(entity).__name__})")
            except Exception as e:
                log.error(f"Error getting entity for peer {peer}: {e}")
        
        log.info(f"Successfully processed {len(peers)}/{len(include_peers)} peers from folder '{folder_title}'")

        # Normalize peer titles for cache
        normalized_peers = []
        for peer in peers:
            normalized_peer = peer.copy()
            if 'title' in normalized_peer and normalized_peer['title']:
                normalized_peer['title'] = unicodedata.normalize('NFC', normalized_peer['title'])
            normalized_peers.append(normalized_peer)
        
        _folder_cache[cache_key] = (now, normalized_peers)
        log.info(f"Cached {len(normalized_peers)} peers for folder '{folder_title}'")
        return normalized_peers

    except Exception as e:
        log.error(f"Error getting folder peers: {e}")
        import traceback
        log.error(f"Traceback: {traceback.format_exc()}")
        return []

async def process_peer_batch(
    client: TelegramClient,
    peers: List[Dict],
    text: str,
    batch_id: str,
    announcement_id: int
) -> Tuple[int, int, List[PeerError]]:
    """Process a batch of peers for message sending with improved rate limiting."""
    # Ensure proper text encoding for Unicode characters (including Cyrillic)
    import unicodedata
    if text:
        text = unicodedata.normalize('NFC', text)
        log.info(f"Processing batch {batch_id} for announcement {announcement_id} with {len(peers)} peers")
    
    success_count = 0
    failure_count = 0
    errors = []
    
    # Process peers in smaller sub-batches to avoid overwhelming the API
    sub_batch_size = _PEER_BATCH_SIZE
    for i in range(0, len(peers), sub_batch_size):
        sub_batch = peers[i:i + sub_batch_size]
        log.info(f"Processing sub-batch {i//sub_batch_size + 1}/{(len(peers) + sub_batch_size - 1)//sub_batch_size} with {len(sub_batch)} peers")
        
        for j, peer in enumerate(sub_batch):
            try:
                # Check if channel is banned
                if peer['id'] in SUCCESS_RATE_STATS['banned_channels']:
                    log.info(f"Skipping banned channel {peer.get('title', 'Unknown')} (ID: {peer['id']})")
                    failure_count += 1
                    continue
                    
                log.info(f"Processing peer {i+j+1}/{len(peers)}: {peer.get('title', 'Unknown')} (ID: {peer['id']})")
                success, error = await send_message_with_retry(client, peer, text)
                if success:
                    success_count += 1
                    log.info(f"Successfully sent to peer {i+j+1}/{len(peers)}: {peer.get('title', 'Unknown')}")
                else:
                    failure_count += 1
                    if error:
                        errors.append(error)
                        log.error(f"Failed to send to peer {i+j+1}/{len(peers)}: {peer.get('title', 'Unknown')} - {error.error}")
                        
                        # If it's a flood wait error, break the entire batch to avoid more flood waits
                        if "FLOOD_WAIT" in str(error.error):
                            log.warning(f"Flood wait detected, stopping batch processing to avoid more flood waits")
                            return success_count, failure_count, errors
                            
            except Exception as e:
                failure_count += 1
                errors.append(PeerError(peer, str(e)))
                log.error(f"Exception sending to peer {i+j+1}/{len(peers)}: {peer.get('title', 'Unknown')} - {e}")
        
        # Add delay between sub-batches to respect rate limits
        if i + sub_batch_size < len(peers):
            log.info(f"Waiting {_BATCH_DELAY_SECONDS} seconds before next sub-batch...")
            await asyncio.sleep(_BATCH_DELAY_SECONDS)

    log.info(f"Batch {batch_id} completed: {success_count} success, {failure_count} failure")
    return success_count, failure_count, errors

async def send_message_with_retry(
    client: TelegramClient,
    peer: Dict,
    text: str,
    max_retries: int = 1
) -> Tuple[bool, Optional[PeerError]]:
    """Send message to a peer with retry logic."""
    # Determine driver id from cached mapping by reverse lookup
    driver_id = None
    for uid, cached in CLIENT_CACHE.items():
        if cached is client:
            driver_id = uid
            break

    # Ensure single in-flight API call per driver
    api_lock = None
    if driver_id is not None:
        api_lock = _client_api_locks.setdefault(driver_id, asyncio.Lock())

    async def _rate_limit():
        if driver_id is None:
            return
        now = time.time()
        window = _driver_rate_window.setdefault(driver_id, [])
        cutoff = now - _RATE_WINDOW_SECONDS
        while window and window[0] < cutoff:
            window.pop(0)
        if len(window) >= _MAX_MSGS_PER_WINDOW:
            # Correct formula: oldest timestamp + window - now
            sleep_for = (window[0] + _RATE_WINDOW_SECONDS) - now
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
        window.append(now)

    # Ensure text is properly encoded for Unicode characters (including Cyrillic)
    try:
        # Normalize Unicode text to ensure proper encoding
        import unicodedata
        text = unicodedata.normalize('NFC', text)
        
        # Log the text for debugging (first 100 characters)
        log.info(f"Sending message to peer {peer['id']} ({peer.get('title', 'Unknown')})")
        
    except Exception as e:
        log.error(f"Error normalizing text: {e}")

    # Update total attempts
    SUCCESS_RATE_STATS['total_attempts'] += 1
    
    try:
        if api_lock:
            async with api_lock:
                await _rate_limit()
                # Use parse_mode=None to ensure raw text is sent
                result = await client.send_message(peer['id'], text, parse_mode=None)
                log.info(f"Message sent successfully to {peer.get('title', 'Unknown')} (ID: {peer['id']}) on attempt 1")
                SUCCESS_RATE_STATS['successful_sends'] += 1
                return True, None
        else:
            result = await client.send_message(peer['id'], text, parse_mode=None)
            log.info(f"Message sent successfully to {peer.get('title', 'Unknown')} (ID: {peer['id']}) on attempt 1")
            SUCCESS_RATE_STATS['successful_sends'] += 1
            return True, None
    except FloodWaitError as e:
        # Slow mode / floodwait: return special reason so it can be kept in pack for next cycle
        log.warning(f"Flood wait error for peer {peer['id']}: {e.seconds} seconds - will retry next cycle")
        # Add extra delay to prevent immediate retry
        return False, PeerError(peer, f"FLOOD_WAIT:{e.seconds}")
        
    except RPCError as e:
        msg = str(e).lower()
        log.error(f"RPC error for peer {peer['id']}: {e}")

        # Treat these as "never send here again" (blacklist)
        ban_markers = [
            "banned from sending messages",
            "chat_write_forbidden",
            "user_banned_in_channel",
            "chat_admin_required",
            "channel_private",
            "peer_id_invalid",
        ]
        if any(m in msg for m in ban_markers):
            channel_id = peer['id']
            SUCCESS_RATE_STATS['banned_channels'].add(channel_id)
            log.warning(f"Channel {peer.get('title', 'Unknown')} (ID: {channel_id}) blacklisted")

        # Handle slowmode variants that sometimes arrive as RPCError text
        if "slowmode_wait" in msg:
            # Best effort: extract seconds if present, else default small pause
            import re
            m = re.search(r"(\d+)", msg)
            secs = int(m.group(1)) if m else 10
            return False, PeerError(peer, f"FLOOD_WAIT:{secs}")

        SUCCESS_RATE_STATS['failed_sends'] += 1
        return False, PeerError(peer, str(e))


async def process_single_announcement(announcement: AnnouncementData) -> Tuple[int, int]:
    """Process a single announcement by sending persistent 10-peer packs sequentially per run.
    - Processes packs one by one in the same call
    - FLOOD_WAIT peers are kept for next interval (do not count as failure)
    - Other errors: remove peer from pack, notify driver, record failure (cycle 0 only)
    - When a pack is fully sent/cleaned, delete it from task_packs
    - When all packs finished, send cycle report (cycle 0) and regenerate packs for next cycle
    """
    try:
        log.info(f"Starting to process announcement {announcement.id} for driver {announcement.driver_id}")
        
        # Ensure proper text encoding for Unicode characters (including Cyrillic)
        import unicodedata
        if announcement.text:
            announcement.text = unicodedata.normalize('NFC', announcement.text)
            log.info(f"Processing announcement for driver {announcement.driver_id}")
        
        # Get driver
        driver = await driver_store.get_driver(announcement.driver_id)
        if not driver:
            log.error(f"Driver {announcement.driver_id} not found")
            return 0, 0

        log.info(f"Found driver: {driver.full_name}")

        # Get client (StringSession-backed, cached)
        client = await get_cached_client(driver.tg_id)
        if not client:
            log.error(f"Failed to get client for driver {driver.tg_id}. Please ensure the driver is logged in.")
            return 0, 0

        log.info(f"Got client for driver {driver.tg_id}")

        # Get peers from folder (cached) and ensure deterministic ordering
        peers = await get_folder_peers(client, announcement.folder_title)
        if not peers:
            log.error(f"No peers found in folder {announcement.folder_title} for driver {driver.tg_id}")
            return 0, 0
        # Sort by id for stable packs
        peers = sorted(peers, key=lambda p: int(p.get('id', 0)))

        # Helper to (re)generate task packs of 10
        def _generate_task_packs(all_peers: List[Dict], cycle: int) -> List[Dict[str, Any]]:
            packs: List[Dict[str, Any]] = []
            pack_size = _PEER_BATCH_SIZE  # Use configurable batch size
            for idx in range(0, len(all_peers), pack_size):
                batch = all_peers[idx:idx + pack_size]
                packs.append({
                    'pack_id': f"{announcement.id}:{cycle}:{idx//pack_size}",
                    'peers': batch,
                    'sent_peer_ids': [],
                    'completed': False
                })
            return packs

        # Initialize or validate existing packs against current peers snapshot
        packs_changed = False
        if not getattr(announcement, 'task_packs', None):
            announcement.task_packs = _generate_task_packs(peers, cycle=0)
            announcement.current_cycle = 0
            packs_changed = True
            log.info(f"Generated {len(announcement.task_packs)} task packs for announcement {announcement.id}")
        else:
            # Validate peers set consistency; if membership drastically changed, rebuild packs for next cycle
            existing_peer_ids: Set[int] = set()
            for p in announcement.task_packs:
                for peer in p.get('peers', []):
                    existing_peer_ids.add(int(peer['id']))
            current_peer_ids: Set[int] = {int(p['id']) for p in peers}
            if existing_peer_ids != current_peer_ids:
                # Rebuild packs using current peers, but only if all current packs are completed; otherwise keep current to finish
                all_completed = all(p.get('completed') for p in announcement.task_packs)
                if all_completed:
                    announcement.current_cycle = int(getattr(announcement, 'current_cycle', 0)) + 1
                    announcement.task_packs = _generate_task_packs(peers, cycle=announcement.current_cycle)
                    packs_changed = True
                    log.info(f"Peers changed. Rebuilt task packs for new cycle {announcement.current_cycle} (count={len(announcement.task_packs)})")

        if packs_changed:
            await announcement_store.update_announcement(announcement)

        total_success = 0
        total_failure = 0

        # Process all current packs once (snapshot) so we don't loop infinitely on paused packs
        packs_snapshot = list(announcement.task_packs or [])
        for next_pack in packs_snapshot:
            # Skip if this pack was completed/deleted meanwhile
            if next_pack.get('completed'):
                continue
            if next_pack.get('pack_id') not in {p.get('pack_id') for p in (announcement.task_packs or [])}:
                continue

            sent_ids: Set[int] = set(int(x) for x in next_pack.get('sent_peer_ids', []))
            pack_peers: List[Dict] = list(next_pack.get('peers', []))
            unsent_peers: List[Dict] = [p for p in pack_peers if int(p['id']) not in sent_ids and int(p['id']) not in SUCCESS_RATE_STATS['banned_channels']]

            saw_flood_wait = False

            for peer in unsent_peers:
                try:
                    success, error = await send_message_with_retry(client, peer, announcement.text)
                    if success:
                        total_success += 1
                        sent_ids.add(int(peer['id']))
                        next_pack['sent_peer_ids'] = list(sent_ids)
                        await announcement_store.update_announcement(announcement)
                    else:
                        if error and isinstance(error, PeerError) and isinstance(error.error, str) and error.error.startswith('FLOOD_WAIT:'):
                            saw_flood_wait = True
                            log.info(f"Slow mode for {peer.get('title','?')} ({peer['id']}), will retry next interval")
                            continue
                        else:
                            total_failure += 1
                            if error:
                                log.error(f"Pack {next_pack.get('pack_id')} failed for peer {peer.get('title','?')} ({peer['id']}): {error.error}")
                                if announcement.current_cycle == 0:
                                    if announcement.failed_peers is None:
                                        announcement.failed_peers = []
                                    announcement.failed_peers.append({
                                        'id': int(peer['id']),
                                        'title': peer.get('title'),
                                        'reason': error.error if isinstance(error.error, str) else str(error.error),
                                        'cycle': announcement.current_cycle
                                    })
                                try:
                                    await bot.send_message(chat_id=driver.tg_id, text=f"❌ '{peer.get('title','?')}' guruhiga yuborilmadi. Sabab: {error.error}")
                                except Exception:
                                    pass
                            next_pack['peers'] = [pp for pp in next_pack['peers'] if int(pp['id']) != int(peer['id'])]
                            await announcement_store.update_announcement(announcement)
                except Exception as e:
                    total_failure += 1
                    log.error(f"Unexpected error sending to peer {peer.get('title','?')} ({peer['id']}): {e}")
                    if announcement.current_cycle == 0:
                        if announcement.failed_peers is None:
                            announcement.failed_peers = []
                        announcement.failed_peers.append({
                            'id': int(peer['id']),
                            'title': peer.get('title'),
                            'reason': str(e),
                            'cycle': announcement.current_cycle
                        })
                    try:
                        await bot.send_message(chat_id=driver.tg_id, text=f"❌ '{peer.get('title','?')}' guruhiga yuborilmadi. Sabab: {str(e)}")
                    except Exception:
                        pass
                    next_pack['peers'] = [pp for pp in next_pack['peers'] if int(pp['id']) != int(peer['id'])]
                    await announcement_store.update_announcement(announcement)

            pack_peers_after: List[Dict] = list(next_pack.get('peers', []))
            if len(pack_peers_after) == 0 or len(sent_ids) >= len(pack_peers_after):
                next_pack['completed'] = True
                await announcement_store.update_announcement(announcement)
                announcement.task_packs = [p for p in (announcement.task_packs or []) if p.get('pack_id') != next_pack.get('pack_id')]
                await announcement_store.update_announcement(announcement)
                continue
            # If FLOOD_WAIT seen, leave this pack for next run and continue to next pack
            if saw_flood_wait:
                log.info(f"Pack {next_pack.get('pack_id')} paused due to FLOOD_WAIT; moving on to next pack")

                continue
            
            # Add configurable timeout between packs
            pack_timeout = get_timing('pack_timeout_seconds', 5)
            await asyncio.sleep(pack_timeout)

        # If after processing snapshot no packs remain, close cycle
        if not (announcement.task_packs or []):
            await announcement_store.update_announcement(announcement)
            await _maybe_send_cycle_report(announcement, driver)
            announcement.current_cycle = int(getattr(announcement, 'current_cycle', 0)) + 1
            announcement.task_packs = _generate_task_packs(peers, cycle=announcement.current_cycle)
            await announcement_store.update_announcement(announcement)

        log.info(f"Announcement {announcement.id} run summary: {total_success} success, {total_failure} failure")
        return total_success, total_failure
    except Exception as e:
        log.error(f"Error processing announcement {announcement.id}: {e}")
        import traceback
        log.error(f"Traceback: {traceback.format_exc()}")
        return 0, 0

async def _maybe_send_cycle_report(announcement: AnnouncementData, driver: Driver) -> None:
    """Send first-cycle summary stats to the driver (once per completed cycle 0)."""
    try:
        # Count totals for the cycle
        if announcement.current_cycle != 0:
            return
        # Total peers equals sum of peers in packs when cycle 0 was created
        total_peers = 0
        for p in announcement.task_packs or []:
            total_peers += len(p.get('peers', []))
        # Sent peers across packs
        sent_peers = 0
        for p in announcement.task_packs or []:
            sent_peers += len(p.get('sent_peer_ids', []))
        # Build report text
        failed = [fp for fp in (announcement.failed_peers or []) if int(fp.get('cycle', 0)) == 0]
        failed_count = len(failed)
        success_count = sent_peers
        report_lines = []
        report_lines.append(f"📊 Sikl 1 statistikasi: {success_count}/{total_peers}")
        if failed_count > 0:
            # Aggregate reasons
            reason_map: Dict[str, List[str]] = {}
            for it in failed:
                reason = it.get('reason', 'unknown')
                reason_map.setdefault(reason, []).append(it.get('title') or str(it.get('id')))
            report_lines.append("")
            report_lines.append(f"{failed_count} guruhga yuborilmadi:")
            for reason, names in reason_map.items():
                report_lines.append(f"{reason}")
                # list up to 20 names to avoid too long message
                show_names = names[:20]
                for nm in show_names:
                    report_lines.append(f"- {nm}")
                if len(names) > 20:
                    report_lines.append(f"… va yana {len(names)-20} ta")
        text = "\n".join(report_lines)
        # Send to driver
        await bot.send_message(chat_id=driver.tg_id, text=text)
    except Exception as e:
        log.error(f"Error sending cycle report: {e}")

async def execute_due_announcements() -> None:
    """Execute announcements that are due to run."""
    try:
        log.info("Starting execute_due_announcements...")
        import os  # local import in case the module top doesn't have it

        # 1) Load active announcements
        announcements = await announcement_store.get_active_announcements()

        # Works for both dataclass objects and legacy dicts
        def _driver_id_of(ann):
            try:
                return ann.driver_id
            except AttributeError:
                return ann["driver_id"]

        # 2) Shard filter (unless cooperative "steal" mode is enabled)
        STEAL_MODE = os.getenv("STEAL_MODE", "0") == "1"
        if not STEAL_MODE:
            announcements = [a for a in announcements if _is_mine(_driver_id_of(a))]

        log.info(
            f"Found {len(announcements)} active announcements to process "
            f"(worker {WORKER_ID}/{SHARD_COUNT}, steal_mode={STEAL_MODE})"
        )
        if not announcements:
            log.debug("No active announcements found")
            return

        # 3) Concurrency guard (per-process)
        semaphore = asyncio.Semaphore(_MAX_CONCURRENT_ANNOUNCEMENTS)

        async def process_with_semaphore(announcement):
            async with semaphore:
                try:
                    log.info(f"Processing announcement {announcement.id} for driver {announcement.driver_id}")

                    # Get or init per-announcement state
                    announcement_state = await db_cache.get(f"announcement_state:{announcement.id}")
                    if not announcement_state:
                        announcement_state = AnnouncementState()
                        announcement_state.is_running = True
                        await db_cache.set(f"announcement_state:{announcement.id}", announcement_state)
                        log.info(f"Created new announcement state for {announcement.id}")

                    # Interval check
                    log.info(
                        f"Checking if announcement {announcement.id} should run "
                        f"(interval: {announcement.interval_min} min)"
                    )
                    if announcement_state.should_run(announcement.interval_min):
                        log.info(f"Announcement {announcement.id} is due to run (interval: {announcement.interval_min} min)")
                        success, failure = await process_single_announcement(announcement)
                        announcement_state.update_run_time()
                        await db_cache.set(f"announcement_state:{announcement.id}", announcement_state)
                        log.info(f"Announcement {announcement.id} processed: {success} success, {failure} failure")
                    else:
                        log.info(f"Announcement {announcement.id} is not due to run yet")
                except Exception as e:
                    log.error(f"Error processing announcement {getattr(announcement, 'id', '?')}: {e}")
                    import traceback
                    log.error(f"Traceback: {traceback.format_exc()}")

        # 4) Run them
        await asyncio.gather(*(process_with_semaphore(a) for a in announcements), return_exceptions=True)

    except Exception as e:
        log.error(f"Error executing announcements: {e}")
        import traceback
        log.error(f"Traceback: {traceback.format_exc()}")

    """Execute announcements that are due to run."""
    try:
        log.info("Starting execute_due_announcements...")
        
        # Get all active announcements
        announcements = await announcement_store.get_active_announcements()
        def _driver_id_of(ann):
    # Works for dataclass objects and legacy dicts
            return getattr(ann, "driver_id", ann["driver_id"])

# Only apply sticky sharding when STEAL_MODE is off
        STEAL_MODE = os.getenv("STEAL_MODE", "0") == "1"
        if not STEAL_MODE:
            announcements = [a for a in announcements if _is_mine(_driver_id_of(a))]

        log.info(
            f"Found {len(announcements)} active announcements to process "
            f"(worker {WORKER_ID}/{SHARD_COUNT}, steal_mode={STEAL_MODE})"
        )
        anns = [a for a in announcements if _is_mine(getattr(a, "driver_id", a.get("driver_id")))]
        log.info(f"Found {len(anns)} active announcements to process (worker {WORKER_ID}/{SHARD_COUNT})")
        if not announcements:
            log.debug("No active announcements found")
            return

        log.info(f"Found {len(announcements)} active announcements to process")

        # Process announcements with semaphore to limit concurrent executions
        semaphore = asyncio.Semaphore(_MAX_CONCURRENT_ANNOUNCEMENTS)  # Configurable max concurrent announcements

        async def process_with_semaphore(announcement):
            async with semaphore:
                try:
                    log.info(f"Processing announcement {announcement.id} for driver {announcement.driver_id}")
                    
                    # Get announcement state
                    announcement_state = await db_cache.get(f"announcement_state:{announcement.id}")
                    if not announcement_state:
                        announcement_state = AnnouncementState()
                        announcement_state.is_running = True
                        await db_cache.set(f"announcement_state:{announcement.id}", announcement_state)
                        log.info(f"Created new announcement state for {announcement.id}")

                    # Check if announcement should run based on interval
                    log.info(f"Checking if announcement {announcement.id} should run (interval: {announcement.interval_min} min)")
                    if announcement_state.should_run(announcement.interval_min):
                        log.info(f"Announcement {announcement.id} is due to run (interval: {announcement.interval_min} min)")
                        success, failure = await process_single_announcement(announcement)
                        announcement_state.update_run_time()
                        await db_cache.set(f"announcement_state:{announcement.id}", announcement_state)
                        log.info(f"Announcement {announcement.id} processed: {success} success, {failure} failure")
                    else:
                        log.info(f"Announcement {announcement.id} is not due to run yet")
                except Exception as e:
                    log.error(f"Error processing announcement {announcement.id}: {e}")
                    import traceback
                    log.error(f"Traceback: {traceback.format_exc()}")

        # Create tasks for all announcements
        tasks = [process_with_semaphore(announcement) for announcement in announcements]
        await asyncio.gather(*tasks, return_exceptions=True)

    except Exception as e:
        log.error(f"Error executing announcements: {e}")
        import traceback
        log.error(f"Traceback: {traceback.format_exc()}")

async def scheduler_loop() -> None:
    """Main scheduler loop with improved error handling and rate limiting."""
    log.info("Scheduler loop started")
    consecutive_errors = 0
    max_consecutive_errors = 5
    
    while True:
        try:
            log.info("Scheduler iteration starting...")
            await execute_due_announcements()
            log.info("Scheduler iteration completed")
            consecutive_errors = 0  # Reset error counter on success
                
        except Exception as e:
            consecutive_errors += 1
            log.error(f"Error in scheduler loop (attempt {consecutive_errors}/{max_consecutive_errors}): {e}")
            import traceback
            log.error(f"Traceback: {traceback.format_exc()}")
            
            # If too many consecutive errors, wait longer before retrying
            if consecutive_errors >= max_consecutive_errors:
                log.error(f"Too many consecutive errors ({consecutive_errors}), waiting 5 minutes before retry")
                await asyncio.sleep(300)  # Wait 5 minutes
                consecutive_errors = 0  # Reset counter
            else:
                # Wait longer on errors to avoid rapid retries
                await asyncio.sleep(30)
                continue
                
        scheduler_interval = get_timing('scheduler_interval_seconds', 60)  # Increased default to 60 seconds
        await asyncio.sleep(scheduler_interval)

async def ensure_scheduler_running():
    """Ensure the scheduler is running."""
    log.info("Starting scheduler and cache cleanup tasks")
    
    # Start scheduler loop
    scheduler_task = asyncio.create_task(scheduler_loop())
    log.info("Scheduler loop task created")
    
    # Start cache cleanup loop
    cache_task = asyncio.create_task(_cache_cleanup_loop())
    log.info("Cache cleanup task created")
    
    log.info("Scheduler and cache cleanup tasks started successfully")
    
    # Wait a moment to ensure tasks are running
    await asyncio.sleep(1)
    log.info("Scheduler initialization completed")

async def _driver_worker(driver_id: int) -> None:
    """Single worker per driver that serializes sends and enforces rate limits."""
    queue = _driver_send_queues.setdefault(driver_id, asyncio.Queue())
    while True:
        peer, text = await queue.get()
        try:
            client = await get_cached_client(driver_id)
            # Reuse existing send with retry (which already rate limits + locks)
            await send_message_with_retry(client, peer, text)
        except Exception as e:
            log.error(f"Worker send error for {driver_id}: {e}")
        finally:
            queue.task_done()

async def enqueue_driver_send(driver_id: int, peer: Dict, text: str) -> None:
    """Public API: enqueue a send for a driver."""
    if driver_id not in _driver_workers_started:
        _driver_workers_started[driver_id] = True
        asyncio.create_task(_driver_worker(driver_id))
    await _driver_send_queues.setdefault(driver_id, asyncio.Queue()).put((peer, text))

async def notify_admin(
    message: str,
    notification_type: str = 'INFO',
    error: Optional[Exception] = None,
    announcement_id: Optional[int] = None,
    driver_id: Optional[int] = None
) -> None:
    """Send notification to admin."""
    try:
        # Ensure proper text encoding for Unicode characters (including Cyrillic)
        import unicodedata
        if message:
            message = unicodedata.normalize('NFC', message)
        
        # Format message
        formatted_message = f"[{notification_type}] {message}"
        if error:
            formatted_message += f"\nError: {str(error)}"
        if announcement_id:
            formatted_message += f"\nAnnouncement ID: {announcement_id}"
        if driver_id:
            formatted_message += f"\nDriver ID: {driver_id}"

        # Send notification
        await send_telegram_notification(formatted_message, notification_type, error)

    except Exception as e:
        log.error(f"Error sending admin notification: {e}")


async def send_telegram_notification(
    message: str,
    notification_type: str,
    error: Optional[Exception] = None
) -> bool:
    """Send notification via Telegram."""
    try:
        # Ensure proper text encoding for Unicode characters (including Cyrillic)
        import unicodedata
        if message:
            message = unicodedata.normalize('NFC', message)
        
        for admin_id in settings['ADMINS']:
            try:
                await bot.send_message(
                    chat_id=admin_id,
                    text=message,
                    parse_mode="HTML"
                )
            except Exception as e:
                log.error(f"Error sending notification to admin {admin_id}: {e}")
        return True
    except Exception as e:
        log.error(f"Error sending Telegram notification: {e}")
        return False

async def get_cached_driver(driver_id: int) -> Optional[Driver]:
    """Get driver from cache or storage."""
    try:
        # Try cache first
        cached_driver = await db_cache.get(f"driver:{driver_id}")
        if cached_driver:
            return cached_driver

        # Get from storage
        driver = await driver_store.get_driver(driver_id)
        if driver:
            await db_cache.set(f"driver:{driver_id}", driver)
        return driver
    except Exception as e:
        log.error(f"Error getting cached driver {driver_id}: {e}")
        return None

async def get_active_announcements() -> List[AnnouncementData]:
    """Get all active announcements."""
    return await announcement_store.get_active_announcements()

async def start_announcement(driver_id: int) -> bool:
    """Start an announcement for a driver."""
    try:
        announcement = await announcement_store.get_announcement(driver_id)
        if not announcement:
            return False

        # Create new announcement state
        announcement_state = AnnouncementState()
        announcement_state.is_running = True
        announcement_state.last_run = None  # Reset last run time
        
        # Save state to cache
        await db_cache.set(f"announcement_state:{announcement.id}", announcement_state)
        
        # Update announcement in store
        announcement.is_running = True
        await announcement_store.update_announcement(announcement)
        return True
    except Exception as e:
        log.error(f"Error starting announcement for driver {driver_id}: {e}")
        return False

async def stop_announcement(driver_id: int) -> bool:
    """Stop an announcement for a driver."""
    try:
        announcement = await announcement_store.get_announcement(driver_id)
        if not announcement:
            return False

        # Get and update announcement state
        announcement_state = await db_cache.get(f"announcement_state:{announcement.id}")
        if announcement_state:
            announcement_state.is_running = False
            await db_cache.set(f"announcement_state:{announcement.id}", announcement_state)
        
        # Update announcement in store
        announcement.is_running = False
        await announcement_store.update_announcement(announcement)
        return True
    except Exception as e:
        log.error(f"Error stopping announcement for driver {driver_id}: {e}")
        return False

async def get_cached_client(user_id: int) -> TelegramClient:
    """Get or create a cached Telegram client for a user."""
    # Get or create lock for this user
    lock = _client_locks.setdefault(user_id, asyncio.Lock())
    
    async with lock:
        try:
            # Check if client exists in cache
            if user_id in CLIENT_CACHE:
                client = CLIENT_CACHE[user_id]
                if client.is_connected():
                    _client_last_used[user_id] = datetime.now()
                    log.debug(f"Using cached client for user {user_id}")
                    return client
                else:
                    # Try to reconnect
                    try:
                        log.debug(f"Attempting to reconnect client for user {user_id}")
                        await client.connect()
                        _client_last_used[user_id] = datetime.now()
                        log.debug(f"Successfully reconnected client for user {user_id}")
                        return client
                    except Exception as e:
                        log.error(f"Failed to reconnect client for user {user_id}: {e}")
                        # Remove failed client from cache
                        del CLIENT_CACHE[user_id]
            
            # Get driver data
            driver = await driver_store.get_driver(user_id)
            if not driver:
                raise RuntimeError("Driver not found")
            
            log.info(f"Creating new client for driver {user_id} ({driver.full_name})")
            
            # Create new client with retries (StringSession only)
            max_attempts = 3
            attempt = 0
            last_error = None
            
            while attempt < max_attempts:
                try:
                    # Get or create session string
                    session_string = driver.session_string
                    if not session_string:
                        # Create new session
                        session = StringSession()
                        session_string = session.save()
                        # Save the session string to driver
                        driver.session_string = session_string
                        await driver_store.update_driver(driver)
                    else:
                        session = StringSession(session_string)
                    
                    # Create new client
                    client = TelegramClient(
                        session,
                        driver.api_id,
                        driver.api_hash,
                        device_model="Taxi Bot",
                        system_version="Windows 10",
                        app_version="1.0",
                        lang_code="uz",
                        connection_retries=3,
                        retry_delay=1,
                        connection=ConnectionTcpFull,
                        auto_reconnect=True,
                        sequential_updates=True,
                        # Add encoding settings for better Unicode support
                        request_retries=3,
                        timeout=30,
                        # Ensure proper text encoding
                        use_ipv6=False
                    )
                    
                    # Connect with timeout
                    log.debug(f"Connecting client for user {user_id}")
                    await asyncio.wait_for(client.connect(), timeout=10)
                    
                    # Check if we need to sign in
                    if not await client.is_user_authorized():
                        log.info(f"Client for user {user_id} is not authorized, attempting sign in")
                        # Try to sign in with stored credentials
                        try:
                            await client.sign_in(phone=driver.phone)
                            # If 2FA is enabled, we'll need to handle that
                            if driver.two_factor_code:
                                await client.sign_in(code=driver.two_factor_code)
                        except SessionPasswordNeededError:
                            if driver.password:
                                await client.sign_in(password=driver.password)
                            else:
                                raise RuntimeError("2FA password required but not provided")
                    else:
                        log.debug(f"Client for user {user_id} is already authorized")
                    
                    # Verify connection
                    if not client.is_connected():
                        raise ConnectionError("Failed to establish connection")
                    
                    # Cache the client
                    CLIENT_CACHE[user_id] = client
                    _client_last_used[user_id] = datetime.now()
                    log.info(f"Successfully created and cached client for user {user_id}")
                    return client
                    
                except Exception as e:
                    last_error = e
                    attempt += 1
                    log.warning(f"Failed to create client for user {user_id} on attempt {attempt}: {e}")
                    if attempt < max_attempts:
                        # Wait before retry with exponential backoff
                        wait_time = 2 ** attempt
                        log.debug(f"Waiting {wait_time} seconds before retry")
                        await asyncio.sleep(wait_time)
                    else:
                        log.error(f"Failed to create client after {max_attempts} attempts for user {user_id}: {e}")
                        raise RuntimeError(f"Failed to get client for driver {user_id}. Please ensure the driver is logged in.")
                        
        except Exception as e:
            log.error(f"Error getting cached client for user {user_id}: {e}")
            import traceback
            log.error(f"Traceback: {traceback.format_exc()}")
            raise RuntimeError(f"Failed to get client for driver {user_id}. Please ensure the driver is logged in.")

# Add a new function to properly clean up clients
async def cleanup_client(user_id: int) -> None:
    """Properly clean up a client connection."""
    if user_id in CLIENT_CACHE:
        try:
            client = CLIENT_CACHE[user_id]
            if client.is_connected():
                log.debug(f"Disconnecting client for user {user_id}")
                await client.disconnect()
            del CLIENT_CACHE[user_id]
            log.debug(f"Cleaned up client for user {user_id}")
        except Exception as e:
            log.error(f"Error cleaning up client for user {user_id}: {e}")

# Modify the cache cleanup loop to be more aggressive
async def _cache_cleanup_loop():
    """Periodically clean up stale cache entries."""
    log.info("Cache cleanup loop started")
    while True:
        try:
            # Clean up stale driver cache entries
            driver_cache_keys = [key for key in list(db_cache._cache.keys()) if key.startswith("driver:")]
            if driver_cache_keys:
                log.debug(f"Cleaning up {len(driver_cache_keys)} driver cache entries")
                for key in driver_cache_keys:
                    await db_cache.delete(key)

            # Clean up stale client cache entries
            client_cache_count = len(CLIENT_CACHE)
            if client_cache_count > 0:
                log.debug(f"Checking {client_cache_count} client cache entries")
                for user_id in list(CLIENT_CACHE.keys()):
                    client = CLIENT_CACHE[user_id]
                    if not client.is_connected():
                        log.debug(f"Cleaning up disconnected client for user {user_id}")
                        await cleanup_client(user_id)
                    else:
                        # Check if client has been idle for too long
                        try:
                            last_activity = _client_last_used.get(user_id, datetime.now())
                            if (datetime.now() - last_activity) > timedelta(minutes=30):
                                log.debug(f"Cleaning up idle client for user {user_id}")
                                await cleanup_client(user_id)
                        except:
                            log.debug(f"Cleaning up client with error for user {user_id}")
                            await cleanup_client(user_id)

        except Exception as e:
            log.error(f"Error in cache cleanup: {e}")
            import traceback
            log.error(f"Traceback: {traceback.format_exc()}")

        cache_cleanup_interval = get_timing('cache_cleanup_interval_minutes', 1) * 60
        await asyncio.sleep(cache_cleanup_interval)  # Run every N minutes
