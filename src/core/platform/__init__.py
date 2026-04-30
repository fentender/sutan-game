"""平台集成层 — Steam 时间戳、历史版本、更新检查"""

from .steam import (
    MAJOR_UPDATE_TS as MAJOR_UPDATE_TS,
    get_game_update_time as get_game_update_time,
    get_mod_update_times as get_mod_update_times,
)
from .updater import check_for_update as check_for_update
