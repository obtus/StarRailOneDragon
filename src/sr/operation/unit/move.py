import time
from typing import List, Optional, Tuple, Callable, ClassVar

from cv2.typing import MatLike

from basic import Point, cal_utils, debug_utils, Rect
from basic.i18_utils import gt
from basic.img import MatchResult
from basic.log_utils import log
from sr import cal_pos
from sr.cal_pos import VerifyPosInfo
from sr.const import game_config_const
from sr.const.map_const import Region
from sr.context.context import Context
from sr.control import GameController
from sr.image.sceenshot import mini_map, MiniMapInfo, LargeMapInfo, large_map, screen_state
from sr.image.sceenshot.screen_state_enum import ScreenState
from sr.operation import Operation, OperationOneRoundResult, OperationResult, StateOperation, StateOperationNode
from sr.operation.unit.record_coordinate import RecordCoordinate
from sr.operation.unit.world_patrol_battle import WorldPatrolEnterFight
from sr.screen_area.screen_normal_world import ScreenNormalWorld


class GetRidOfStuck(Operation):

    def __init__(self, ctx: Context, stuck_times: int):
        """
        简单的脱困指令
        返回数据为脱困使用的时间
        以下方式各尝试2遍
        1. 往左 然后往前走
        2. 往右 然后往前走
        3. 往后再往右 然后往前走  # 注意这里左右顺序要跟上面相反 可以防止一左一右还卡在原处
        4. 往后再往左 然后往前走
        5. 往左再往后再往右 然后往前走
        6. 往右再往后再往左 然后往前走
        :param ctx:
        :param stuck_times: 被困次数 1~12
        """
        super().__init__(ctx, op_name='%s %d' % (gt('尝试脱困', 'ui'), stuck_times))
        self.stuck_times: int = stuck_times

    def _execute_one_round(self) -> OperationOneRoundResult:
        ctrl: GameController = self.ctx.controller

        ctrl.stop_moving_forward()

        move_unit_sec = 0.25
        try_move_unit = self.stuck_times % 2 if self.stuck_times % 2 != 0 else 2
        try_method = (self.stuck_times + 1) // 2

        if try_method == 1:  # 左 前
            walk_sec = try_move_unit * move_unit_sec
            ctrl.move('a', walk_sec)
            ctrl.start_moving_forward()  # 多往前走1秒再判断是否被困
            time.sleep(1)
            total_time = walk_sec + 1
        elif try_method == 2:  # 右 前
            walk_sec = try_move_unit * move_unit_sec
            ctrl.move('d', walk_sec)
            ctrl.start_moving_forward()
            time.sleep(1)
            total_time = walk_sec + 1
        elif try_method == 4:  # 后左 前  # 注意这里左右顺序要跟1, 2相反 可以防止一左一右还卡在原处
            walk_sec = try_move_unit * move_unit_sec
            ctrl.move('s', walk_sec)
            ctrl.move('a', walk_sec)
            ctrl.move('w', walk_sec)
            ctrl.start_moving_forward()
            time.sleep(1)
            total_time = walk_sec * 3 + 1
        elif try_method == 3:  # 后右 前
            walk_sec = try_move_unit * move_unit_sec
            ctrl.move('s', walk_sec)
            ctrl.move('d', walk_sec)
            ctrl.move('w', walk_sec)
            ctrl.start_moving_forward()
            time.sleep(1)
            total_time = walk_sec * 3 + 1
        elif try_method == 5:  # 左后右 前
            walk_sec = try_move_unit * move_unit_sec
            ctrl.move('a', walk_sec)
            ctrl.move('s', walk_sec)
            ctrl.move('d', walk_sec + move_unit_sec)
            ctrl.start_moving_forward()
            time.sleep(1)
            total_time = walk_sec * 3 + move_unit_sec + 1
        elif try_method == 6:  # 右后左 前
            walk_sec = try_move_unit * move_unit_sec
            ctrl.move('d', walk_sec)
            ctrl.move('s', walk_sec)
            ctrl.move('a', walk_sec + move_unit_sec)
            ctrl.start_moving_forward()
            time.sleep(1)
            total_time = walk_sec * 3 + move_unit_sec + 1
        else:
            total_time = 0

        return self.round_success(data=total_time)


