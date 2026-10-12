import json
import os
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Optional

from pydantic import BaseModel, ValidationError, field_validator

from models import Analysis, Attendance, Employee, LeaveRequest, db


class AnalysisServiceError(Exception):
    pass


class BaseAnalysisResult(BaseModel):
    personality: str
    motivation: int
    health: int
    report: str

    @field_validator("motivation", "health")
    @classmethod
    def validate_score(cls, value):
        if not 0 <= value <= 100:
            raise ValueError("score must be between 0 and 100")
        return value


class LongAnalysisResult(BaseAnalysisResult):
    pass


class ShortAnalysisResult(BaseAnalysisResult):
    pass


@dataclass
class AttendancePayload:
    employee_id: str
    employee_profile: dict[str, Any]
    attendance_summary: dict[str, Any]
    attendance_data: list[dict[str, Any]]


WORK_START_TIME = time(9, 0, 0)
WORK_END_TIME = time(17, 0, 0)

ZODIAC_RULES = [
    ((1, 20), "水瓶座"),
    ((2, 19), "双鱼座"),
    ((3, 21), "白羊座"),
    ((4, 20), "金牛座"),
    ((5, 21), "双子座"),
    ((6, 22), "巨蟹座"),
    ((7, 23), "狮子座"),
    ((8, 23), "处女座"),
    ((9, 23), "天秤座"),
    ((10, 24), "天蝎座"),
    ((11, 23), "射手座"),
    ((12, 22), "摩羯座"),
]

ZODIAC_TRAITS = {
    "白羊座": "通常表现为行动直接、执行力较强，适合作为积极度判断的辅助参考。",
    "金牛座": "通常表现为稳定务实、耐心较好，适合作为持续性和抗压性判断的辅助参考。",
    "双子座": "通常表现为思维灵活、适应变化较快，适合作为沟通和节奏变化判断的辅助参考。",
    "巨蟹座": "通常表现为重视安全感、情绪感知较强，适合作为压力与健康感受判断的辅助参考。",
    "狮子座": "通常表现为自我驱动较明显、希望获得认可，适合作为主动性判断的辅助参考。",
    "处女座": "通常表现为细致谨慎、规则感较强，适合作为纪律性判断的辅助参考。",
    "天秤座": "通常表现为重视平衡与合作，适合作为团队适配与情绪稳定判断的辅助参考。",
    "天蝎座": "通常表现为专注度较高、内在驱动力较强，适合作为投入深度判断的辅助参考。",
    "射手座": "通常表现为开放主动、探索意愿较强，适合作为积极性和变化偏好判断的辅助参考。",
    "摩羯座": "通常表现为责任感较强、目标意识清晰，适合作为长期稳定性判断的辅助参考。",
    "水瓶座": "通常表现为独立性较强、接受新事物较快，适合作为创新适应判断的辅助参考。",
    "双鱼座": "通常表现为感受力较强、共情能力较高，适合作为情绪波动与恢复判断的辅助参考。",
}

TRADITIONAL_DAY_BUCKETS = [
    ((1, 10), "月初出生，传统上常被视为起步感较强，做事更容易呈现主动展开的倾向。"),
    ((11, 20), "月中出生，传统上常被视为节奏较稳、重视平衡，做事往往更讲究持续与协调。"),
    ((21, 31), "月末出生，传统上常被视为韧性较好，面对压力时更容易表现出收束与坚持。"),
]


def _get_deepseek_client():
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise AnalysisServiceError("未检测到 DEEPSEEK_API_KEY，无法生成分析。")

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise AnalysisServiceError("未安装 OpenAI SDK，请先执行依赖安装。") from exc

    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    return OpenAI(api_key=api_key, base_url=base_url)


def get_zodiac_sign(birth_date: Optional[date]) -> str:
    if birth_date is None:
        return "未知"

    month_day = (birth_date.month, birth_date.day)
    current_sign = "摩羯座"
    for threshold, sign in ZODIAC_RULES:
        if month_day >= threshold:
            current_sign = sign
    return current_sign


