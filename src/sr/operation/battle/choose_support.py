from typing import Optional, ClassVar, List

from cv2.typing import MatLike

from basic import Rect, Point
from basic.i18_utils import gt
from basic.img import cv2_utils, MatchResult
from basic.log_utils import log
from sr.context import Context
from sr.operation import Operation, OperationOneRoundResult, StateOperation, StateOperationEdge, StateOperationNode
from sr.screen_area.screen_team import ScreenTeam


class ChooseSupport(StateOperation):

    STATUS_SUPPORT_NOT_FOUND: ClassVar[str] = '未找到支援角色'
    STATUS_SUPPORT_NEEDED: ClassVar[str] = '需要支援角色'

    def __init__(self, ctx: Context, character_id: Optional[str]):
        """
        在角色列表页 选择对应支援角色
        如果不传入支援角色 则直接返回成功
        :param ctx:
        :param character_id:
        """
        edges: List[StateOperationEdge] = []

        super().__init__(ctx, try_times=5,
                         op_name=gt('选择支援', 'ui'),
                         edges=edges
                         )

        self.character_id: str = character_id

    def add_edges_and_nodes(self) -> None:
        """
        初始化前 添加边和节点 由子类实行
        :return:
        """
        check_screen = StateOperationNode('识别画面', self.check_screen)

        click_avatar = StateOperationNode('点击头像', self.click_avatar)
        self.add_edge(check_screen, click_avatar, status=ChooseSupport.STATUS_SUPPORT_NEEDED)

        click_join = StateOperationNode('点击入队', self.click_join)
        self.add_edge(click_avatar, click_join)

        back = StateOperationNode('返回', self.back)
        self.add_edge(check_screen, back)  # 不需要支援的
        self.add_edge(click_avatar, back, success=False)  # 找不到支援角色的

    def handle_init(self) -> Optional[OperationOneRoundResult]:
        """
        执行前的初始化 由子类实现
        注意初始化要全面 方便一个指令重复使用
        可以返回初始化后判断的结果
        - 成功时跳过本指令
        - 失败时立刻返回失败
        - 不返回时正常运行本指令
        """
        self.found_character: bool = False
        """是否找到支援角色"""

        return None

    def check_screen(self) -> OperationOneRoundResult:
        """
        等待加载 左上角有【支援】
        :return:
        """
        area = ScreenTeam.SUPPORT_TITLE.value
        if self.find_area(area):
            if self.character_id is not None:
                return self.round_success(status=ChooseSupport.STATUS_SUPPORT_NEEDED)
            else:
                return self.round_success()
        else:
            return self.round_retry('未在%s画面' % area.status, wait_round_time=1)

    def click_avatar(self) -> OperationOneRoundResult:
        """
        点击头像
        :return:
        """
        screen = self.screenshot()
        pos = self._get_character_pos(screen)

        if pos is None:
            drag_from = ScreenTeam.SUPPORT_CHARACTER_LIST.value.rect.center
            drag_to = drag_from + Point(0, -400)
            self.ctx.controller.drag_to(drag_to, drag_from)
            return self.round_retry(ChooseSupport.STATUS_SUPPORT_NOT_FOUND, wait=2)
        else:
            click = self.ctx.controller.click(pos.center)
            if click:
                self.found_character = True
                return self.round_success(wait=0.5)
            else:
                return self.round_retry('点击头像失败', wait=0.5)

    def click_join(self) -> OperationOneRoundResult:
        """
        点击入队 点击后会返回上级画面
        :return:
        """
        area = ScreenTeam.SUPPORT_JOIN.value
        click = self.find_and_click_area(area)
        if click == Operation.OCR_CLICK_SUCCESS:
            return self.round_success(wait=1)
        else:
            return self.round_retry('点击%s失败' % area.status, wait=1)

    def _get_character_pos(self, screen: Optional[MatLike] = None) -> Optional[MatchResult]:
        """
        找到角色头像的位置
        :return:
        """
        if screen is None:
            screen: MatLike = self.screenshot()

        area = ScreenTeam.SUPPORT_CHARACTER_LIST.value
        part = cv2_utils.crop_image_only(screen, area.rect)

        # 先找到UID的位置
        ocr_result_map = self.ctx.ocr.match_words(part, words=['等级'], lcs_percent=0.1)
        if len(ocr_result_map) == 0:
            log.error('找不到等级')
            return None

        template = self.ctx.ih.get_character_avatar_template(self.character_id)
        if template is None:
            log.error('找不到角色头像模板 %s', self.character_id)
            return None

        for k, v in ocr_result_map.items():
            for pos in v:
                center = area.rect.left_top + pos.center
                avatar_rect = Rect(center.x - 42, center.y - 100, center.x + 55, center.y - 10)
                avatar_part = cv2_utils.crop_image_only(screen, avatar_rect)
                # cv2_utils.show_image(avatar_part, wait=0)
                source_kps, source_desc = cv2_utils.feature_detect_and_compute(avatar_part)

                character_pos = cv2_utils.feature_match_for_one(
                    source_kps, source_desc,
                    template.kps, template.desc,
                    template.origin.shape[1], template.origin.shape[0],
                    knn_distance_percent=0.5
                )

                if character_pos is not None:
                    character_pos.x += avatar_rect.left_top.x
                    character_pos.y += avatar_rect.left_top.y
                    return character_pos

        return None

    def back(self) -> OperationOneRoundResult:
        """
        返回
        :return:
        """
        self.ctx.controller.click(ScreenTeam.SUPPORT_CLOSE.value.rect.center)

        if self.character_id is None:
            return self.round_success('无需支援')
        elif self.found_character:
            return self.round_success(wait_round_time=1.5)
        else:
            return self.round_fail(ChooseSupport.STATUS_SUPPORT_NOT_FOUND, wait_round_time=1.5)

