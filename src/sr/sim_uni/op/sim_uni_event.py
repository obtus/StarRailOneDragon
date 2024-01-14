from typing import ClassVar, List, Optional

from cv2.typing import MatLike

from basic import Rect, Point
from basic.i18_utils import gt
from basic.img import cv2_utils
from sr.context import Context
from sr.image.sceenshot import screen_state
from sr.operation import StateOperation, OperationOneRoundResult, Operation, StateOperationNode, StateOperationEdge
from sr.sim_uni.op.sim_uni_choose_bless import SimUniChooseBless
from sr.sim_uni.op.sim_uni_choose_curio import SimUniChooseCurio
from sr.sim_uni.sim_uni_priority import SimUniBlessPriority, SimUniCurioPriority


class SimUniEventOption:

    def __init__(self, title: str, title_rect: Rect, confirm_rect: Optional[Rect] = None):
        self.title: str = title
        self.title_rect: Rect = title_rect
        self.confirm_rect: Optional[Rect] = confirm_rect  # 开始对话部分的选项不需要确认


class SimUniEvent(StateOperation):

    STATUS_CHOOSE_OPT_CONFIRM: ClassVar[str] = '需确认'
    STATUS_CHOOSE_OPT_NO_CONFIRM: ClassVar[str] = '无需确认'
    STATUS_CONFIRM_SUCCESS: ClassVar[str] = '确认成功'

    OPT_RECT: ClassVar[Rect] = Rect(1335, 204, 1826, 886)  # 选项所在的地方
    EMPTY_POS: ClassVar[Point] = Point(778, 880)

    def __init__(self, ctx: Context,
                 bless_priority: Optional[SimUniBlessPriority] = None,
                 curio_priority: Optional[SimUniCurioPriority] = None):
        """
        模拟宇宙 事件
        :param ctx:
        """
        edges = []

        wait = StateOperationNode('等待加载', self._wait)
        choose_opt = StateOperationNode('选择', self._choose_opt_by_priority)
        edges.append(StateOperationEdge(wait, choose_opt))

        confirm = StateOperationNode('确认', self._confirm)
        edges.append(StateOperationEdge(choose_opt, confirm, status=SimUniEvent.STATUS_CHOOSE_OPT_CONFIRM))

        check_after_confirm = StateOperationNode('确认后判断', self._check_after_confirm)
        edges.append(StateOperationEdge(confirm, check_after_confirm, status=SimUniEvent.STATUS_CONFIRM_SUCCESS))
        edges.append(StateOperationEdge(choose_opt, check_after_confirm, status=SimUniEvent.STATUS_CHOOSE_OPT_NO_CONFIRM))

        choose_exit = StateOperationNode('选择离开', self._choose_leave)
        edges.append(StateOperationEdge(confirm, choose_exit, status='无效选项'))
        edges.append(StateOperationEdge(choose_exit, confirm))

        bless = StateOperationNode('选择祝福', self._choose_bless)
        curio = StateOperationNode('选择奇物', self._choose_curio)
        empty = StateOperationNode('点击空白处关闭', self._click_empty_to_continue)
        edges.append(StateOperationEdge(check_after_confirm, bless, status='选择祝福'))
        edges.append(StateOperationEdge(check_after_confirm, curio, status='选择奇物'))
        edges.append(StateOperationEdge(check_after_confirm, choose_opt, status='事件'))
        edges.append(StateOperationEdge(check_after_confirm, empty, status='点击空白处关闭'))

        edges.append(StateOperationEdge(bless, check_after_confirm))
        edges.append(StateOperationEdge(curio, check_after_confirm))
        edges.append(StateOperationEdge(empty, check_after_confirm))

        super().__init__(ctx, try_times=10,
                         op_name='%s %s' % (gt('模拟宇宙', 'ui'), gt('事件', 'ui')),
                         edges=edges,
                         # specified_start_node=bless
                         )

        self.opt_list: List[SimUniEventOption] = []
        self.bless_priority: Optional[SimUniBlessPriority] = bless_priority
        self.curio_priority: Optional[SimUniCurioPriority] = curio_priority

    def _init_before_execute(self):
        """
        执行前的初始化 注意初始化要全面 方便一个指令重复使用
        """
        super()._init_before_execute()
        self.opt_list = []

    def _wait(self) -> OperationOneRoundResult:
        screen = self.screenshot()

        if screen_state.in_sim_uni_event(screen, self.ctx.ocr):
            return Operation.round_success()
        else:
            return Operation.round_retry('未在事件页面')

    def _choose_opt_by_priority(self) -> OperationOneRoundResult:
        """
        根据优先级选一个选项
        目前固定选第一个
        :return:
        """
        screen = self.screenshot()
        self.opt_list = self._get_opt_list(screen)

        if len(self.opt_list) == 0:
            # 有可能在对话
            self.ctx.controller.click(SimUniEvent.EMPTY_POS)
            return Operation.round_retry('未检测到选项', wait=0.5)
        else:
            return self._do_choose_opt(0)

    def _get_opt_list(self, screen: MatLike) -> List[SimUniEventOption]:
        """
        获取当前的选项
        :param screen:
        :return:
        """
        opt_list_1 = self._get_confirm_opt_list(screen)
        opt_list_2 = self._get_no_confirm_opt_list(screen)
        return opt_list_1 + opt_list_2

    def _get_confirm_opt_list(self, screen: MatLike) -> List[SimUniEventOption]:
        """
        获取当前需要确认的选项
        :param screen:
        :return:
        """
        part, _ = cv2_utils.crop_image(screen, SimUniEvent.OPT_RECT)
        match_result_list = self.ctx.im.match_template(
            part, 'event_option_icon', template_sub_dir='sim_uni',
            only_best=False)

        opt_list = []
        for mr in match_result_list:
            title_lt = SimUniEvent.OPT_RECT.left_top + mr.left_top + Point(30, 0)
            title_rb = SimUniEvent.OPT_RECT.left_top + mr.right_bottom + Point(430, 0)
            title_rect = Rect(title_lt.x, title_lt.y, title_rb.x, title_rb.y)

            title_part, _ = cv2_utils.crop_image(screen, title_rect)
            title = self.ctx.ocr.ocr_for_single_line(title_part)

            confirm_lt = SimUniEvent.OPT_RECT.left_top + mr.left_top + Point(260, 90)
            confirm_rb = SimUniEvent.OPT_RECT.left_top + mr.left_top + Point(440, 130)
            confirm_rect = Rect(confirm_lt.x, confirm_lt.y, confirm_rb.x, confirm_rb.y)
            # confirm_part, _ = cv2_utils.crop_image(screen, confirm_rect)
            # cv2_utils.show_image(confirm_part, wait=0)

            opt_list.append(SimUniEventOption(title, title_rect, confirm_rect))

        return opt_list

    def _get_no_confirm_opt_list(self, screen: MatLike) -> List[SimUniEventOption]:
        """
        获取当前不需要确认的选项
        :param screen:
        :return:
        """
        part, _ = cv2_utils.crop_image(screen, SimUniEvent.OPT_RECT)
        opt_list = []
        template_id_list = [
            'event_option_no_confirm_icon',
            # 'event_option_enhance_icon',  # TODO 暂时不考虑强化祝福
            'event_option_exit_icon'
        ]

        for template_id in template_id_list:
            match_result_list = self.ctx.im.match_template(part, template_id, template_sub_dir='sim_uni', only_best=False)

            for mr in match_result_list:
                title_lt = SimUniEvent.OPT_RECT.left_top + mr.left_top + Point(50, 0)
                title_rb = SimUniEvent.OPT_RECT.left_top + mr.right_bottom + Point(430, 0)
                title_rect = Rect(title_lt.x, title_lt.y, title_rb.x, title_rb.y)

                title_part, _ = cv2_utils.crop_image(screen, title_rect)
                title = self.ctx.ocr.ocr_for_single_line(title_part)

                opt_list.append(SimUniEventOption(title, title_rect))

        return opt_list

    def _confirm(self):
        click = self.ocr_and_click_one_line('确认', self.chosen_opt.confirm_rect)
        if click == Operation.OCR_CLICK_SUCCESS:
            return Operation.round_success(SimUniEvent.STATUS_CONFIRM_SUCCESS, wait=1)
        elif click == Operation.OCR_CLICK_NOT_FOUND:
            return Operation.round_success('无效选项')
        else:
            return Operation.round_success('点击确认失败', wait=0.25)

    def _do_choose_opt(self, idx: int) -> OperationOneRoundResult:
        click = self.ctx.controller.click(self.opt_list[idx].title_rect.center)
        if click:
            self.chosen_opt = self.opt_list[idx]
            if self.chosen_opt.confirm_rect is None:
                status = SimUniEvent.STATUS_CHOOSE_OPT_NO_CONFIRM
                return Operation.round_success(status, wait=1)
            else:
                status = SimUniEvent.STATUS_CHOOSE_OPT_CONFIRM
                return Operation.round_success(status, wait=0.5)
        else:
            return Operation.round_retry('点击选项失败', wait=0.5)

    def _choose_leave(self):
        """
        选择最后一个代表离开
        :return:
        """
        idx = len(self.opt_list) - 1
        return self._do_choose_opt(idx)

    def _check_after_confirm(self) -> OperationOneRoundResult:
        """
        确认后判断下一步动作
        :return:
        """
        screen = self.screenshot()
        state = self._get_screen_state(screen)
        if state is None:
            return Operation.round_retry('未能判断当前页面', wait=1)
        else:
            return Operation.round_success(state)

    def _get_screen_state(self, screen: MatLike) -> Optional[str]:
        if screen_state.is_empty_to_close(screen, self.ctx.ocr):
            return '点击空白处关闭'
        elif screen_state.in_sim_uni_secondary_ui(screen, self.ctx.ocr):
            if screen_state.in_sim_uni_choose_bless(screen, self.ctx.ocr):
                return '选择祝福'
            elif screen_state.in_sim_uni_choose_curio(screen, self.ctx.ocr):
                return '选择奇物'
            elif screen_state.in_sim_uni_event(screen, self.ctx.ocr):
                return '事件'
        elif screen_state.is_normal_in_world(screen, self.ctx.im):
            return '大世界'
        return None

    def _choose_bless(self) -> OperationOneRoundResult:
        op = SimUniChooseBless(self.ctx, priority=self.bless_priority)
        op_result = op.execute()

        if op_result.success:
            return Operation.round_success()
        else:
            return Operation.round_fail('选择祝福失败')

    def _choose_curio(self) -> OperationOneRoundResult:
        op = SimUniChooseCurio(self.ctx, priority=self.curio_priority)
        op_result = op.execute()

        if op_result.success:
            return Operation.round_success()
        else:
            return Operation.round_fail('选择奇物失败')

    def _click_empty_to_continue(self) -> OperationOneRoundResult:
        click = self.ctx.controller.click(screen_state.TargetRect.EMPTY_TO_CONTINUE.value.center)

        if click:
            return Operation.round_success()
        else:
            return Operation.round_retry('点击空白处关闭失败')