def get_traditional_personality_hint(birth_date: Optional[date]) -> str:
    if birth_date is None:
        return "未提供生日，无法补充传统性格辅助描述。"

    for (start_day, end_day), description in TRADITIONAL_DAY_BUCKETS:
        if start_day <= birth_date.day <= end_day:
            return description
    return "传统辅助描述缺失。"


def build_employee_profile_context(employee: Employee) -> dict[str, Any]:
    zodiac_sign = get_zodiac_sign(employee.birth_date)
    return {
        "employee_id": employee.employee_id,
        "name": employee.name,
        "position": employee.position or "未设置",
        "hire_date": employee.hire_date.isoformat() if employee.hire_date else None,
        "birth_date": employee.birth_date.isoformat() if employee.birth_date else None,
        "zodiac_sign": zodiac_sign,
        "zodiac_hint": ZODIAC_TRAITS.get(zodiac_sign, "无额外星座辅助描述。"),
        "traditional_hint": get_traditional_personality_hint(employee.birth_date),
    }


def _build_attendance_summary(employee_id: str, attendance_rows: list[Attendance]) -> dict[str, Any]:
    normal_checkin_count = 0
    late_count = 0
    early_leave_count = 0
    normal_checkout_count = 0
    day_set = set()

    for row in attendance_rows:
        day_set.add(row.timestamp.date().isoformat())
        if row.attendance_status == Attendance.STATUS_NORMAL_CHECKIN:
            normal_checkin_count += 1
        elif row.attendance_status == Attendance.STATUS_LATE:
            late_count += 1
        elif row.attendance_status == Attendance.STATUS_EARLY_LEAVE:
            early_leave_count += 1
        elif row.attendance_status == Attendance.STATUS_NORMAL_CHECKOUT:
            normal_checkout_count += 1

    leave_rows = (
        LeaveRequest.query.filter(
            LeaveRequest.employee_id == employee_id,
            LeaveRequest.status != LeaveRequest.STATUS_REJECTED,
        )
        .order_by(LeaveRequest.start_time.asc())
        .all()
    )
    leave_count = len(leave_rows)
    approved_leave_count = len(
        [row for row in leave_rows if row.status == LeaveRequest.STATUS_APPROVED]
    )
    pending_leave_count = len(
        [row for row in leave_rows if row.status == LeaveRequest.STATUS_PENDING]
    )

    absent_count = 0
    if attendance_rows:
        start_date = attendance_rows[0].timestamp.date()
        end_date = attendance_rows[-1].timestamp.date()
        current_day = start_date
        while current_day <= end_date:
            day_key = current_day.isoformat()
            if day_key not in day_set:
                day_start = datetime.combine(current_day, time.min)
                day_end = datetime.combine(current_day, time.max)
                has_leave = (
                    LeaveRequest.query.filter(
                        LeaveRequest.employee_id == employee_id,
                        LeaveRequest.status != LeaveRequest.STATUS_REJECTED,
                        LeaveRequest.start_time <= day_end,
                        LeaveRequest.end_time >= day_start,
                    ).first()
                    is not None
                )
                if not has_leave:
                    absent_count += 1
            current_day = current_day.fromordinal(current_day.toordinal() + 1)

    return {
        "attendance_days": len(day_set),
        "normal_checkin_count": normal_checkin_count,
        "late_count": late_count,
        "early_leave_count": early_leave_count,
        "normal_checkout_count": normal_checkout_count,
        "absent_count": absent_count,
        "leave_count": leave_count,
        "approved_leave_count": approved_leave_count,
        "pending_leave_count": pending_leave_count,
        "rules": {
            "work_start_time": WORK_START_TIME.isoformat(),
            "work_end_time": WORK_END_TIME.isoformat(),
        },
    }


