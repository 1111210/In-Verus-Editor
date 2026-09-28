"""全局常量、默认值与枚举字符串。"""

from __future__ import annotations

APP_TITLE = "In Verus 谱面编辑器"
APP_SUBTITLE = "6K 下落式 + 鼠标平面 双平面音游编辑器"
VERSION = "0.1.0"

# 谱面文件格式
CHART_FORMAT = "inverus.chart"
CHART_FORMAT_VERSION = 1
CHART_EXTENSIONS = (".ivchart", ".json")
AUDIO_EXTENSIONS = (".mp3", ".ogg", ".wav", ".flac", ".m4a", ".aac", ".opus")

# 玩法布局
LANE_COUNT = 6
MOUSE_LANE_FIRST = 1          # 鼠标平面覆盖 6 轨中的中间四条（索引 1..4）
MOUSE_LANE_LAST = 4
MOUSE_LANE_COUNT = MOUSE_LANE_LAST - MOUSE_LANE_FIRST + 1

# 布局模式：编辑器里两个区域左右分开；演示模式下鼠标平面叠加到 6K 上
LAYOUT_SPLIT = "split"
LAYOUT_OVERLAY = "overlay"
SPLIT_6K_RATIO = 0.25         # 6K 区域只占约四分之一宽度，其余给鼠标平面
PANE_GAP = 16                 # 两个区域之间的间隔（像素）
# 鼠标平面在合法区域之外额外显示的范围（归一化坐标）：
# 合法区域是 0..1，这里两侧各多显示 0.45，方便看清超出 0..1 的区域边界
MOUSE_OVERFLOW_NORM = 0.45

# 默认值：BPM 列表 / 偏移 / 网格 / 缩放 / 延迟
DEFAULT_BPM = 120.0
DEFAULT_BEATS_PER_BAR = 4
DEFAULT_OFFSET_MS = 0.0
DEFAULT_GRID_DIVISION = 4      # 一拍细分 4 份
DEFAULT_HORIZONTAL_DIVISIONS = 0   # 0 = 不启用横向辅助网格
DEFAULT_AUDIO_LATENCY_MS = 0.0
DEFAULT_ZOOM_SECONDS = 4.0

ZOOM_MIN_SECONDS = 0.5
ZOOM_MAX_SECONDS = 40.0
ZOOM_STEP = 1.25
GRID_DIVISION_MIN = 1
GRID_DIVISION_MAX = 64
HORIZONTAL_DIVISION_MAX = 32

# 判定线位置（占游玩区高度的比例，0 = 顶部，1 = 底部）
JUDGMENT_LINE_RATIO = 0.80

# 交互
MIN_SLIDE_SPAN = 0.06
HIT_PADDING_PX = 3
SCROLL_WHEEL_SNAP = True          # 滚轮滚动后仍然吸附到时间网格（更保险）
SCROLL_WHEEL_FAST_BEATS = 1.0     # Shift + 滚轮：一次滚动 1 拍
PENDING_COLOR = "#ffd166"

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
TOOL_LANE_HOLD_MULTI = "lane_hold_multi"
TOOL_MOUSE_REGION = "mouse_region"
TOOL_MOUSE_SLIDE = "mouse_slide"

TOOL_LABELS = {
    TOOL_SELECT: "选择/区域选择",
    TOOL_LANE_TAP: "单轨按键",
    TOOL_LANE_MULTI: "多轨按键",
    TOOL_LANE_HOLD: "长按",
    TOOL_LANE_HOLD_MULTI: "多轨长按",
    TOOL_MOUSE_REGION: "空域",
    TOOL_MOUSE_SLIDE: "左右滑键",
}

TOOL_ORDER = (
    TOOL_SELECT,
    TOOL_LANE_TAP,
    TOOL_LANE_MULTI,
    TOOL_LANE_HOLD,
    TOOL_LANE_HOLD_MULTI,
    TOOL_MOUSE_REGION,
    TOOL_MOUSE_SLIDE,
)

# 空域放置过程中：尚未点击的边界点按默认值渲染（左边界 0、右边界 1）
REGION_PENDING_LEFT_DEFAULT = 0.0
REGION_PENDING_RIGHT_DEFAULT = 1.0

# 数值容差
EPS = 1e-7