class TurnToAngle(Operation):

    def __init__(self, ctx: Context, target_angle: float):
        """
        转到特定的朝向
        :param ctx:
        """
        super().__init__(ctx, try_times=5,
                         op_name='%s %.2f' % (gt('转向', 'ui'), target_angle))

        self.target_angle: float = target_angle  # 目标角度

    def _execute_one_round(self) -> OperationOneRoundResult:
        screen = self.screenshot()
        mm = mini_map.cut_mini_map(screen, self.ctx.game_config.mini_map_pos)
        current_angle = mini_map.analyse_angle(mm)

        angle_delta = cal_utils.angle_delta(current_angle, self.target_angle)

        if abs(angle_delta) < 10:
            return self.round_success()

        self.ctx.controller.turn_by_angle(angle_delta)
        return self.round_wait(wait=0.5)


class MoveDirectly(Operation):

    max_len: int = 6  # 最多存储多少个走过的坐标
    rec_pos_interval: float = 0.5  # 间隔多少秒记录一次坐标
    stuck_distance: float = 20  # 移动距离多少以内认为是被困
    arrival_distance: float = 10  # 多少距离内认为是到达目的地
    fail_after_no_battle: float = 120  # 多少秒无战斗后退出 通常不会有路线这么久都遇不到怪 只能是卡死了 然后脱困算法又让角色产生些位移

    STATUS_NO_POS: ClassVar[str] = '无法识别坐标'

    def __init__(self, ctx: Context,
                 lm_info: LargeMapInfo,
                 start: Point,
                 target: Point,
                 next_lm_info: Optional[LargeMapInfo] = None,
                 stop_afterwards: bool = True,
                 no_run: bool = False,
                 no_battle: bool = False,
                 technique_fight: bool = False,
                 technique_only: bool = False,
                 op_callback: Optional[Callable[[OperationResult], None]] = None):
        """
        从当前位置 朝目标点直线前行
        有简单的脱困功能

        1. 最开始使用传入的开始坐标 直接开始移动
        2. 移动途中计算新的坐标，计算坐标会判断在一定距离内，且方向与当前人物朝向基本一致
        3. 由于第2点，移动途中不应该有任何移动外转向，避免计算坐标被舍弃
        4. 移动途中可能被怪物锁定而停下来
        """
        super().__init__(ctx, op_name=gt('移动 %s -> %s') % (start, target), op_callback=op_callback)
        self.lm_info: LargeMapInfo = lm_info
        self.next_lm_info: LargeMapInfo = next_lm_info
        self.region: Region = lm_info.region
        self.target: Point = target
        self.start_pos: Point = start
        self.pos: List[Point] = []
        self.stuck_times = 0  # 被困次数
        self.last_rec_time = 0  # 上一次记录坐标的时间
        self.no_pos_times = 0  # 累计算不到坐标的次数
        self.stop_afterwards = stop_afterwards  # 最后是否停止前进
        self.last_no_pos_time = 0  # 上一次算不到坐标的时间 目前算坐标太快了 可能地图还在缩放中途就已经失败 所以稍微隔点时间再记录算不到坐标
        self.stop_move_time: Optional[float] = None  # 停止移动的时间

        self.run_mode = game_config_const.RUN_MODE_OFF if no_run else self.ctx.game_config.run_mode
        self.no_battle: bool = no_battle  # 本次移动是否保证没有战斗
        self.technique_fight: bool = technique_fight  # 是否使用秘技进入战斗
        self.technique_only: bool = technique_only  # 是否只使用秘技进入战斗

    def handle_init(self) -> Optional[OperationOneRoundResult]:
        """
        执行前的初始化 由子类实现
        注意初始化要全面 方便一个指令重复使用
        可以返回初始化后判断的结果
        - 成功时跳过本指令
        - 失败时立刻返回失败
        - 不返回时正常运行本指令
        """
        now = time.time()
        self.last_rec_time = now - 1
        self.last_battle_time = now  # 上一次在战斗的时间 用于判断是否长时间没有进入战斗 然后退出
        self.last_battle_exit_with_alert: bool = False  # 上一次战斗指令退出时 仍然有告警。说明人物卡住了，后续要先忽略攻击告警进行移动。
        self.pos = []
        if self.ctx.controller.is_moving:  # 连续移动的时候 使用开始点作为一个起始点
            self.pos.append(self.start_pos)
        self.stop_move_time = None

        return None

    def _execute_one_round(self) -> OperationOneRoundResult:
        now_time = time.time()

        if not self.no_battle and now_time - self.last_battle_time > MoveDirectly.fail_after_no_battle:
            return self.round_fail('移动超时')

        stuck = self.move_in_stuck()  # 先尝试脱困 再进行移动
        if stuck is not None:  # 只有脱困失败的情况会返回 round_fail
            return stuck

        screen = self.screenshot()

        if screen_state.is_normal_in_world(screen, self.ctx.im):
            return self.handle_in_world(screen, now_time)
        else:
            return self.handle_not_in_world(screen, now_time)

    def handle_not_in_world(self, screen: MatLike, now_time: float) -> OperationOneRoundResult:
        """
        不在大世界中 进行处理
        目前就只会进入了战斗
        :return:
        """
        self.ctx.controller.stop_moving_forward()
        if self.stop_move_time is None:
            self.stop_move_time = now_time + (1 if self.run_mode != game_config_const.RUN_MODE_OFF else 0)
        log.info('移动中被袭击')
        fight = self.get_fight_op(in_world=False)
        fight_start_time = now_time
        fight_result = fight.execute()
        fight_end_time = time.time()
        if not fight_result.success:
            return self.round_fail(status=fight_result.status, data=fight_result.data)
        else:
            self.last_battle_exit_with_alert = fight_result.status == WorldPatrolEnterFight.STATUS_EXIT_WITH_ALERT

        self.last_battle_time = fight_end_time
        self.last_rec_time += fight_end_time - fight_start_time  # 战斗可能很久 更改记录时间
        self.ctx.pos_first_cal_pos_after_fight = True
        return self.round_wait()

    def get_fight_op(self, in_world: bool = True) -> Operation:
        """
        移动过程中被袭击时候处理的指令
        :return:
        """
        first_state = ScreenNormalWorld.CHARACTER_ICON.value.status if in_world else ScreenState.BATTLE.value
        return WorldPatrolEnterFight(self.ctx,
                                     technique_fight=self.technique_fight,
                                     technique_only=self.technique_only,
                                     first_state=first_state)

    def handle_in_world(self, screen: MatLike, now_time: float) -> OperationOneRoundResult:
        """
        在大世界中 进行处理
        :return:
        """
        # 先异步识别是否需要攻击
        if (not self.no_battle  # 如果外层调用保证没有战斗 跳过识别
            and not self.last_battle_exit_with_alert  # 如果上一次的战斗指令是有告警地退出，说明人物卡住了，先移动，不识别攻击
        ):
            submit, attack_future = self.ctx.yolo_detector.detect_should_attack_in_world_async(screen, now_time)
            log.debug('提交攻击检测 %s', submit)

        mm = mini_map.cut_mini_map(screen, self.ctx.game_config.mini_map_pos)

        next_pos, mm_info = self.cal_pos(mm, now_time)  # 计算当前坐标

        check_no_pos = self.check_no_pos(next_pos, now_time)  # 坐标计算失败处理
        if check_no_pos is None:
            # 能识别到坐标的时候 先判断是否到达 就算被怪锁定 也交给下一个patrol指令攻击
            check_arrive = self.check_arrive(next_pos)
            if check_arrive is not None:
                return check_arrive

        # 被敌人锁定的时候 小地图会被染红 坐标匹配能力大减
        # 因此 就算识别不到坐标 也要判断是否被怪锁定 以免一直识别坐标失败站在原地被袭
        check_enemy = self.check_enemy_and_attack(screen, mm_info.origin_del_radio, now_time)
        if check_enemy is not None:
            return check_enemy

        if check_no_pos is not None:
            return check_no_pos

        self.move(next_pos, now_time, mm_info)

        return self.round_wait()

    def move_in_stuck(self) -> Optional[OperationOneRoundResult]:
        """
        判断是否被困且进行移动
        :return: 如果被困次数过多就返回失败
        """
        if len(self.pos) == 0 or self.no_pos_times > 0:  # 识别不到坐标的时候也不要乱走了
            return None

        first_pos = self.pos[0]
        last_pos = self.pos[len(self.pos) - 1]

        # 通过第一个坐标和最后一个坐标的距离 判断是否困住了
        if len(self.pos) >= MoveDirectly.max_len and \
                cal_utils.distance_between(first_pos, last_pos) < MoveDirectly.stuck_distance:
            self.stuck_times += 1
            if self.stuck_times > 12:
                return self.round_fail('脱困失败')
            get_rid_of_stuck = GetRidOfStuck(self.ctx, self.stuck_times)
            stuck_op_result = get_rid_of_stuck.execute()
            if stuck_op_result.success:
                self.last_rec_time += stuck_op_result.data
        else:
            self.stuck_times = 0

        return None

    def check_enemy_and_attack(self, screen: MatLike, mm: MatLike, now_time: float) -> Optional[OperationOneRoundResult]:
        """
        从小地图检测敌人 如果有的话 进入索敌
        :param screen: 游戏画面
        :param mm: 小地图部分
        :param now_time: 当前时间
        :return: 是否有敌人
        """
        if self.no_battle:  # 外层调用保证没有战斗 跳过后续检测
            return None
        if not self.ctx.yolo_detector.should_attack_in_world_last_result(now_time):
            return None

        # 停止移动的指令交给了 WorldPatrolEnterFight 这样可以通过攻击或者十方秘技来取消停止移动造成的后摇
        if self.stop_move_time is None:
            self.stop_move_time = time.time() + (1 if self.run_mode != game_config_const.RUN_MODE_OFF else 0)

        fight = self.get_fight_op(in_world=True)
        fight_start_time = time.time()
        op_result = fight.execute()
        if not op_result.success:
            return self.round_fail(status=op_result.status, data=op_result.data)
        else:
            self.last_battle_exit_with_alert = op_result.status == WorldPatrolEnterFight.STATUS_EXIT_WITH_ALERT

        fight_end_time = time.time()

        self.last_battle_time = fight_end_time
        self.last_rec_time += fight_end_time - fight_start_time  # 战斗可能很久 更改记录时间
        self.ctx.pos_first_cal_pos_after_fight = True

        return self.round_wait()

    def cal_pos(self, mm: MatLike, now_time: float) -> Tuple[Optional[Point], MiniMapInfo]:
        """
        根据上一次的坐标和行进距离 计算当前位置坐标
        :param mm: 小地图截图
        :param now_time: 当前时间
        :return:
        """
        # 根据上一次的坐标和行进距离 计算当前位置
        if self.last_rec_time > 0:
            if self.stop_move_time is not None:
                move_time = self.stop_move_time - self.last_rec_time  # 停止移动后的时间不应该纳入计算
            else:
                move_time = now_time - self.last_rec_time
            if move_time < 1:
                move_time = 1
        else:
            move_time = 1
        if self.ctx.pos_first_cal_pos_after_fight:
            move_time += 1  # 扩大范围 兼容攻击时产生的位移

        log.debug('上次记录时间 %.2f 停止移动时间 %.2f 当前时间 %.2f',
                  self.last_rec_time,
                  0 if self.stop_move_time is None else self.stop_move_time,
                  now_time)

        move_distance = self.ctx.controller.cal_move_distance_by_time(move_time)
        last_pos = self.pos[len(self.pos) - 1] if len(self.pos) > 0 else self.start_pos
        possible_pos = (last_pos.x, last_pos.y, move_distance)
        log.debug('准备计算人物坐标 使用上一个坐标为 %s 移动时间 %.2f 是否在移动 %s', possible_pos,
                  move_time, self.ctx.controller.is_moving)
        lm_rect = large_map.get_large_map_rect_by_pos(self.lm_info.gray.shape, mm.shape[:2], possible_pos)

        mm_info = mini_map.analyse_mini_map(mm)

        if len(self.pos) == 0:  # 第一个可以直接使用开始点 不进行计算
            return self.start_pos, mm_info

        # 正确移动时 人物不应该偏离直线太远
        # 攻击后 可能因为攻击产生了位移 允许远一点
        # 脱困移动时 会向左右移动 允许远一点
        max_line_distance = 40 if self.ctx.pos_first_cal_pos_after_fight or self.stuck_times > 0 else 20
        verify = VerifyPosInfo(last_pos=last_pos, max_distance=move_distance,
                               line_p1=self.start_pos, line_p2=self.target,
                               max_line_distance=max_line_distance
                               )

        next_pos = self.do_cal_pos(mm_info, lm_rect, verify)

        if next_pos is None:
            log.error('无法判断当前人物坐标')
            if self.ctx.one_dragon_config.is_debug and self.no_pos_times == 0:  # 只记录第一次识别坐标失败的
                debug_utils.get_executor().submit(
                    cal_pos.save_as_test_case,
                    mm, self.region, verify
                )
        else:
            if self.ctx.record_coordinate and now_time - self.last_rec_time > 0.5:
                RecordCoordinate.save(self.region, mm, next_pos)
        return next_pos.center if next_pos is not None else None, mm_info

    def do_cal_pos(self, mm_info: MiniMapInfo,
                   lm_rect: Rect, verify: VerifyPosInfo) -> Optional[MatchResult]:
        """
        真正的计算坐标
        :param mm_info: 当前的小地图信息
        :param lm_rect: 使用的大地图范围
        :param verify: 用于验证坐标的信息
        :return:
        """
        try:
            real_move_time = self.ctx.controller.get_move_time()
            next_pos = cal_pos.cal_character_pos(self.ctx.im, self.lm_info, mm_info,
                                                 lm_rect=lm_rect, retry_without_rect=False,
                                                 running=self.ctx.controller.is_moving,
                                                 real_move_time=real_move_time,
                                                 verify=verify)
            if next_pos is None and self.next_lm_info is not None:
                next_pos = cal_pos.cal_character_pos(self.ctx.im, self.next_lm_info, mm_info,
                                                     lm_rect=lm_rect, retry_without_rect=False,
                                                     running=self.ctx.controller.is_moving,
                                                     real_move_time=real_move_time,
                                                     verify=verify)
        except Exception:
            next_pos = None
            log.error('识别坐标失败', exc_info=True)

        return next_pos

    def check_no_pos(self, next_pos: Point, now_time: float) -> Optional[OperationOneRoundResult]:
        """
        并判断是否识别不到坐标
        过长时间无法识别需要停止角色移动 甚至错误退出
        :param next_pos: 计算得到的坐标
        :param now_time: 当前时间
        :return:
        """
        if next_pos is None:
            if now_time - self.last_no_pos_time > 0.5:
                self.ctx.controller.enter_running(False)  # 不疾跑避免跑远了
                self.no_pos_times += 1
                self.last_no_pos_time = now_time
                if self.no_pos_times >= 3:  # 不要再乱走了
                    self.ctx.controller.stop_moving_forward()
                    if self.stop_move_time is None:
                        self.stop_move_time = now_time + 1  # 加1秒代表惯性
                if self.no_pos_times >= 10:
                    return self.round_fail(MoveDirectly.STATUS_NO_POS)
            return self.round_wait()
        else:
            self.no_pos_times = 0
            self.ctx.pos_first_cal_pos_after_fight = False

    def check_arrive(self, next_pos: Point) -> Optional[OperationOneRoundResult]:
        """
        检查是否已经到达目标点
        :param next_pos:
        :return:
        """
        if cal_utils.distance_between(next_pos, self.target) < MoveDirectly.arrival_distance:
            if self.stop_afterwards:
                self.ctx.controller.stop_moving_forward()
            self.ctx.update_pos_after_move(next_pos, region=None if self.next_lm_info is None else self.next_lm_info.region)
            return self.round_success(data=next_pos)
        return None

    def move(self, next_pos: Point, now_time: float, mm_info: MiniMapInfo):
        """
        移动
        :param next_pos:
        :param now_time:
        :param mm_info:
        :return:
        """
        self.stop_move_time = None
        self.ctx.update_pos_after_move(next_pos)
        if now_time - self.last_rec_time > self.rec_pos_interval:  # 隔一段时间才记录一个点
            self.ctx.controller.move_towards(next_pos, self.target, mm_info.angle,
                                             run=self.run_mode == game_config_const.RUN_MODE_BTN)
            # time.sleep(0.5)  # 如果使用小箭头计算方向 则需要等待人物转过来再进行下一轮
            self.pos.append(next_pos)
            log.debug('记录坐标 %s', next_pos)
            if len(self.pos) > MoveDirectly.max_len:
                del self.pos[0]
            self.last_rec_time = now_time

    def handle_pause(self, e=None):
        """
        暂停后的处理 由子类实现
        :return:
        """
        self.ctx.controller.stop_moving_forward()

    def handle_resume(self) -> None:
        """
        恢复运行后的处理 由子类实现
        :return:
        """
        self.last_rec_time += self.current_pause_time
        self.last_battle_time += self.current_pause_time

    def _after_operation_done(self, result: OperationResult):
        super()._after_operation_done(result)
        if not result.success:
            self.ctx.controller.stop_moving_forward()


