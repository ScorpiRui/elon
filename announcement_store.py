import json
import os
from datetime import datetime
from typing import Dict, List, Optional, Any
import asyncio
from dataclasses import dataclass, asdict
import logging

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

# Load settings from config file
with open('config.json', 'r', encoding='utf-8') as f:
    settings = json.load(f)

# Get encoding settings
ENCODING_SETTINGS = settings.get('ENCODING_SETTINGS', {
    'DEFAULT_ENCODING': 'utf-8',
    'UNICODE_NORMALIZATION': 'NFC',
    'ENSURE_ASCII': False
})

@dataclass
class AnnouncementData:
    """Announcement data class for storing message broadcast information."""
    id: int
    driver_id: int
    text: str
    interval_min: int
    folder_title: str
    is_running: bool = False
    created_at: str = None  # ISO format datetime string

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        """Convert announcement object to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AnnouncementData':
        """Create announcement object from dictionary."""
        return cls(**data)

class AnnouncementStore:
    """JSON-based storage for announcement data."""
    def __init__(self, file_path: str = "announcements.json"):
        self.file_path = file_path
        self._lock = asyncio.Lock()
        self._cache: Optional[List[Dict[str, Any]]] = None
        self._debounce_task: Optional[asyncio.Task] = None
        self._debounce_delay = 0.5  # seconds
        self._ensure_file_exists()

    def _ensure_file_exists(self):
        """Ensure the JSON file exists."""
        if not os.path.exists(self.file_path):
            with open(self.file_path, 'w', encoding=ENCODING_SETTINGS['DEFAULT_ENCODING']) as f:
                json.dump([], f, ensure_ascii=ENCODING_SETTINGS['ENSURE_ASCII'])

    async def _read_data(self) -> List[Dict[str, Any]]:
        """Read data with in-memory cache; fall back to disk."""
        async with self._lock:
            if self._cache is not None:
                return self._cache
            try:
                with open(self.file_path, 'r', encoding=ENCODING_SETTINGS['DEFAULT_ENCODING']) as f:
                    data = json.load(f)
                    self._cache = data
                    return data
            except json.JSONDecodeError:
                self._cache = []
                return []

    async def _flush_to_disk(self) -> None:
        async with self._lock:
            data = self._cache if self._cache is not None else []
            with open(self.file_path, 'w', encoding=ENCODING_SETTINGS['DEFAULT_ENCODING']) as f:
                json.dump(data, f, indent=2, ensure_ascii=ENCODING_SETTINGS['ENSURE_ASCII'])

    async def _schedule_flush(self) -> None:
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
        """Get announcement by driver ID."""
        data = await self._read_data()
        for announcement_data in data:
            if announcement_data['driver_id'] == driver_id:
                announcement = AnnouncementData.from_dict(announcement_data)
                log.info(f"Found announcement for driver {driver_id}: is_running={announcement.is_running}")
                return announcement
        log.debug(f"No announcement found for driver {driver_id}")
        return None

    async def get_active_announcements(self) -> List[AnnouncementData]:
        """Get all active announcements."""
        data = await self._read_data()
        active_announcements = [AnnouncementData.from_dict(d) for d in data if d['is_running']]
        log.info(f"Found {len(active_announcements)} active announcements")
        return active_announcements

    async def create_announcement(self, announcement: AnnouncementData) -> AnnouncementData:
        """Create a new announcement."""
        data = await self._read_data()
        
        # Ensure proper text encoding for Unicode characters (including Cyrillic)
        import unicodedata
        if announcement.text:
            announcement.text = unicodedata.normalize('NFC', announcement.text)
            log.info(f"Creating announcement with text: {announcement.text[:100]}...")
        
        announcement_dict = announcement.to_dict()
        # Replace by driver_id if exists
        data = [d for d in data if d['driver_id'] != announcement.driver_id]
        data.append(announcement_dict)
        self._cache = data
        await self._schedule_flush()
        log.info(f"Created announcement for driver {announcement.driver_id} with interval {announcement.interval_min} min")
        return announcement

    async def update_announcement(self, announcement: AnnouncementData) -> AnnouncementData:
        """Update an existing announcement."""
        data = await self._read_data()
        
        # Ensure proper text encoding for Unicode characters (including Cyrillic)
        import unicodedata
        if announcement.text:
            announcement.text = unicodedata.normalize('NFC', announcement.text)
            log.info(f"Updating announcement text: {announcement.text[:100]}...")
        
        updated = False
        for i, d in enumerate(data):
            if d['driver_id'] == announcement.driver_id:
                data[i] = announcement.to_dict()
                updated = True
                break
        if not updated:
            data.append(announcement.to_dict())
        self._cache = data
        await self._schedule_flush()
        log.info(f"Updated announcement for driver {announcement.driver_id} (is_running: {announcement.is_running})")
        return announcement

    async def delete_announcement(self, driver_id: int) -> bool:
        """Delete an announcement by driver ID."""
        data = await self._read_data()
        initial_length = len(data)
        data = [d for d in data if d['driver_id'] != driver_id]
        if len(data) < initial_length:
            self._cache = data
            await self._schedule_flush()
            log.info(f"Deleted announcement for driver {driver_id}")
            return True
        log.warning(f"No announcement found for driver {driver_id} to delete")
        return False

# Create a global instance
announcement_store = AnnouncementStore() 