def build_attendance_payload(employee_id: str) -> AttendancePayload:
    employee = Employee.query.filter_by(employee_id=employee_id).first()
    if employee is None:
        raise AnalysisServiceError("未找到指定员工，无法构造分析输入。")

    attendance_rows = (
        Attendance.query.filter_by(employee_id=employee_id)
        .order_by(Attendance.timestamp.asc())
        .all()
    )
    attendance_data = [
        {
            "timestamp": row.timestamp.isoformat(),
            "type": row.type,
            "attendance_status": row.attendance_status,
        }
        for row in attendance_rows
    ]
    return AttendancePayload(
        employee_id=employee_id,
        employee_profile=build_employee_profile_context(employee),
        attendance_summary=_build_attendance_summary(employee_id, attendance_rows),
        attendance_data=attendance_data,
    )


def build_recent_attendance_payload(employee_id: str, min_days: int = 7) -> AttendancePayload:
    employee = Employee.query.filter_by(employee_id=employee_id).first()
    if employee is None:
        raise AnalysisServiceError("未找到指定员工，无法构造分析输入。")

    attendance_rows = (
        Attendance.query.filter_by(employee_id=employee_id)
        .order_by(Attendance.timestamp.desc())
        .all()
    )

    selected_rows = []
    selected_days = []
    day_set = set()
    for row in attendance_rows:
        day_key = row.timestamp.date().isoformat()
        if day_key not in day_set:
            day_set.add(day_key)
            selected_days.append(day_key)
        if day_key in day_set and len(day_set) <= min_days:
            selected_rows.append(row)
        if len(day_set) >= min_days and row.timestamp.date().isoformat() not in selected_days[:min_days]:
            continue

    latest_days = selected_days[:min_days]
    filtered_rows = [
        row for row in attendance_rows if row.timestamp.date().isoformat() in latest_days
    ]
    filtered_rows.sort(key=lambda row: row.timestamp)

    if len(latest_days) < min_days:
        raise AnalysisServiceError("该员工最近有效打卡天数不足 7 天，无法生成短期分析。")

    attendance_data = [
        {
            "timestamp": row.timestamp.isoformat(),
            "type": row.type,
            "attendance_status": row.attendance_status,
        }
        for row in filtered_rows
    ]
    return AttendancePayload(
        employee_id=employee_id,
        employee_profile=build_employee_profile_context(employee),
        attendance_summary=_build_attendance_summary(employee_id, filtered_rows),
        attendance_data=attendance_data,
    )


def get_recent_attendance_day_count(employee_id: str) -> int:
    attendance_rows = (
        Attendance.query.filter_by(employee_id=employee_id)
        .order_by(Attendance.timestamp.desc())
        .all()
    )
    day_set = []
    seen = set()
    for row in attendance_rows:
        day_key = row.timestamp.date().isoformat()
        if day_key not in seen:
            seen.add(day_key)
            day_set.append(day_key)
    return len(day_set)


def _build_chart_data_from_attendance_data(attendance_data: list[dict[str, Any]]) -> dict[str, list[Any]]:
    grouped = {}
    for item in attendance_data:
        dt = datetime.fromisoformat(item["timestamp"])
        day_key = dt.date().isoformat()
        grouped.setdefault(day_key, []).append(dt)

    labels = sorted(grouped.keys())
    motivation_values = []
    health_values = []

    for label in labels:
        times = sorted(grouped[label])
        count_score = min(len(times) * 12, 36)
        punctual_bonus = 12 if times[0].hour < 9 or (times[0].hour == 9 and times[0].minute <= 5) else 0
        spread_bonus = 8 if len(times) >= 2 else 0
        motivation = min(100, 52 + count_score + punctual_bonus + spread_bonus)

        hour_diff = abs((times[0].hour + times[0].minute / 60) - 9)
        health_penalty = min(int(hour_diff * 10), 20)
        consistency_bonus = 10 if len(times) >= 1 else 0
        health = max(55, min(100, 88 - health_penalty + consistency_bonus - max(len(times) - 3, 0) * 3))

        motivation_values.append(motivation)
        health_values.append(health)

    return {
        "labels": labels,
        "motivation_values": motivation_values,
        "health_values": health_values,
    }