class SimUniEventHerta(StateOperation):

    FIRST_CHOOSE_BTN: ClassVar[Rect] = Rect(1405, 724, 1802, 771)  # 第一次出现的【选择】

    OPT_1_TITLE: ClassVar[Rect] = Rect(1390, 208, 1821, 242)  # 购买1个1星祝福
    OPT_1_CONFIRM: ClassVar[Rect] = Rect(1609, 297, 1796, 338)  # 确认

    def __init__(self, ctx: Context,
                 bless_priority: Optional[SimUniBlessPriority] = None,
                 curio_priority: Optional[SimUniCurioPriority] = None):
        """
        模拟宇宙 跟黑塔的交互
        :param ctx:
        """
        edges = []

        wait = StateOperationNode('等待加载', self._wait)
        first_choose = StateOperationNode('选择', self._first_choose)
        edges.append(StateOperationEdge(wait, first_choose))

        event_option = StateOperationNode('选项', self._event_option)
        edges.append(StateOperationEdge(first_choose, event_option))

        super().__init__(ctx, try_times=10,
                         op_name='%s %s' % (gt('模拟宇宙', 'ui'), gt('黑塔', 'ui')),
                         edges=edges
                         )

        self.bless_priority: Optional[SimUniBlessPriority] = bless_priority
        self.curio_priority: Optional[SimUniCurioPriority] = curio_priority

    def _wait(self) -> OperationOneRoundResult:
        screen = self.screenshot()

        if screen_state.in_sim_uni_event(screen, self.ctx.ocr):
            return Operation.round_success()
        else:
            return Operation.round_retry('未在事件页面')

    def _first_choose(self) -> OperationOneRoundResult:
        click = self.ocr_and_click_one_line('选择', SimUniEventHerta.FIRST_CHOOSE_BTN)
        if click == Operation.OCR_CLICK_SUCCESS:
            return Operation.round_success(wait=0.5)
        else:
            return Operation.round_retry('点击选择失败', wait=0.5)

    def _event_option(self) -> OperationOneRoundResult:
        op = SimUniEvent(self.ctx)
        op_result = op.execute()

        if op_result.success:
            return Operation.round_success()
        else:
            return Operation.round_fail('事件选择失败')