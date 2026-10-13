"""全局常量、默认值与枚举字符串。"""

from __future__ import annotations

APP_TITLE = "In Falsus 社区谱面编辑器"
APP_SUBTITLE = "《In Falsus》的社区谱面编辑器"
VERSION = "0.1.0"

# 谱面文件格式
CHART_FORMAT = "infalsus.chart"
CHART_FORMAT_VERSION = 1
CHART_EXTENSION = ".json"          # 默认保存为普通 JSON 文件

# 玩法布局
LANE_COUNT = 6
MOUSE_LANE_FIRST = 1          # 鼠标平面覆盖 6 轨中的中间四条（索引 1..4）
MOUSE_LANE_LAST = 4

# 布局模式：编辑器里两个区域左右分开；演示模式下鼠标平面叠加到 6K 上
LAYOUT_SPLIT = "split"
LAYOUT_OVERLAY = "overlay"
SPLIT_6K_RATIO = 0.25         # 6K 区域只占约四分之一宽度，其余给鼠标平面
PANE_GAP = 16                 # 两个区域之间的间隔（像素）
SPLIT_MIN_PANE_PX = 150.0     # 拖动分界线时两边各自的最小宽度
DIVIDER_GRAB_PX = 7.0         # 分界线的“可抓取”半径（像素）
OVERLAY_WIDTH_RATIO = 1 / 3   # 演示模式只占窗口宽度的三分之一（居中）
# 鼠标平面在合法区域之外额外显示的范围（归一化坐标）：
# 合法区域是 0..1，这里两侧各多显示 0.45，方便看清超出 0..1 的区域边界
MOUSE_OVERFLOW_NORM = 0.45

# 默认值：BPM 列表 / 偏移 / 网格 / 缩放 / 延迟
DEFAULT_BPM = 120.0
DEFAULT_BEATS_PER_BAR = 4
DEFAULT_GRID_DIVISION = 4      # 一拍细分 4 份
DEFAULT_HORIZONTAL_DIVISIONS = 0   # 0 = 不启用横向辅助网格
DEFAULT_AUDIO_LATENCY_MS = 0.0
DEFAULT_ZOOM_SECONDS = 4.0

# 缩放范围：最大放大到 0.02 秒（当前最大放大程度的 10 倍），
# 最缩小到 4 秒（= 默认缩放，时间网格最稀疏）
ZOOM_MIN_SECONDS = 0.02
ZOOM_MAX_SECONDS = 4.0
ZOOM_STEP = 1.25
GRID_DIVISION_MIN = 1

# 判定线位置（占游玩区高度的比例，0 = 顶部，1 = 底部）
JUDGMENT_LINE_RATIO = 0.80

# 交互
MIN_SLIDE_SPAN = 0.06
HIT_PADDING_PX = 3
SCROLL_WHEEL_SNAP = True          # 滚轮滚动后仍然吸附到时间网格（更保险）
SCROLL_WHEEL_FAST_BEATS = 1.0     # Shift + 滚轮：一次滚动 1 拍
PENDING_COLOR = "#ffd166"

# 播放倍速：0.01 ~ 1.0（慢放用重采样，音调随之变低）。
# ≥ RATE_FINE_MAX 时每 0.1 一档；从 0.1x 继续减速进入 0.01 一档的细档（0.09 … 0.01）。
# 音频引擎本身允许到 RATE_LIMIT_MAX（给未来的加速播放留的余量）。
RATE_MIN = 0.01
RATE_MAX = 1.0
RATE_LIMIT_MAX = 3.0
RATE_STEP = 0.1
RATE_FINE_STEP = 0.01
RATE_FINE_MAX = 0.1
DEFAULT_RATE = 1.0


def quantize_rate(rate: float) -> float:
    """把倍速夹进允许范围并按所在档位取整（细档 0.01、常规档 0.1）。"""
    value = max(RATE_MIN, min(RATE_MAX, float(rate)))
    if value < RATE_FINE_MAX - 1e-9:
        return round(round(value / RATE_FINE_STEP) * RATE_FINE_STEP, 2)
    return round(round(value / RATE_STEP) * RATE_STEP, 1)