def build_long_chart_data(employee_id: str) -> dict[str, list[Any]]:
    payload = build_attendance_payload(employee_id)
    if not payload.attendance_data:
        raise AnalysisServiceError("该员工暂无历史打卡记录，无法生成长期趋势图。")
    return _build_chart_data_from_attendance_data(payload.attendance_data)


def build_short_chart_data(employee_id: str) -> dict[str, list[Any]]:
    payload = build_recent_attendance_payload(employee_id)
    return _build_chart_data_from_attendance_data(payload.attendance_data)


def _render_fixed_report(
    title: str,
    employee: Employee,
    personality: str,
    motivation: int,
    health: int,
    report_summary: str,
    note: str,
    payload: AttendancePayload,
) -> str:
    return (
        f"【{title}】\n"
        f"员工工号：{employee.employee_id}\n"
        f"员工姓名：{employee.name}\n"
        f"工作岗位：{employee.position or '未设置'}\n"
        f"入职时间：{employee.hire_date.isoformat()}\n"
        f"出生日期：{payload.employee_profile.get('birth_date') or '未提供'}\n"
        f"星座：{payload.employee_profile.get('zodiac_sign', '未知')}\n\n"
        "【核心结论】\n"
        f"性格特征：{personality}\n"
        f"工作积极度：{motivation}\n"
        f"健康度：{health}\n\n"
        "【分析依据】\n"
        f"星座辅助描述：{payload.employee_profile.get('zodiac_hint')}\n"
        f"传统辅助描述：{payload.employee_profile.get('traditional_hint')}\n"
        f"考勤摘要：正常出勤 {payload.attendance_summary.get('normal_checkin_count', 0)} 次，"
        f"迟到 {payload.attendance_summary.get('late_count', 0)} 次，"
        f"早退 {payload.attendance_summary.get('early_leave_count', 0)} 次，"
        f"正常下班 {payload.attendance_summary.get('normal_checkout_count', 0)} 次，"
        f"缺勤 {payload.attendance_summary.get('absent_count', 0)} 次，"
        f"请假 {payload.attendance_summary.get('leave_count', 0)} 次。\n\n"
        "【详细分析】\n"
        f"{report_summary}\n\n"
        "【说明】\n"
        f"{note}"
    )


def _build_system_prompt(mode: str) -> str:
    return (
        f"你是一名企业员工{mode}行为分析助手，请根据历史打卡数据、出生日期、星座辅助信息、传统性格辅助描述和考勤状态摘要输出 json。"
        "星座和传统资料只能作为辅助参考，不能替代考勤事实本身。"
        "必须严格返回一个 JSON 对象，字段固定为："
        "personality、motivation、health、report。"
        "其中 motivation 和 health 必须是 0 到 100 的整数。"
        "report 字段必须是中文详细分析正文，不要输出 Markdown，不要输出额外字段。"
        "json 示例："
        '{'
        '"personality":"稳健细致",'
        '"motivation":82,'
        '"health":76,'
        '"report":"该员工近期打卡节奏稳定，出勤规律较好，整体投入度较高。"}'
    )


