from typing import Optional, TypedDict, List

from basic.i18_utils import gt
from sr.app import Application, AppRunRecord, app_const
from sr.config import ConfigHolder
from sr.context import Context
from sr.operation import Operation
from sr.operation.unit.open_map import OpenMap


class TrailblazePowerRecord(AppRunRecord):

    def __init__(self):
        super().__init__(app_const.TRAILBLAZE_POWER.id)


trailblaze_power_record: Optional[TrailblazePowerRecord] = None


def get_record() -> TrailblazePowerRecord:
    global trailblaze_power_record
    if trailblaze_power_record is None:
        trailblaze_power_record = TrailblazePowerRecord()
    return trailblaze_power_record


class TrailblazePowerPlanItem(TypedDict):
    point_id: str  # 关卡id
    team_num: int  # 使用配队
    plan_times: int  # 计划通关次数
    run_times: int  # 已经通关次数


class TrailblazePowerConfig(ConfigHolder):

    def __init__(self):
        super().__init__(app_const.TRAILBLAZE_POWER.id)

    def _init_after_read_file(self):
        pass

    def _check_plan_finished(self):
        """
        检测计划是否都执行完了
        执行完的话 所有执行次数置为0 重新开始下一轮
        :return:
        """
        plan_list: List[TrailblazePowerPlanItem] = self.plan_list
        for item in plan_list:
            if item['run_times'] < item['plan_times']:
                return

        # 全部都执行完了
        for item in plan_list:
            item['run_times'] = 0

        self.plan_list = plan_list

    @property
    def plan_list(self) -> List[TrailblazePowerPlanItem]:
        """
        体力规划配置
        :return:
        """
        return self.get('plan_list', [])

    @plan_list.setter
    def plan_list(self, new_list: List[TrailblazePowerPlanItem]):
        self.update('plan_list', new_list)

    @property
    def next_plan_item(self) -> Optional[TrailblazePowerPlanItem]:
        """
        按规划配置列表，找到第一个还没有完成的去执行
        如果都完成了 选择第一个
        :return: 下一个需要执行的计划
        """
        plan_list: List[TrailblazePowerPlanItem] = self.plan_list
        for item in plan_list:
            if item['run_times'] < item['plan_times']:
                return item

        if len(plan_list) > 0:
            return plan_list[0]

        return None


trailblaze_power_config: Optional[TrailblazePowerConfig] = None


def get_config() -> TrailblazePowerConfig:
    global trailblaze_power_config
    if trailblaze_power_config is None:
        trailblaze_power_config = TrailblazePowerConfig()
    return trailblaze_power_config


class TrailblazePower(Application):

    def __init__(self, ctx: Context):
        super().__init__(ctx, op_name=gt('开拓力', 'ui'))
        self.phase: int = 0
        self.power: int = 160

    def _execute_one_round(self) -> int:
        if self.phase == 0:  # 打开大地图
            op = OpenMap(self.ctx)
            if not op.execute():
                return Operation.FAIL
            else:
                self.phase += 1
                return Operation.WAIT
        elif self.phase == 1:  # 查看剩余体力
            # TODO
            self.phase += 1
            return Operation.WAIT
        elif self.phase == 2:  # 使用体力
            pass  # TODO