def step_rate(rate: float, direction: int) -> float:
    """步进一档：0.2x → 0.1x → 0.09x … 0.01x；反向 0.01x → … → 0.1x → 0.2x。"""
    value = quantize_rate(rate)
    if direction < 0:
        if value > RATE_FINE_MAX + 1e-9:
            return round(max(RATE_FINE_MAX, value - RATE_STEP), 2)
        return round(max(RATE_MIN, value - RATE_FINE_STEP), 2)
    if value < RATE_FINE_MAX - 1e-9:
        return round(min(RATE_FINE_MAX, value + RATE_FINE_STEP), 2)
    return round(min(RATE_MAX, value + RATE_STEP), 2)


def format_rate(rate: float) -> str:
    """倍速文本：常规档保留一位小数，细档保留两位。"""
    value = float(rate)
    return f"{value:.2f}x" if value < RATE_FINE_MAX - 1e-9 else f"{value:.1f}x"

# 系统音频延迟允许为负（负值 = 声音比软件位置更早），这里给出合理范围防止误填
AUDIO_LATENCY_MIN_MS = -5000.0
AUDIO_LATENCY_MAX_MS = 5000.0

# 缓动类型
EASE_LINEAR = "linear"
EASE_IN = "ease_in"
EASE_OUT = "ease_out"
EASE_CHOICES = (EASE_LINEAR, EASE_IN, EASE_OUT)
EASE_LABELS = {
    EASE_LINEAR: "线性",
    EASE_IN: "缓入",
    EASE_OUT: "缓出",
}
EASE_LABEL_TO_VALUE = {v: k for k, v in EASE_LABELS.items()}

# 速度组
DEFAULT_GROUP_ID = 0
DEFAULT_GROUP_NAME = "默认组"
DEFAULT_SPEED_MULTIPLIER = 1.0

# 工具栏：放置工具
TOOL_SELECT = "select"
TOOL_LANE_TAP = "lane_tap"
TOOL_LANE_MULTI = "lane_multi"
TOOL_LANE_HOLD = "lane_hold"
TOOL_MOUSE_REGION = "mouse_region"
TOOL_MOUSE_FORBIDDEN = "mouse_forbidden"
TOOL_MOUSE_SLIDE = "mouse_slide"

TOOL_LABELS = {
    TOOL_SELECT: "选择/区域选择",
    TOOL_LANE_TAP: "单轨按键",
    TOOL_LANE_MULTI: "多轨按键",
    # 长按工具同时支持单轨与多轨
    TOOL_LANE_HOLD: "长按",
    TOOL_MOUSE_REGION: "空域",
    TOOL_MOUSE_FORBIDDEN: "禁区",
    TOOL_MOUSE_SLIDE: "左右滑键",
}

TOOL_ORDER = (
    TOOL_SELECT,
    TOOL_LANE_TAP,
    TOOL_LANE_MULTI,
    TOOL_LANE_HOLD,
    TOOL_MOUSE_REGION,
    TOOL_MOUSE_FORBIDDEN,
    TOOL_MOUSE_SLIDE,
)

# 空域 / 禁区放置过程中：尚未点击的边界点按默认值渲染（左边界 0、右边界 1）
REGION_PENDING_LEFT_DEFAULT = 0.0
REGION_PENDING_RIGHT_DEFAULT = 1.0

# 数值容差
EPS = 1e-7

# 播放自动停止的余量（秒）：音乐与谱面都播完这么久之后才停
PLAY_END_MARGIN_SECONDS = 3.0

# ---------------------------------------------------------------- 演示视图
# 编辑器演示模式用软件渲染的游玩视角预览，渲染参数是独立的一份（存在全局设置里）。
# 厚度单位为世界单位：鼠标合法区宽 = 1。
DEMO_NOTE_THICKNESS = 0.25
DEMO_MULTI_THICKNESS = 0.25
DEMO_SLIDE_THICKNESS = 0.03 * 5.0 / 7.0
DEMO_SPEED = 1.0                 # 流速：可视毫秒 = 1500 / 流速
DEMO_NOTE_THICKNESS_RANGE = (0.05, 1.0, 0.05)
DEMO_PATTERN_THICKNESS_RANGE = (0.005, 0.3, 0.005)
DEMO_SPEED_RANGE = (1.0, 10.0, 0.1)