def _call_analysis_llm(
    employee: Employee,
    payload: AttendancePayload,
    mode: str,
    result_model: type[BaseAnalysisResult],
) -> BaseAnalysisResult:
    client = _get_deepseek_client()
    model = os.getenv("LONG_ANALYSIS_MODEL", "deepseek-reasoner")

    user_prompt = (
        f"请基于以下员工{mode}分析输入，输出 {mode}行为分析 json。\n"
        "要求：\n"
        "1. personality 用一句中文概括员工特征。\n"
        "2. motivation 用 0-100 整数表示工作积极度。\n"
        "3. health 用 0-100 整数表示健康度。\n"
        "4. report 用固定报告模板里的“详细分析”正文风格输出，不要重复字段名。\n"
        "5. 必须综合考勤状态摘要、请假情况、出生日期和星座辅助信息，形成更完整的判断。\n"
        "6. 星座和传统性格辅助文本只能作为解释性参考，不要输出绝对化结论。\n"
        f"员工基础信息：工号={employee.employee_id}，姓名={employee.name}，"
        f"岗位={employee.position or '未设置'}，入职时间={employee.hire_date.isoformat()}，"
        f"出生日期={payload.employee_profile.get('birth_date') or '未提供'}，"
        f"星座={payload.employee_profile.get('zodiac_sign', '未知')}。\n"
        f"星座辅助描述：{payload.employee_profile.get('zodiac_hint')}\n"
        f"传统辅助描述：{payload.employee_profile.get('traditional_hint')}\n"
        f"考勤状态摘要：{json.dumps(payload.attendance_summary, ensure_ascii=False)}\n"
        f"输入 JSON：{json.dumps(payload.__dict__, ensure_ascii=False)}"
    )

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _build_system_prompt(mode)},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            max_tokens=4096,
        )
    except Exception as exc:
        raise AnalysisServiceError(f"{mode}分析调用失败：{exc}") from exc

    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError) as exc:
        raise AnalysisServiceError(f"{mode}分析返回为空，无法解析。") from exc

    if not content:
        raise AnalysisServiceError(f"{mode}分析返回为空内容，无法保存。")

    try:
        parsed_json = json.loads(content)
        parsed = result_model.model_validate(parsed_json)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise AnalysisServiceError(f"{mode}分析结果格式异常，无法保存。") from exc

    return parsed


def generate_long_analysis_for_employee(employee_id: str) -> Analysis:
    employee = Employee.query.filter_by(employee_id=employee_id).first()
    if employee is None:
        raise AnalysisServiceError("未找到指定员工，无法生成长期分析。")

    payload = build_attendance_payload(employee_id)
    if not payload.attendance_data:
        raise AnalysisServiceError("该员工暂无历史打卡记录，无法生成长期分析。")

    parsed = _call_analysis_llm(employee, payload, "长期", LongAnalysisResult)
    analysis = Analysis(
        employee_id=employee.employee_id,
        type="long",
        report_text=_render_fixed_report(
            "长期行为分析报告",
            employee,
            parsed.personality,
            parsed.motivation,
            parsed.health,
            parsed.report,
            "本报告基于历史打卡记录生成，仅作为长期行为趋势参考。",
            payload,
        ),
        personality=parsed.personality,
        motivation=str(parsed.motivation),
        health=str(parsed.health),
    )
    db.session.add(analysis)
    db.session.commit()
    return analysis


def generate_short_analysis_for_employee(employee_id: str) -> Analysis:
    employee = Employee.query.filter_by(employee_id=employee_id).first()
    if employee is None:
        raise AnalysisServiceError("未找到指定员工，无法生成短期分析。")

    payload = build_recent_attendance_payload(employee_id)
    parsed = _call_analysis_llm(employee, payload, "短期", ShortAnalysisResult)
    analysis = Analysis(
        employee_id=employee.employee_id,
        type="short",
        report_text=_render_fixed_report(
            "短期行为分析报告",
            employee,
            parsed.personality,
            parsed.motivation,
            parsed.health,
            parsed.report,
            "本报告基于最近不少于 7 天的打卡记录生成，仅作为短期状态观察参考。",
            payload,
        ),
        personality=parsed.personality,
        motivation=str(parsed.motivation),
        health=str(parsed.health),
    )
    db.session.add(analysis)
    db.session.commit()
    return analysis
