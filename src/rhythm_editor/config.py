"""全局常量、默认值与枚举字符串。"""

from __future__ import annotations

APP_TITLE = "In Verus 谱面编辑器"
APP_SUBTITLE = "6K 下落式 + 鼠标平面 双平面音游编辑器"
VERSION = "0.1.0"

# 谱面文件格式
CHART_FORMAT = "inverus.chart"
CHART_FORMAT_VERSION = 1
CHART_EXTENSION = ".json"          # 默认保存为普通 JSON 文件
CHART_EXTENSIONS = (".json",)      # 保存时使用
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
SPLIT_MIN_PANE_PX = 150.0     # 拖动分界线时两边各自的最小宽度
DIVIDER_GRAB_PX = 7.0         # 分界线的“可抓取”半径（像素）
OVERLAY_WIDTH_RATIO = 1 / 3   # 演示模式只占窗口宽度的三分之一（居中）
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

# 允许放大到只看 0.2 秒（刻度更细，方便对微小的偏移）
ZOOM_MIN_SECONDS = 0.2
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

# 播放倍速：0.1 ~ 1.0，每 0.1 一档（慢放用重采样，音调随之变低）；
# 音频引擎本身允许到 RATE_LIMIT_MAX（游玩器可以把倍速开到 3x = 加速）
RATE_MIN = 0.1
RATE_MAX = 1.0
RATE_LIMIT_MAX = 3.0
RATE_STEP = 0.1
DEFAULT_RATE = 1.0

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
TOOL_MOUSE_SLIDE = "mouse_slide"
TOOL_MOUSE_CLICK = "mouse_click"

TOOL_LABELS = {
    TOOL_SELECT: "选择/区域选择",
    TOOL_LANE_TAP: "单轨按键",
    TOOL_LANE_MULTI: "多轨按键",
    # 长按工具同时支持单轨与多轨
    TOOL_LANE_HOLD: "长按（含多轨）",
    TOOL_MOUSE_REGION: "空域",
    TOOL_MOUSE_SLIDE: "左右滑键",
    TOOL_MOUSE_CLICK: "鼠标点击按键",
}

TOOL_ORDER = (
    TOOL_SELECT,
    TOOL_LANE_TAP,
    TOOL_LANE_MULTI,
    TOOL_LANE_HOLD,
    TOOL_MOUSE_REGION,
    TOOL_MOUSE_SLIDE,
    TOOL_MOUSE_CLICK,
)

# 空域放置过程中：尚未点击的边界点按默认值渲染（左边界 0、右边界 1）
REGION_PENDING_LEFT_DEFAULT = 0.0
REGION_PENDING_RIGHT_DEFAULT = 1.0

# 鼠标点击按键：半宽（归一化）最小值与中心菱形的固定半径（像素）
MIN_CLICK_RADIUS = 0.04
CLICK_DIAMOND_RADIUS_PX = 9.0

# 数值容差
EPS = 1e-7

# 播放自动停止的余量（秒）：音乐与谱面都播完这么久之后才停
PLAY_END_MARGIN_SECONDS = 3.0

# 节拍音：播放中一次 tick 最多补响一声，跳过这么多拍以上的跳转不补响
METRONOME_MAX_JUMP_BEATS = 2.0