class MoveToEnemy(Operation):

    STATUS_ENEMY_NOT_FOUND: ClassVar[str] = '未找到敌人'

    def __init__(self, ctx: Context, no_run: bool = False, timeout_seconds: float = 600):
        """
        按小地图上的红色点 向敌人移动
        适合只有一个怪的场景
        - 逐光捡金
        - 模拟宇宙精英怪
        :param ctx: 上下文
        :param no_run: 不能疾跑
        :param timeout_seconds: 超时时间
        """
        super().__init__(ctx, op_name=gt('向敌人移动', 'ui'), timeout_seconds=timeout_seconds)
        self.run_mode = game_config_const.RUN_MODE_OFF if no_run else self.ctx.game_config.run_mode
        self.last_move_time: float = 0  # 上一次移动的时间

    def handle_init(self) -> Optional[OperationOneRoundResult]:
        """
        执行前的初始化 由子类实现
        注意初始化要全面 方便一个指令重复使用
        可以返回初始化后判断的结果
        - 成功时跳过本指令
        - 失败时立刻返回失败
        - 不返回时正常运行本指令
        """
        self.last_move_time = 0

        return None

    def _execute_one_round(self) -> OperationOneRoundResult:
        screen = self.screenshot()
        mm = mini_map.cut_mini_map(screen, self.ctx.game_config.mini_map_pos)
        pos = mini_map.find_one_enemy_pos(mm=mm)

        if pos is None:
            return self.round_retry(MoveToEnemy.STATUS_ENEMY_NOT_FOUND, wait=1)

        center = Point(mm.shape[1] // 2, mm.shape[0] // 2)

        if pos is None or cal_utils.distance_between(pos, center) < 20:  # 已经到达
            self.ctx.controller.stop_moving_forward()
            return self.round_success()
        elif pos is not None:  # 朝目标走去
            now = time.time()
            if now - self.last_move_time > 0.5:  # 隔一段时间再调整方向移动
                self.last_enemy_pos = pos
                _, _, angle = mini_map.analyse_arrow_and_angle(mm)
                self.ctx.controller.move_towards(center, pos, angle,
                                                 run=self.run_mode == game_config_const.RUN_MODE_BTN)
            return self.round_wait()
        else:  # 不应该有这种情况
            return self.round_retry('unknown')


class MoveForward(Operation):

    def __init__(self, ctx: Context, seconds: float):
        """
        向前走一段时间
        :param ctx:
        :param seconds: 秒数
        """
        super().__init__(ctx, op_name='%s %s' %
                                      (gt('向前移动', 'ui'),
                                       gt('%.2f秒' % seconds, 'ui'))
                         )
        self.seconds: float = seconds

    def _execute_one_round(self) -> OperationOneRoundResult:
        self.ctx.controller.start_moving_forward()
        time.sleep(self.seconds)
        self.ctx.controller.stop_moving_forward()
        return self.round_success()


class MoveWithoutPos(StateOperation):

    def __init__(self, ctx: Context,
                 start: Point,
                 target: Point,
                 move_time: Optional[float] = None,
                 op_callback: Optional[Callable[[OperationResult], None]] = None):
        """
        从当前位置 朝目标点直线前行
        按照不疾跑的速度移动若干秒 中途不会计算坐标 也不会进行任何战斗判断
        适合在难以判断坐标的情况下使用 且中途不会有任何打断或困住的情况

        为了更稳定移动到目标点 使用前人物应该静止

        返回 data = 目标点坐标
        """
        turn = StateOperationNode('转向', self._turn)
        move = StateOperationNode('移动', self._move)

        super().__init__(ctx, op_name='%s %s -> %s' % (gt('机械移动', 'ui'), start, target),
                         nodes=[turn, move],
                         op_callback=op_callback)

        self.start: Point = start
        self.target: Point = target
        self.move_time: float = move_time
        if move_time is None:
            dis = cal_utils.distance_between(self.start, self.target)
            self.move_time = dis / self.ctx.controller.walk_speed

    def _turn(self) -> OperationOneRoundResult:
        screen = self.screenshot()

        mm = mini_map.cut_mini_map(screen, self.ctx.game_config.mini_map_pos)
        angle = mini_map.analyse_angle(mm)

        self.ctx.controller.turn_by_pos(self.start, self.target, angle)

        return self.round_success(wait=0.5)  # 等待转向结束

    def _move(self) -> OperationOneRoundResult:
        self.ctx.controller.move('w', self.move_time)

        return self.round_success(data=self.target)
