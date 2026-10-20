"""
数据库表结构 DDL 与领域常量定义。

约定：
- 所有 DDL 使用 IF NOT EXISTS，便于幂等初始化。
- 领域枚举（媒体类型、音效分类）在代码中硬编码，作为唯一权威来源。
- features_json 存储扩展特征的 JSON 串（brightness_hz/onset_rate/zcr 等），
  结构自描述，后续新增特征不需要改表。
"""

# ---------------------------------------------------------------------------
# 领域常量
# ---------------------------------------------------------------------------

# 媒体类型：四种枚举，与其他模块共享
MEDIA_TYPES = ("bgm", "sfx", "image", "video")

# 音效 14 分类固定枚举（决策2），扫描时子目录名命中即用，否则归入 other
SFX_CATEGORIES = (
    "cartoon",
    "city",
    "doors",
    "footsteps",
    "human",
    "impact",
    "kitchen",
    "nature",
    "rain",
    "storm",
    "technology",
    "transition",
    "water",
    "whoosh",
)

# 扫描到音效但无法归类时的兜底分类
SFX_FALLBACK_CATEGORY = "other"

# 允许入库的文件扩展名（小写，不含点）
AUDIO_EXT_BGM = ("mp3",)
AUDIO_EXT_SFX = ("wav",)
IMAGE_EXT = ("jpg", "jpeg", "png", "webp", "gif", "bmp")
VIDEO_EXT = ("mp4", "mov", "mkv", "webm", "avi")

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

# 素材主表
#
# 关键字段说明：
#   file_mtime     增量扫描判断依据：路径存在但 size 或 mtime 变化时需要重新提取元数据
#   title          来自 BGM JSON 的 title 字段（决策1），其他类型为 NULL
#   features_json  扩展特征 JSON 串（brightness_hz/onset_rate/zcr 等），
#                  其他类型可为 NULL；结构自描述，可扩展
#   bpm/energy     仅 bgm 类型有效（来自 JSON 或 librosa 分析），其余为 NULL
#   musical_key    仅 bgm 类型有效
#   sfx_category   仅 sfx 类型有效，取值见 SFX_CATEGORIES 或 other
#   thumbnail_path 相对项目根目录的缩略图路径（WebP），NULL 表示暂无缩略图
DDL_MEDIA = """
CREATE TABLE IF NOT EXISTS media (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path       TEXT    NOT NULL UNIQUE,
    file_name       TEXT    NOT NULL,
    file_ext        TEXT    NOT NULL,
    file_size       INTEGER,
    file_mtime      REAL,
    media_type      TEXT    NOT NULL
                            CHECK(media_type IN ('bgm','sfx','image','video')),
    duration        REAL,
    bitrate         INTEGER,
    sample_rate     INTEGER,
    width           INTEGER,
    height          INTEGER,
    bpm             REAL,
    energy          REAL,
    musical_key     TEXT,
    sfx_category    TEXT,
    title           TEXT,
    features_json   TEXT,
    thumbnail_path  TEXT,
    rating          INTEGER NOT NULL DEFAULT 0
                            CHECK(rating BETWEEN 0 AND 5),
    is_favorite     INTEGER NOT NULL DEFAULT 0
                            CHECK(is_favorite IN (0,1)),
    indexed_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

# 标签
DDL_TAGS = """
CREATE TABLE IF NOT EXISTS tags (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT    NOT NULL UNIQUE COLLATE NOCASE
);
"""

# 素材-标签关联
DDL_MEDIA_TAGS = """
CREATE TABLE IF NOT EXISTS media_tags (
    media_id    INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    tag_id      INTEGER NOT NULL REFERENCES tags(id)  ON DELETE CASCADE,
    PRIMARY KEY (media_id, tag_id)
);
"""

# 歌单/项目篮
DDL_PLAYLISTS = """
CREATE TABLE IF NOT EXISTS playlists (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    description TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

# 歌单条目
DDL_PLAYLIST_ITEMS = """
CREATE TABLE IF NOT EXISTS playlist_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    media_id    INTEGER NOT NULL REFERENCES media(id)     ON DELETE CASCADE,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    note        TEXT,
    UNIQUE (playlist_id, media_id)
);
"""

# 索引：覆盖筛选面板常用维度
DDL_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_media_type        ON media(media_type);",
    "CREATE INDEX IF NOT EXISTS idx_media_bpm         ON media(bpm)         WHERE bpm IS NOT NULL;",
    "CREATE INDEX IF NOT EXISTS idx_media_energy      ON media(energy)      WHERE energy IS NOT NULL;",
    "CREATE INDEX IF NOT EXISTS idx_media_favorite    ON media(is_favorite);",
    "CREATE INDEX IF NOT EXISTS idx_media_rating      ON media(rating);",
    "CREATE INDEX IF NOT EXISTS idx_media_sfx_cat     ON media(sfx_category);",
    "CREATE INDEX IF NOT EXISTS idx_mt_tag            ON media_tags(tag_id);",
    "CREATE INDEX IF NOT EXISTS idx_plitem_playlist   ON playlist_items(playlist_id, sort_order);",
)

# 建表顺序（依赖关系敏感，media 必须在 media_tags/playlist_items 之前）
ALL_TABLES_DDL = (
    DDL_MEDIA,
    DDL_TAGS,
    DDL_MEDIA_TAGS,
    DDL_PLAYLISTS,
    DDL_PLAYLIST_ITEMS,
)
