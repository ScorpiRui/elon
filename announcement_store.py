
import json
import os
from datetime import datetime
from typing import Dict, List, Optional, Any
import asyncio
from dataclasses import dataclass, asdict
import logging

try:
    import fcntl  
except Exception:
    fcntl = None

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('announcement_debug.log', encoding='utf-8'),
              logging.StreamHandler()]
)
log = logging.getLogger(__name__)

# Load settings
with open('config.json', 'r', encoding='utf-8') as f:
    settings = json.load(f)

ENCODING_SETTINGS = settings.get('ENCODING_SETTINGS', {
    'DEFAULT_ENCODING': 'utf-8',
    'UNICODE_NORMALIZATION': 'NFC',
    'ENSURE_ASCII': False
})

@dataclass
class AnnouncementData:
    id: int
    driver_id: int
    text: str
    interval_min: int
    folder_title: str
    is_running: bool = False
    created_at: str = None
    task_packs: Optional[List[Dict[str, Any]]] = None
    current_cycle: int = 0
    cycle_totals: Optional[Dict[str, int]] = None
    failed_peers: Optional[List[Dict[str, Any]]] = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AnnouncementData':
        return cls(**data)


class AnnouncementStore:
    """JSON-based storage for announcement data with cross-process safety."""
    def __init__(self, file_path: str = "announcements.json"):
        self.file_path = file_path
        self._lock = asyncio.Lock()
        self._cache: Optional[List[Dict[str, Any]]] = None
        self._debounce_task: Optional[asyncio.Task] = None
        self._debounce_delay = 0.3  # not relied on anymore; we flush immediately
        self._last_mtime: float = 0.0
        self._ensure_file_exists()
        self._refresh_mtime()

    def _ensure_file_exists(self):
        if not os.path.exists(self.file_path):
            with open(self.file_path, 'w', encoding=ENCODING_SETTINGS['DEFAULT_ENCODING']) as f:
                json.dump([], f, ensure_ascii=ENCODING_SETTINGS['ENSURE_ASCII'])

    def _refresh_mtime(self):
        try:
            self._last_mtime = os.path.getmtime(self.file_path)
        except FileNotFoundError:
            self._last_mtime = 0.0

    async def _acquire_file_lock(self, fh, timeout: float = 5.0) -> bool:
        if fcntl is None:
            return True
        import time as _t
        start = _t.time()
        while _t.time() - start < timeout:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except OSError:
                await asyncio.sleep(0.05)
        return False

    async def _release_file_lock(self, fh):
        if fcntl is None:
            return
        try:
            fcntl.flock(fh, fcntl.LOCK_UN)
        except OSError:
            pass

    async def _maybe_reload_from_disk(self) -> None:
        """Reload if file has changed (supports multiple processes)."""
        try:
            m = os.path.getmtime(self.file_path)
        except FileNotFoundError:
            m = 0.0
        if self._cache is None or m > self._last_mtime:
            async with self._lock:
                try:
                    with open(self.file_path, 'r', encoding=ENCODING_SETTINGS['DEFAULT_ENCODING']) as f:
                        if not await self._acquire_file_lock(f):
                            raise IOError("Could not acquire file lock for read")
                        try:
                            data = json.load(f)
                        finally:
                            await self._release_file_lock(f)
                    if not isinstance(data, list):
                        data = []
                    self._cache = data
                    self._last_mtime = m
                    log.info("announcement_store: reloaded from disk (mtime changed)")
                except json.JSONDecodeError:
                    # file corrupted? keep empty to avoid crashing the worker
                    self._cache = []
                    self._last_mtime = m
                    log.warning("announcement_store: JSON decode error, treating as empty list")

    async def _read_data(self) -> List[Dict[str, Any]]:
        await self._maybe_reload_from_disk()
        return self._cache or []

    async def _flush_to_disk(self) -> None:
        async with self._lock:
            data = self._cache if self._cache is not None else []
            with open(self.file_path, 'w', encoding=ENCODING_SETTINGS['DEFAULT_ENCODING']) as f:
                if not await self._acquire_file_lock(f):
                    raise IOError("Could not acquire file lock for write")
                try:
                    json.dump(data, f, indent=2, ensure_ascii=ENCODING_SETTINGS['ENSURE_ASCII'])
                finally:
                    await self._release_file_lock(f)
            self._refresh_mtime()

    async def _schedule_flush(self) -> None:
        # kept for API compatibility, but we also flush immediately in CRUD ops
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()
            try:
                await asyncio.sleep(0)
            except Exception:
                pass

        async def _delayed():
            try:
                await asyncio.sleep(self._debounce_delay)
                await self._flush_to_disk()
            except asyncio.CancelledError:
                pass

        self._debounce_task = asyncio.create_task(_delayed())

    async def get_announcement(self, driver_id: int) -> Optional[AnnouncementData]:
        data = await self._read_data()
        for d in data:
            if d.get('driver_id') == driver_id:
                ann = AnnouncementData.from_dict(d)
                log.info(f"Found announcement for driver {driver_id}: is_running={ann.is_running}")
                return ann
        return None

    async def get_active_announcements(self) -> List[AnnouncementData]:
        data = await self._read_data()
        active = [AnnouncementData.from_dict(d) for d in data if d.get('is_running')]
        log.info(f"Found {len(active)} active announcements")
        return active

    async def create_announcement(self, announcement: AnnouncementData) -> AnnouncementData:
        # NFC normalize text to keep Cyrillic sane
        import unicodedata
        if announcement.text:
            announcement.text = unicodedata.normalize('NFC', announcement.text)

        data = await self._read_data()
        data = [d for d in data if d.get('driver_id') != announcement.driver_id]
        data.append(announcement.to_dict())
        self._cache = data
        # Flush NOW so UI messages are durable immediately
        await self._flush_to_disk()
        log.info(f"Created announcement for driver {announcement.driver_id} (interval: {announcement.interval_min} min)")
        return announcement

    async def update_announcement(self, announcement: AnnouncementData) -> AnnouncementData:
        import unicodedata
        if announcement.text:
            announcement.text = unicodedata.normalize('NFC', announcement.text)

        data = await self._read_data()
        updated = False
        for i, d in enumerate(data):
            if d.get('driver_id') == announcement.driver_id:
                data[i] = announcement.to_dict()
                updated = True
                break
        if not updated:
            data.append(announcement.to_dict())

        self._cache = data
        await self._flush_to_disk()
        log.info(f"Updated announcement for driver {announcement.driver_id} (is_running: {announcement.is_running})")
        return announcement

    async def delete_announcement(self, driver_id: int) -> bool:
        data = await self._read_data()
        initial = len(data)
        data = [d for d in data if d.get('driver_id') != driver_id]
        if len(data) < initial:
            self._cache = data
            await self._flush_to_disk()
            log.info(f"Deleted announcement for driver {driver_id}")
            return True
        log.warning(f"No announcement found for driver {driver_id} to delete")
        return False


# Global instance
announcement_store = AnnouncementStore()
