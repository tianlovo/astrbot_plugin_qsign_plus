"""
图片缓存服务模块

提供图片下载、缓存管理等功能，遵守 AstrBot 大文件存储规范。
缓存目录: data/plugin_data/astrbot_plugin_Qsign/image_cache/
"""

import asyncio
import base64
import hashlib
import json
import shutil
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import aiohttp
from astrbot.api import logger

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
except ImportError:
    get_astrbot_data_path = None


@dataclass
class CacheEntry:
    """缓存条目元数据"""
    key: str
    file_path: str
    created_at: float
    accessed_at: float
    size: int
    cache_type: str  # 'avatar' 或 'background'


class ImageCacheService:
    """图片缓存服务

    提供图片资源的缓存管理功能，包括：
    - 存储：将下载的图片存储到缓存目录
    - 读取：优先返回缓存的图片
    - 清理：支持过期清理、LRU清理

    存储路径遵守 AstrBot 规范：
    data/plugin_data/astrbot_plugin_Qsign/image_cache/
    """

    def __init__(
        self,
        plugin_name: str = "astrbot_plugin_Qsign",
        ttl: int = 86400,  # 默认1天
        max_size: int = 500,  # 默认500个文件
    ):
        """初始化缓存服务

        Args:
            plugin_name: 插件名称
            ttl: 缓存有效期（秒），默认1天
            max_size: 最大缓存文件数
        """
        self.plugin_name = plugin_name
        self.ttl = ttl
        self.max_size = max_size

        # 缓存目录
        self.cache_dir = self._get_cache_dir()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # 元数据文件
        self.metadata_file = self.cache_dir / ".cache_metadata.json"
        self._metadata: dict[str, CacheEntry] = {}
        self._load_metadata()

        # HTTP session
        timeout = aiohttp.ClientTimeout(total=10)
        self.session = aiohttp.ClientSession(timeout=timeout)

        logger.info(f"[{plugin_name}] 图片缓存服务初始化完成，缓存目录: {self.cache_dir}")

    def _get_cache_dir(self) -> Path:
        """获取缓存目录路径，遵守 AstrBot 存储规范

        Returns:
            缓存目录路径: data/plugin_data/{plugin_name}/image_cache/
        """
        if get_astrbot_data_path:
            base_path = Path(get_astrbot_data_path())
        else:
            base_path = Path(__file__).resolve().parent.parent.parent / "data"

        cache_dir = base_path / "plugin_data" / self.plugin_name / "image_cache"
        return cache_dir

    def _load_metadata(self):
        """加载缓存元数据"""
        if self.metadata_file.exists():
            try:
                with open(self.metadata_file, encoding="utf-8") as f:
                    data = json.load(f)
                    for key, value in data.items():
                        self._metadata[key] = CacheEntry(**value)
            except Exception as e:
                logger.warning(f"[{self.plugin_name}] 加载缓存元数据失败: {e}")
                self._metadata = {}

    def _save_metadata(self):
        """保存缓存元数据"""
        try:
            data = {k: asdict(v) for k, v in self._metadata.items()}
            with open(self.metadata_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[{self.plugin_name}] 保存缓存元数据失败: {e}")

    def _get_cache_file_path(self, cache_key: str) -> Path:
        """获取缓存文件路径

        Args:
            cache_key: 缓存键

        Returns:
            缓存文件路径
        """
        key_hash = hashlib.md5(cache_key.encode()).hexdigest()[:8]
        return self.cache_dir / f"img_{cache_key[:30]}_{key_hash}.jpg"

    def _is_expired(self, entry: CacheEntry) -> bool:
        """检查缓存是否过期

        Args:
            entry: 缓存条目

        Returns:
            是否过期
        """
        if self.ttl <= 0:
            return False
        return time.time() - entry.accessed_at > self.ttl

    def _cleanup_lru(self, needed_space: int = 1) -> int:
        """LRU 清理策略

        Args:
            needed_space: 需要的空间

        Returns:
            清理的文件数量
        """
        if len(self._metadata) + needed_space <= self.max_size:
            return 0

        sorted_entries = sorted(self._metadata.items(), key=lambda x: x[1].accessed_at)
        to_remove = len(self._metadata) + needed_space - self.max_size
        removed = 0

        for key, entry in sorted_entries[:to_remove]:
            try:
                file_path = Path(entry.file_path)
                if file_path.exists():
                    file_path.unlink()
                del self._metadata[key]
                removed += 1
            except Exception as e:
                logger.warning(f"[{self.plugin_name}] LRU清理失败: {e}")

        if removed > 0:
            self._save_metadata()
            logger.info(f"[{self.plugin_name}] LRU清理完成，清理了 {removed} 个文件")

        return removed

    async def _download_image(self, url: str, max_retries: int = 3) -> bytes | None:
        """下载图片

        Args:
            url: 图片URL
            max_retries: 最大重试次数

        Returns:
            图片字节数据，失败返回None
        """
        for attempt in range(max_retries):
            try:
                async with self.session.get(url) as response:
                    if response.status == 200:
                        return await response.read()
                    else:
                        logger.warning(
                            f"下载图片失败 ({url})，状态码: {response.status}，"
                            f"尝试 {attempt + 1}/{max_retries}"
                        )
            except Exception as e:
                logger.warning(
                    f"下载图片异常 ({url}): {e}，尝试 {attempt + 1}/{max_retries}"
                )

            if attempt < max_retries - 1:
                await asyncio.sleep(0.5 * (attempt + 1))

        logger.error(f"下载图片最终失败 ({url})")
        return None

    def get(self, cache_key: str) -> Path | None:
        """获取缓存的图片

        Args:
            cache_key: 缓存键

        Returns:
            缓存文件路径，如果不存在或已过期则返回 None
        """
        if cache_key not in self._metadata:
            return None

        entry = self._metadata[cache_key]

        if self._is_expired(entry):
            logger.debug(f"[{self.plugin_name}] 缓存已过期: {cache_key}")
            self.delete(cache_key)
            return None

        file_path = Path(entry.file_path)
        if not file_path.exists():
            logger.warning(f"[{self.plugin_name}] 缓存文件不存在: {file_path}")
            self.delete(cache_key)
            return None

        entry.accessed_at = time.time()
        self._save_metadata()

        logger.debug(f"[{self.plugin_name}] 缓存命中: {cache_key}")
        return file_path

    async def get_or_download(
        self,
        cache_key: str,
        url: str,
        cache_type: str = "avatar",
    ) -> Path | None:
        """获取缓存图片，如果不存在则下载

        Args:
            cache_key: 缓存键
            url: 图片URL
            cache_type: 缓存类型 ('avatar' 或 'background')

        Returns:
            缓存文件路径，失败返回 None
        """
        # 先尝试从缓存获取
        cached = self.get(cache_key)
        if cached:
            return cached

        # 下载图片
        image_bytes = await self._download_image(url)
        if not image_bytes:
            return None

        # 存储到缓存
        return await self.set(cache_key, image_bytes, cache_type)

    async def set(
        self,
        cache_key: str,
        image_bytes: bytes,
        cache_type: str = "avatar",
    ) -> Path | None:
        """存储图片到缓存

        Args:
            cache_key: 缓存键
            image_bytes: 图片字节数据
            cache_type: 缓存类型

        Returns:
            缓存文件路径，失败返回 None
        """
        cache_file = self._get_cache_file_path(cache_key)

        try:
            # LRU 清理
            self._cleanup_lru(needed_space=1)

            # 写入文件
            cache_file.write_bytes(image_bytes)

            # 更新元数据
            now = time.time()
            self._metadata[cache_key] = CacheEntry(
                key=cache_key,
                file_path=str(cache_file),
                created_at=now,
                accessed_at=now,
                size=len(image_bytes),
                cache_type=cache_type,
            )
            self._save_metadata()

            logger.debug(f"[{self.plugin_name}] 缓存已存储: {cache_key}")
            return cache_file

        except Exception as e:
            logger.error(f"[{self.plugin_name}] 缓存存储失败: {e}")
            return None

    def delete(self, cache_key: str) -> bool:
        """删除缓存

        Args:
            cache_key: 缓存键

        Returns:
            是否成功删除
        """
        if cache_key not in self._metadata:
            return False

        entry = self._metadata[cache_key]

        try:
            file_path = Path(entry.file_path)
            if file_path.exists():
                file_path.unlink()

            del self._metadata[cache_key]
            self._save_metadata()

            logger.debug(f"[{self.plugin_name}] 缓存已删除: {cache_key}")
            return True

        except Exception as e:
            logger.warning(f"[{self.plugin_name}] 缓存删除失败: {e}")
            return False

    def clear_expired(self) -> int:
        """清理过期缓存

        Returns:
            清理的文件数量
        """
        expired_keys = [
            key for key, entry in self._metadata.items() if self._is_expired(entry)
        ]

        removed = 0
        for key in expired_keys:
            if self.delete(key):
                removed += 1

        if removed > 0:
            logger.info(f"[{self.plugin_name}] 过期缓存清理完成，清理了 {removed} 个文件")

        return removed

    def clear_all(self) -> int:
        """清理所有缓存

        Returns:
            清理的文件数量
        """
        count = len(self._metadata)

        try:
            for entry in self._metadata.values():
                file_path = Path(entry.file_path)
                if file_path.exists():
                    file_path.unlink()

            self._metadata.clear()
            self._save_metadata()

            logger.info(f"[{self.plugin_name}] 所有缓存已清理，共 {count} 个文件")
            return count

        except Exception as e:
            logger.error(f"[{self.plugin_name}] 清理所有缓存失败: {e}")
            return 0

    def file_to_base64(self, file_path: Path) -> str:
        """将文件转换为base64

        Args:
            file_path: 文件路径

        Returns:
            base64编码的字符串
        """
        try:
            with open(file_path, "rb") as f:
                encoded = base64.b64encode(f.read()).decode("utf-8")
                return f"data:image/jpeg;base64,{encoded}"
        except Exception as e:
            logger.error(f"文件转base64失败: {e}")
            return ""

    async def get_avatar(self, user_id: str) -> str:
        """获取用户头像（带缓存）

        Args:
            user_id: 用户ID

        Returns:
            base64编码的头像数据
        """
        cache_key = f"avatar_{user_id}"
        avatar_url = f"http://q.qlogo.cn/headimg_dl?dst_uin={user_id}&spec=640&img_type=jpg"

        cached_path = await self.get_or_download(cache_key, avatar_url, "avatar")
        if cached_path:
            return self.file_to_base64(cached_path)
        return ""

    async def get_daily_background(self, bg_api_url: str) -> str:
        """获取每日背景图（带缓存，按日期缓存）

        Args:
            bg_api_url: 背景图API地址

        Returns:
            base64编码的背景图数据
        """
        today = datetime.now().strftime("%Y-%m-%d")
        cache_key = f"bg_{today}"

        cached_path = await self.get_or_download(cache_key, bg_api_url, "background")
        if cached_path:
            return self.file_to_base64(cached_path)
        return ""

    async def close(self):
        """关闭服务"""
        await self.session.close()
