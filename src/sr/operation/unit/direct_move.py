from typing import Callable, Optional, Union

from basic.i18_utils import gt
from sr.context.context import Context
from sr.operation import Operation, OperationOneRoundResult, OperationResult


class DirectMove(Operation):
    """
    等待加载 直到进入游戏主界面 右上角有角色图标
    """

    def __init__(self, ctx: Context, direct: str = '', wait: float = 0,
                 op_callback: Optional[Callable[[OperationResult], None]] = None):
        """
        :param ctx:
        :param wait: 最多等待多少秒
        :param op_callback: 回调
        """
        super().__init__(ctx, op_name=gt('up'), timeout_seconds=wait,
                         op_callback=op_callback)
        self.direct: str = self.get_direction(direct)
        self.press_time = wait

    def get_direction(self, direct: str):
        if direct == 'left':
            return 'a'
        elif direct == 'right':
            return 'd'
        elif direct == 'up':
            return 'w'
        elif direct == 'down':
            return 's'
        elif direct == 'left_up':
            return 'wa'
        elif direct == 'left_down':
            return 'sa'
        elif direct == 'right_up':
            return 'wd'
        elif direct == 'right_down':
            return 'sd'

    def _execute_one_round(self) -> OperationOneRoundResult:
        print("start move")
        if self.ctx.controller.move(self.direct, self.press_time):
            return self.round_success()
        else:
            return self.round_fail()
