import base64
import binascii
import json
from datetime import date, datetime, time, timedelta
from functools import wraps
from io import BytesIO
from pathlib import Path
from shutil import copy2
from uuid import uuid4

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.utils import secure_filename

from models import (
    Admin,
    Analysis,
    Attendance,
    Employee,
    EmployeeFaceProfile,
    LeaveRequest,
    db,
)
from services.analysis_service import (
    AnalysisServiceError,
    build_long_chart_data,
    build_short_chart_data,
    generate_long_analysis_for_employee,
    generate_short_analysis_for_employee,
    get_recent_attendance_day_count,
)


main_bp = Blueprint("main", __name__)


ADMIN_NAV_ITEMS = [
    {
        "endpoint": "main.admin",
        "title": "后台首页",
        "description": "查看管理员入口与系统模块概览。",
    },
    {
        "endpoint": "main.admin_employee_entry",
        "title": "员工数据录入与账号发放",
        "description": "录入员工基础资料并生成员工账号。",
    },
    {
        "endpoint": "main.admin_behavior_long_term",
        "title": "员工行为分析（长期）",
        "description": "汇总长期行为数据与趋势分析。",
    },
    {
        "endpoint": "main.admin_behavior_short_term",
        "title": "短期行为分析",
        "description": "查看短周期内的行为变化和提醒。",
    },
    {
        "endpoint": "main.admin_employee_list_page",
        "title": "员工列表管理",
        "description": "维护员工状态、信息和账号情况。",
    },
    {
        "endpoint": "main.admin_face_profile_list",
        "title": "人脸模板管理",
        "description": "维护员工刷脸打卡候选模板。",
    },
    {
        "endpoint": "main.admin_attendance_overview",
        "title": "员工出勤情况",
        "description": "查看员工出勤统计和每日明细。",
    },
    {
        "endpoint": "main.admin_leave_list",
        "title": "请假审核管理",
        "description": "查看员工请假申请并完成审批处理。",
    },
    {
        "endpoint": "main.admin_account_page",
        "title": "账户管理",
        "description": "管理管理员账户与权限配置入口。",
    },
]

EMPLOYEE_NAV_ITEMS = [
    {
        "endpoint": "main.checkin",
        "title": "员工工作台",
        "description": "查看考勤状态并提交请假申请。",
    },
    {
        "endpoint": "main.employee_leave_history",
        "title": "我的请假记录",
        "description": "查看个人请假申请与审批状态。",
    },
    {
        "endpoint": "main.employee_profile",
        "title": "个人资料",
        "description": "查看个人基础信息与岗位资料。",
    },
    {
        "endpoint": "main.employee_change_password",
        "title": "修改密码",
        "description": "维护个人登录密码。",
    },
]

ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
EMPLOYEE_ID_START = 410000000
EMPLOYEE_ID_MAX = 499999999
WORK_START_TIME = time(9, 0, 0)
WORK_END_TIME = time(17, 0, 0)


def login_required(role=None):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped_view(*args, **kwargs):
            user_role = session.get("user_role")
            if not user_role:
                flash("请先登录后再访问该页面。", "warning")
                return redirect(url_for("main.login"))

            if role and user_role != role:
                flash("您没有权限访问该页面。", "danger")
                if user_role == "admin":
                    return redirect(url_for("main.admin"))
                return redirect(url_for("main.checkin"))

            return view_func(*args, **kwargs)

        return wrapped_view

    return decorator


def api_response(success, message, data=None, status=200):
    payload = {
        "success": success,
        "message": message,
    }
    if data is not None:
        payload["data"] = data
    return jsonify(payload), status


def api_login_required(role=None):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped_view(*args, **kwargs):
            user_role = session.get("user_role")
            if not user_role:
                return api_response(False, "未登录或登录已失效。", status=401)

            if role and user_role != role:
                return api_response(False, "无权访问该接口。", status=403)

            return view_func(*args, **kwargs)

        return wrapped_view

    return decorator


def get_request_payload():
    return request.get_json(silent=True) or request.form


def set_admin_session(admin):
    session.clear()
    session["user_role"] = "admin"
    session["username"] = admin.username


def set_employee_session(employee):
    session.clear()
    session["user_role"] = "employee"
    session["employee_id"] = employee.employee_id
    session["employee_name"] = employee.name


def authenticate_credentials(username, password):
    if not username or not password:
        return None, None, "用户名和密码不能为空。"

    admin = Admin.query.filter_by(username=username).first()
    if admin and admin.password == password:
        return "admin", admin, None

    employee = Employee.query.filter_by(employee_id=username).first()
    if employee and employee.password == password:
        if employee.status != Employee.STATUS_ACTIVE:
            return None, None, "该员工已离职，无法登录。"
        return "employee", employee, None

    return None, None, "用户名或密码错误。"


def serialize_attendance(record):
    if record.type == Attendance.TYPE_CLOCK_IN:
        attendance_type_label = "上班打卡"
    elif record.type == Attendance.TYPE_CLOCK_OUT:
        attendance_type_label = "下班打卡"
    else:
        attendance_type_label = "打卡记录"

    return {
        "id": record.id,
        "employee_id": record.employee_id,
        "timestamp": record.timestamp.isoformat() if record.timestamp else None,
        "type": record.type,
        "type_label": attendance_type_label,
        "attendance_status": record.attendance_status,
    }


def serialize_face_checkin_result(employee, attendance, message, match):
    return {
        "employee": {
            "employee_id": employee.employee_id,
            "name": employee.name,
            "position": employee.position,
        },
        "attendance": serialize_attendance(attendance),
        "message": message,
        "match": {
            "profile_id": match["profile"].id,
            "source_type": match["profile"].source_type,
            "source_label": get_face_profile_source_label(match["profile"].source_type),
            "distance": match["distance"],
        },
    }


def serialize_leave_request(item, include_employee=False):
    data = {
        "id": item.id,
        "employee_id": item.employee_id,
        "start_time": item.start_time.isoformat() if item.start_time else None,
        "end_time": item.end_time.isoformat() if item.end_time else None,
        "reason": item.reason,
        "status": item.status,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "reviewed_at": item.reviewed_at.isoformat() if item.reviewed_at else None,
        "reviewed_by": item.reviewed_by,
    }
    if include_employee:
        data["employee_name"] = item.employee.name if item.employee else None
        data["position"] = item.employee.position if item.employee else None
    return data


def serialize_employee(employee):
    return {
        "id": employee.id,
        "employee_id": employee.employee_id,
        "name": employee.name,
        "photo_path": employee.photo_path,
        "avatar_path": employee.avatar_path,
        "photo_url": build_upload_url(employee.photo_path),
        "avatar_url": build_upload_url(employee.avatar_path),
        "birth_date": employee.birth_date.isoformat() if employee.birth_date else None,
        "position": employee.position,
        "contact": employee.contact,
        "hire_date": employee.hire_date.isoformat() if employee.hire_date else None,
        "status": employee.status,
    }


def serialize_face_profile(profile):
    return {
        "id": profile.id,
        "employee_id": profile.employee_id,
        "employee_name": profile.employee.name if profile.employee else None,
        "source_type": profile.source_type,
        "source_label": get_face_profile_source_label(profile.source_type),
        "source_image_path": profile.source_image_path,
        "source_image_url": build_upload_url(profile.source_image_path),
        "created_at": profile.created_at.isoformat() if profile.created_at else None,
        "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
        "is_active": profile.is_active,
    }


def serialize_analysis(analysis):
    return {
        "id": analysis.id,
        "employee_id": analysis.employee_id,
        "employee_name": analysis.employee.name if analysis.employee else None,
        "type": analysis.type,
        "personality": analysis.personality,
        "motivation": analysis.motivation,
        "health": analysis.health,
        "report_text": analysis.report_text,
        "created_at": analysis.created_at.isoformat() if analysis.created_at else None,
    }


def get_next_employee_id():
    # Only allocate new ids from the managed range so abnormal historical/test
    # ids do not push the next generated employee id to an unexpected value.
    employee_ids = Employee.query.with_entities(Employee.employee_id).all()
    valid_values = []
    for (employee_id,) in employee_ids:
        employee_id = str(employee_id or "").strip()
        if not employee_id.isdigit():
            continue

        numeric_value = int(employee_id)
        if EMPLOYEE_ID_START <= numeric_value <= EMPLOYEE_ID_MAX:
            valid_values.append(numeric_value)

    if not valid_values:
        return str(EMPLOYEE_ID_START)

    next_value = max(valid_values) + 1
    if next_value > EMPLOYEE_ID_MAX:
        raise ValueError("员工工号已超出可分配范围，请联系管理员处理。")
    return f"{next_value:09d}"


def parse_birth_date(value):
    return datetime.strptime(value, "%Y-%m-%d").date()


def allowed_image_file(filename):
    if "." not in filename:
        return False
    return filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


def save_employee_photo(photo, employee_id):
    original_name = secure_filename(photo.filename)
    file_ext = Path(original_name).suffix.lower()
    saved_name = f"{employee_id}_{uuid4().hex}{file_ext}"
    upload_dir = Path(current_app.config["UPLOAD_FOLDER"])
    upload_dir.mkdir(parents=True, exist_ok=True)
    saved_path = upload_dir / saved_name
    photo.save(saved_path)
    return f"uploads/{saved_name}"


def save_face_template_image(photo, employee_id):
    original_name = secure_filename(photo.filename)
    file_ext = Path(original_name).suffix.lower()
    saved_name = f"{employee_id}_{uuid4().hex}{file_ext}"
    upload_dir = Path(current_app.config["FACE_TEMPLATE_FOLDER"])
    upload_dir.mkdir(parents=True, exist_ok=True)
    saved_path = upload_dir / saved_name
    photo.save(saved_path)
    return f"uploads/face_templates/{saved_name}"


def build_upload_url(file_path):
    if not file_path:
        return None
    relative_path = str(file_path).replace("\\", "/")
    if relative_path.startswith("uploads/"):
        relative_path = relative_path[len("uploads/") :]
    return url_for("main.uploaded_file", filename=relative_path)


def get_uploaded_file_size(uploaded_file):
    stream = uploaded_file.stream
    current_pos = stream.tell()
    stream.seek(0, 2)
    size = stream.tell()
    stream.seek(current_pos)
    return size


def get_day_range(target_date):
    return (
        datetime.combine(target_date, time.min),
        datetime.combine(target_date, time.max),
    )


def get_attendance_records_for_day(employee_id, target_date):
    start_dt, end_dt = get_day_range(target_date)
    return (
        Attendance.query.filter(
            Attendance.employee_id == employee_id,
            Attendance.timestamp >= start_dt,
            Attendance.timestamp <= end_dt,
        )
        .order_by(Attendance.timestamp.asc(), Attendance.id.asc())
        .all()
    )


def order_leave_created_desc(query):
    # Keep NULL handling portable across SQLite and MySQL.
    return query.order_by(
        LeaveRequest.created_at.is_(None).asc(),
        LeaveRequest.created_at.desc(),
        LeaveRequest.id.desc(),
    )


def get_attendance_slot(record, record_index=0):
    if record.type == Attendance.TYPE_CLOCK_IN:
        return Attendance.TYPE_CLOCK_IN
    if record.type == Attendance.TYPE_CLOCK_OUT:
        return Attendance.TYPE_CLOCK_OUT
    return Attendance.TYPE_CLOCK_IN if record_index == 0 else Attendance.TYPE_CLOCK_OUT


def has_leave_for_day(employee_id, target_date):
    day_start, day_end = get_day_range(target_date)
    leave_request = (
        LeaveRequest.query.filter(
            LeaveRequest.employee_id == employee_id,
            LeaveRequest.status != LeaveRequest.STATUS_REJECTED,
            LeaveRequest.start_time <= day_end,
            LeaveRequest.end_time >= day_start,
        )
        .order_by(LeaveRequest.id.desc())
        .first()
    )
    return leave_request is not None


def get_daily_attendance_summary(employee_id, target_date=None):
    if target_date is None:
        target_date = datetime.now().date()

    records = get_attendance_records_for_day(employee_id, target_date)
    if records:
        latest_record = records[-1]
        latest_index = len(records) - 1
        next_expected = (
            "下班打卡"
            if get_attendance_slot(latest_record, latest_index) == Attendance.TYPE_CLOCK_IN
            and len(records) == 1
            else "已完成"
        )
        return {
            "date": target_date.isoformat(),
            "status": latest_record.attendance_status or "已打卡",
            "has_leave": False,
            "record_count": len(records),
            "next_expected": next_expected,
        }

    if has_leave_for_day(employee_id, target_date):
        return {
            "date": target_date.isoformat(),
            "status": "请假",
            "has_leave": True,
            "record_count": 0,
            "next_expected": "无需打卡",
        }

    now = datetime.now()
    if target_date < now.date() or (
        target_date == now.date() and now.time() >= WORK_END_TIME
    ):
        return {
            "date": target_date.isoformat(),
            "status": Attendance.STATUS_ABSENT,
            "has_leave": False,
            "record_count": 0,
            "next_expected": "已缺勤",
        }

    return {
        "date": target_date.isoformat(),
        "status": "待打卡",
        "has_leave": False,
        "record_count": 0,
        "next_expected": "上班打卡",
    }


def iter_dates(start_date, end_date):
    current_date = start_date
    while current_date <= end_date:
        yield current_date
        current_date += timedelta(days=1)


def get_attendance_status_counts():
    return {
        Attendance.STATUS_NORMAL_CHECKIN: 0,
        Attendance.STATUS_LATE: 0,
        Attendance.STATUS_EARLY_LEAVE: 0,
        Attendance.STATUS_NORMAL_CHECKOUT: 0,
        Attendance.STATUS_ABSENT: 0,
        "请假": 0,
    }


def get_attendance_day_detail(employee, target_date):
    records = get_attendance_records_for_day(employee.employee_id, target_date)
    summary = get_daily_attendance_summary(employee.employee_id, target_date)
    clock_in_record = None
    clock_out_record = None
    statuses = []

    for index, record in enumerate(records):
        slot = get_attendance_slot(record, index)
        if slot == Attendance.TYPE_CLOCK_IN and clock_in_record is None:
            clock_in_record = record
        elif slot == Attendance.TYPE_CLOCK_OUT and clock_out_record is None:
            clock_out_record = record

        if record.attendance_status:
            statuses.append(record.attendance_status)

    if not statuses:
        statuses.append(summary["status"])

    return {
        "employee": employee,
        "date": target_date,
        "status": summary["status"],
        "statuses": statuses,
        "clock_in_time": clock_in_record.timestamp if clock_in_record else None,
        "clock_out_time": clock_out_record.timestamp if clock_out_record else None,
        "record_count": len(records),
    }


def build_attendance_overview(employees, start_date, end_date):
    rows = []
    totals = get_attendance_status_counts()

    for employee in employees:
        counts = get_attendance_status_counts()
        details = []

        for target_date in iter_dates(start_date, end_date):
            detail = get_attendance_day_detail(employee, target_date)
            details.append(detail)

            if detail["record_count"] > 0:
                for status in detail["statuses"]:
                    if status in counts:
                        counts[status] += 1
                        totals[status] += 1
            elif detail["status"] in counts:
                counts[detail["status"]] += 1
                totals[detail["status"]] += 1

        rows.append(
            {
                "employee": employee,
                "counts": counts,
                "details": details,
            }
        )

    return rows, totals


def create_attendance_record(employee):
    now = datetime.now()
    today_records = get_attendance_records_for_day(employee.employee_id, now.date())

    has_clock_in = False
    has_clock_out = False
    for index, record in enumerate(today_records):
        slot = get_attendance_slot(record, index)
        if slot == Attendance.TYPE_CLOCK_IN:
            has_clock_in = True
        elif slot == Attendance.TYPE_CLOCK_OUT:
            has_clock_out = True

    if not has_clock_in:
        record_type = Attendance.TYPE_CLOCK_IN
        status = (
            Attendance.STATUS_NORMAL_CHECKIN
            if now.time() <= WORK_START_TIME
            else Attendance.STATUS_LATE
        )
        message = f"上班打卡成功，当前状态：{status}。"
    elif not has_clock_out:
        record_type = Attendance.TYPE_CLOCK_OUT
        status = (
            Attendance.STATUS_EARLY_LEAVE
            if now.time() < WORK_END_TIME
            else Attendance.STATUS_NORMAL_CHECKOUT
        )
        message = f"下班打卡成功，当前状态：{status}。"
    else:
        raise ValueError("今日已完成上班和下班打卡，无需重复提交。")

    attendance = Attendance(
        employee_id=employee.employee_id,
        timestamp=now,
        type=record_type,
        attendance_status=status,
    )
    db.session.add(attendance)
    db.session.commit()
    return attendance, message


def save_employee_avatar(photo, employee_id):
    original_name = secure_filename(photo.filename)
    file_ext = Path(original_name).suffix.lower()
    saved_name = f"{employee_id}_{uuid4().hex}{file_ext}"
    upload_dir = Path(current_app.config["UPLOAD_FOLDER"]) / "avatars"
    upload_dir.mkdir(parents=True, exist_ok=True)
    saved_path = upload_dir / saved_name
    photo.save(saved_path)
    return f"uploads/avatars/{saved_name}"


def build_face_template_source_path(photo_path):
    if not photo_path:
        return None

    relative_path = str(photo_path).replace("\\", "/")
    if relative_path.startswith("uploads/"):
        relative_path = relative_path[len("uploads/") :]
    return Path(current_app.config["UPLOAD_FOLDER"]) / relative_path


def get_face_profile_source_label(source_type):
    labels = {
        EmployeeFaceProfile.SOURCE_ID_PHOTO_COPY: "证件照复制",
        EmployeeFaceProfile.SOURCE_MANUAL_FACE_REGISTER: "管理员上传",
        EmployeeFaceProfile.SOURCE_CAMERA_CAPTURE: "摄像头采集",
    }
    return labels.get(source_type, source_type or "未知来源")


def deactivate_employee_face_profiles(employee_id, except_profile_id=None):
    query = EmployeeFaceProfile.query.filter_by(
        employee_id=employee_id,
        is_active=True,
    )
    if except_profile_id is not None:
        query = query.filter(EmployeeFaceProfile.id != except_profile_id)
    for profile in query.all():
        profile.is_active = False


def copy_id_photo_to_face_template(employee):
    if not employee.photo_path:
        raise ValueError("该员工暂无证件照，无法生成初始人脸模板。")

    source_path = build_face_template_source_path(employee.photo_path)
    if source_path is None or not source_path.exists():
        raise ValueError("员工证件照文件不存在，无法生成初始人脸模板。")

    template_dir = Path(current_app.config["FACE_TEMPLATE_FOLDER"])
    template_dir.mkdir(parents=True, exist_ok=True)
    file_ext = source_path.suffix.lower() or ".jpg"
    copied_name = f"{employee.employee_id}_{uuid4().hex}{file_ext}"
    target_path = template_dir / copied_name
    copy2(source_path, target_path)

    deactivate_employee_face_profiles(employee.employee_id)
    template_record = EmployeeFaceProfile(
        employee_id=employee.employee_id,
        source_type=EmployeeFaceProfile.SOURCE_ID_PHOTO_COPY,
        source_image_path=f"uploads/face_templates/{copied_name}",
        is_active=True,
    )
    db.session.add(template_record)
    db.session.commit()
    return template_record


def create_manual_face_template(employee, photo):
    if photo is None or not photo.filename:
        raise ValueError("请上传人脸模板图片。")
    if not allowed_image_file(photo.filename):
        raise ValueError("人脸模板仅支持 jpg、jpeg、png、webp 格式。")
    if get_uploaded_file_size(photo) > current_app.config["FACE_TEMPLATE_MAX_SIZE"]:
        raise ValueError("人脸模板图片不能超过 2MB。")

    deactivate_employee_face_profiles(employee.employee_id)
    template_record = EmployeeFaceProfile(
        employee_id=employee.employee_id,
        source_type=EmployeeFaceProfile.SOURCE_MANUAL_FACE_REGISTER,
        source_image_path=save_face_template_image(photo, employee.employee_id),
        is_active=True,
    )
    db.session.add(template_record)
    db.session.commit()
    return template_record


def get_active_face_profiles_for_recognition():
    return (
        EmployeeFaceProfile.query.join(Employee)
        .filter(
            EmployeeFaceProfile.is_active.is_(True),
            Employee.status == Employee.STATUS_ACTIVE,
        )
        .order_by(EmployeeFaceProfile.updated_at.desc(), EmployeeFaceProfile.id.desc())
        .all()
    )


def decode_face_capture_image(image_data):
    if not image_data:
        raise ValueError("请先拍照后再提交识别。")

    if "," in image_data:
        header, encoded_data = image_data.split(",", 1)
        if not header.startswith("data:image/"):
            raise ValueError("仅支持提交图片数据。")
    else:
        encoded_data = image_data

    try:
        image_bytes = base64.b64decode(encoded_data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("图片数据格式不正确，请重新拍照。") from exc

    if not image_bytes:
        raise ValueError("图片数据为空，请重新拍照。")
    if len(image_bytes) > current_app.config["FACE_CAPTURE_MAX_SIZE"]:
        raise ValueError("拍照图片不能超过 2MB，请降低画面尺寸后重试。")
    return image_bytes


def load_pillow_image(image_bytes):
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError as exc:
        raise RuntimeError("缺少 Pillow 依赖，请先安装 requirements.txt 中的依赖。") from exc

    try:
        image = Image.open(BytesIO(image_bytes))
        image.verify()
        image = Image.open(BytesIO(image_bytes)).convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError("无法读取图片内容，请提交有效的人脸图片。") from exc

    return image


def compute_face_image_signature(image_bytes):
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("缺少 OpenCV 依赖，请先安装 requirements.txt 中的依赖。") from exc

    image_array = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("无法读取图片内容，请提交有效的人脸图片。")

    gray_image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    cascade_path = str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")
    face_detector = cv2.CascadeClassifier(cascade_path)
    if face_detector.empty():
        raise RuntimeError("OpenCV 人脸检测模型加载失败。")

    faces = face_detector.detectMultiScale(
        gray_image,
        scaleFactor=1.08,
        minNeighbors=5,
        minSize=(80, 80),
    )
    if len(faces) == 0:
        raise ValueError("未检测到清晰人脸，请正对摄像头并保持光线充足。")

    x, y, width, height = max(faces, key=lambda item: item[2] * item[3])
    face_gray = gray_image[y : y + height, x : x + width]
    face_gray = cv2.equalizeHist(face_gray)
    resized_face = cv2.resize(face_gray, (65, 64), interpolation=cv2.INTER_AREA)
    diff_bits = resized_face[:, 1:] > resized_face[:, :-1]
    hash_value = "".join("1" if bit else "0" for bit in diff_bits.flatten())
    compact_face = cv2.resize(face_gray, (16, 16), interpolation=cv2.INTER_AREA)
    compact_values = [int(value) for value in compact_face.flatten()]

    return {
        "algorithm": "opencv_haar_face_signature_v1",
        "hash": hash_value,
        "face": compact_values,
        "face_box": [int(x), int(y), int(width), int(height)],
    }


def get_face_profile_image_path(profile):
    relative_path = str(profile.source_image_path or "").replace("\\", "/")
    if not relative_path.startswith("uploads/"):
        return None

    upload_root = Path(current_app.config["UPLOAD_FOLDER"]).resolve()
    candidate_path = (upload_root / relative_path[len("uploads/") :]).resolve()
    if upload_root not in candidate_path.parents and candidate_path != upload_root:
        return None
    return candidate_path


def get_face_profile_signature(profile):
    if profile.face_encoding:
        try:
            signature = json.loads(profile.face_encoding)
        except json.JSONDecodeError:
            signature = None
        else:
            if signature.get("algorithm") == "opencv_haar_face_signature_v1":
                return signature

    image_path = get_face_profile_image_path(profile)
    if image_path is None or not image_path.exists():
        return None

    try:
        signature = compute_face_image_signature(image_path.read_bytes())
    except ValueError:
        return None
    profile.face_encoding = json.dumps(signature)
    db.session.commit()
    return signature


def calculate_face_signature_distance(left_signature, right_signature):
    left_hash = left_signature.get("hash", "")
    right_hash = right_signature.get("hash", "")
    if len(left_hash) != len(right_hash):
        return None

    hash_distance = sum(
        1 for left_bit, right_bit in zip(left_hash, right_hash) if left_bit != right_bit
    )
    hash_distance_percent = (hash_distance / len(left_hash)) * 100
    left_face = left_signature.get("face", [])
    right_face = right_signature.get("face", [])
    if len(left_face) != len(right_face) or not left_face:
        return round(hash_distance_percent, 2)

    pixel_distance_percent = (
        sum(
        abs(left_value - right_value)
            for left_value, right_value in zip(left_face, right_face)
        )
        / len(left_face)
        / 255
        * 100
    )
    return round((hash_distance_percent * 0.7) + (pixel_distance_percent * 0.3), 2)


def recognize_employee_from_face_image(image_bytes):
    capture_signature = compute_face_image_signature(image_bytes)
    active_profiles = get_active_face_profiles_for_recognition()
    if not active_profiles:
        raise ValueError("当前没有可用于刷脸打卡的启用模板，请联系管理员维护人脸模板。")

    best_match = None
    for profile in active_profiles:
        template_signature = get_face_profile_signature(profile)
        if template_signature is None:
            continue

        distance = calculate_face_signature_distance(
            capture_signature,
            template_signature,
        )
        if distance is None:
            continue

        if best_match is None or distance < best_match["distance"]:
            best_match = {
                "profile": profile,
                "distance": distance,
            }

    if best_match is None:
        raise ValueError("没有可用的人脸模板文件，请联系管理员检查模板图片。")

    max_distance = current_app.config["FACE_RECOGNITION_MAX_DISTANCE"]
    if best_match["distance"] > max_distance:
        raise ValueError("人脸识别未匹配成功，请调整光线和角度后重试。")

    employee = best_match["profile"].employee
    if employee is None or employee.status != Employee.STATUS_ACTIVE:
        raise ValueError("识别到的员工状态不可用，无法刷脸打卡。")

    return employee, best_match


def get_current_employee():
    return Employee.query.filter_by(
        employee_id=session.get("employee_id")
    ).first()


@main_bp.route("/")
def index():
    return render_template("face_checkin.html")


@main_bp.route("/face-checkin")
def face_checkin():
    return render_template("face_checkin.html")


@main_bp.route("/api/face-checkin/recognize", methods=["POST"])
def api_face_checkin_recognize():
    payload = request.get_json(silent=True) or {}
    image_data = str(payload.get("image_data", "")).strip()

    try:
        image_bytes = decode_face_capture_image(image_data)
        employee, match = recognize_employee_from_face_image(image_bytes)
        attendance, message = create_attendance_record(employee)
    except RuntimeError as exc:
        return api_response(False, str(exc), status=500)
    except ValueError as exc:
        return api_response(False, str(exc), status=400)

    return api_response(
        True,
        "刷脸识别成功，已完成打卡。",
        serialize_face_checkin_result(employee, attendance, message, match),
    )


@main_bp.route("/uploads/<path:filename>")
def uploaded_file(filename):
    upload_dir = Path(current_app.config["UPLOAD_FOLDER"])
    return send_from_directory(upload_dir, filename)


@main_bp.route("/api/login", methods=["POST"])
def api_login():
    payload = get_request_payload()
    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", "")).strip()

    role, user, error_message = authenticate_credentials(username, password)
    if error_message:
        return api_response(False, error_message, status=400)

    if role == "admin":
        set_admin_session(user)
        return api_response(
            True,
            "登录成功。",
            {
                "role": "admin",
                "username": user.username,
            },
        )

    set_employee_session(user)
    return api_response(
        True,
        "登录成功。",
        {
            "role": "employee",
            "employee": serialize_employee(user),
        },
    )


@main_bp.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return api_response(True, "已退出登录。")


@main_bp.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_role") == "admin":
        return redirect(url_for("main.admin"))
    if session.get("user_role") == "employee":
        return redirect(url_for("main.checkin"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        if not username or not password:
            flash("用户名和密码不能为空。", "danger")
            return render_template("login.html")

        admin = Admin.query.filter_by(username=username).first()
        if admin and admin.password == password:
            session.clear()
            session["user_role"] = "admin"
            session["username"] = admin.username
            flash("管理员登录成功。", "success")
            return redirect(url_for("main.admin"))

        employee = Employee.query.filter_by(employee_id=username).first()
        if employee and employee.password == password:
            if employee.status != Employee.STATUS_ACTIVE:
                flash("该员工已离职，无法登录。", "danger")
                return render_template("login.html")

            session.clear()
            session["user_role"] = "employee"
            session["employee_id"] = employee.employee_id
            session["employee_name"] = employee.name
            flash("员工登录成功。员工打卡请使用首页刷脸打卡。", "success")
            return redirect(url_for("main.checkin"))

        flash("用户名或密码错误。", "danger")

    return render_template("login.html")


@main_bp.route("/checkin", methods=["GET", "POST"])
@login_required(role="employee")
def checkin():
    employee = get_current_employee()
    if employee is None or employee.status != Employee.STATUS_ACTIVE:
        session.clear()
        flash("当前员工状态不可用，请重新登录。", "warning")
        return redirect(url_for("main.login"))

    show_leave_modal = False

    if request.method == "POST":
        action = request.form.get("action", "checkin")

        if action == "leave_request":
            show_leave_modal = True
            start_time_raw = request.form.get("start_time", "").strip()
            end_time_raw = request.form.get("end_time", "").strip()
            reason = request.form.get("reason", "").strip()

            if not start_time_raw or not end_time_raw or not reason:
                flash("请假开始时间、结束时间和理由不能为空。", "danger")
            else:
                try:
                    start_time = datetime.strptime(start_time_raw, "%Y-%m-%dT%H:%M")
                    end_time = datetime.strptime(end_time_raw, "%Y-%m-%dT%H:%M")
                except ValueError:
                    flash("请填写有效的请假时间。", "danger")
                else:
                    if start_time >= end_time:
                        flash("请假开始时间必须早于结束时间。", "danger")
                    else:
                        leave_request = LeaveRequest(
                            employee_id=employee.employee_id,
                            start_time=start_time,
                            end_time=end_time,
                            reason=reason,
                            status=LeaveRequest.STATUS_PENDING,
                        )
                        db.session.add(leave_request)
                        db.session.commit()
                        flash("请假申请提交成功，等待审批。", "success")
                        return redirect(url_for("main.checkin"))

        else:
            flash("账号手动打卡已停用，请返回首页使用刷脸打卡。", "warning")
            return redirect(url_for("main.checkin"))

    recent_attendances = (
        Attendance.query.filter_by(employee_id=employee.employee_id)
        .order_by(Attendance.timestamp.desc())
        .limit(5)
        .all()
    )
    recent_leave_requests = (
        order_leave_created_desc(
            LeaveRequest.query.filter_by(employee_id=employee.employee_id)
        )
        .limit(3)
        .all()
    )
    today_summary = get_daily_attendance_summary(employee.employee_id)
    return render_template(
        "checkin.html",
        employee_nav_items=EMPLOYEE_NAV_ITEMS,
        active_endpoint="main.checkin",
        employee=employee,
        recent_attendances=recent_attendances,
        recent_leave_requests=recent_leave_requests,
        today_summary=today_summary,
        show_leave_modal=show_leave_modal,
    )


@main_bp.route("/api/checkin", methods=["POST"])
@api_login_required(role="employee")
def api_checkin():
    return api_response(
        False,
        "账号手动打卡已停用，请使用首页刷脸打卡。",
        status=403,
    )


@main_bp.route("/api/checkin/history", methods=["GET"])
@api_login_required(role="employee")
def api_checkin_history():
    employee = get_current_employee()
    if employee is None or employee.status != Employee.STATUS_ACTIVE:
        session.clear()
        return api_response(False, "当前员工状态不可用，请重新登录。", status=403)

    limit = request.args.get("limit", "10").strip()
    limit_value = int(limit) if limit.isdigit() else 10
    history = (
        Attendance.query.filter_by(employee_id=employee.employee_id)
        .order_by(Attendance.timestamp.desc())
        .limit(limit_value)
        .all()
    )
    return api_response(
        True,
        "获取打卡记录成功。",
        {
            "items": [serialize_attendance(item) for item in history],
            "today_summary": get_daily_attendance_summary(employee.employee_id),
        },
    )


@main_bp.route("/api/leave", methods=["POST"])
@api_login_required(role="employee")
def api_leave_create():
    employee = get_current_employee()
    if employee is None or employee.status != Employee.STATUS_ACTIVE:
        session.clear()
        return api_response(False, "当前员工状态不可用，请重新登录。", status=403)

    payload = get_request_payload()
    start_time_raw = str(payload.get("start_time", "")).strip()
    end_time_raw = str(payload.get("end_time", "")).strip()
    reason = str(payload.get("reason", "")).strip()

    if not start_time_raw or not end_time_raw or not reason:
        return api_response(False, "请假开始时间、结束时间和理由不能为空。", status=400)

    try:
        start_time = datetime.strptime(start_time_raw, "%Y-%m-%dT%H:%M")
        end_time = datetime.strptime(end_time_raw, "%Y-%m-%dT%H:%M")
    except ValueError:
        return api_response(False, "请填写有效的请假时间。", status=400)

    if start_time >= end_time:
        return api_response(False, "请假开始时间必须早于结束时间。", status=400)

    leave_request = LeaveRequest(
        employee_id=employee.employee_id,
        start_time=start_time,
        end_time=end_time,
        reason=reason,
        status=LeaveRequest.STATUS_PENDING,
    )
    db.session.add(leave_request)
    db.session.commit()
    return api_response(True, "请假申请提交成功。", serialize_leave_request(leave_request))


@main_bp.route("/api/leave/my", methods=["GET"])
@api_login_required(role="employee")
def api_leave_my():
    employee = get_current_employee()
    if employee is None or employee.status != Employee.STATUS_ACTIVE:
        session.clear()
        return api_response(False, "当前员工状态不可用，请重新登录。", status=403)

    status_filter = request.args.get("status", "").strip()
    start_date_from = request.args.get("start_date_from", "").strip()
    end_date_to = request.args.get("end_date_to", "").strip()

    query = LeaveRequest.query.filter_by(employee_id=employee.employee_id)
    if status_filter:
        query = query.filter(LeaveRequest.status == status_filter)
    if start_date_from:
        try:
            start_value = datetime.strptime(start_date_from, "%Y-%m-%d")
        except ValueError:
            return api_response(False, "开始时间筛选格式无效。", status=400)
        query = query.filter(LeaveRequest.start_time >= start_value)
    if end_date_to:
        try:
            end_value = datetime.strptime(end_date_to, "%Y-%m-%d")
        except ValueError:
            return api_response(False, "结束时间筛选格式无效。", status=400)
        query = query.filter(
            LeaveRequest.end_time <= end_value.replace(hour=23, minute=59, second=59)
        )

    items = order_leave_created_desc(query).all()
    return api_response(
        True,
        "获取请假记录成功。",
        {"items": [serialize_leave_request(item) for item in items]},
    )


@main_bp.route("/api/leave/detail/<int:leave_id>", methods=["GET"])
@api_login_required(role="employee")
def api_leave_detail(leave_id):
    employee = get_current_employee()
    if employee is None or employee.status != Employee.STATUS_ACTIVE:
        session.clear()
        return api_response(False, "当前员工状态不可用，请重新登录。", status=403)

    leave_request = LeaveRequest.query.filter_by(
        id=leave_id,
        employee_id=employee.employee_id,
    ).first()
    if leave_request is None:
        return api_response(False, "未找到对应的请假记录。", status=404)

    return api_response(
        True,
        "获取请假详情成功。",
        serialize_leave_request(leave_request),
    )


@main_bp.route("/employee/leaves")
@login_required(role="employee")
def employee_leave_history():
    employee = get_current_employee()
    if employee is None or employee.status != Employee.STATUS_ACTIVE:
        session.clear()
        flash("当前员工状态不可用，请重新登录。", "warning")
        return redirect(url_for("main.login"))

    return redirect(url_for("main.employee_leave_list"))


@main_bp.route("/employee/leave/list")
@login_required(role="employee")
def employee_leave_list():
    employee = get_current_employee()
    if employee is None or employee.status != Employee.STATUS_ACTIVE:
        session.clear()
        flash("当前员工状态不可用，请重新登录。", "warning")
        return redirect(url_for("main.login"))

    filters = {
        "status": request.args.get("status", "").strip(),
        "start_date_from": request.args.get("start_date_from", "").strip(),
        "end_date_to": request.args.get("end_date_to", "").strip(),
    }

    query = LeaveRequest.query.filter_by(employee_id=employee.employee_id)
    if filters["status"]:
        query = query.filter(LeaveRequest.status == filters["status"])
    if filters["start_date_from"]:
        try:
            start_date_from = datetime.strptime(
                filters["start_date_from"], "%Y-%m-%d"
            )
        except ValueError:
            flash("开始时间筛选格式无效。", "danger")
            return redirect(url_for("main.employee_leave_list"))
        query = query.filter(LeaveRequest.start_time >= start_date_from)
    if filters["end_date_to"]:
        try:
            end_date_to = datetime.strptime(filters["end_date_to"], "%Y-%m-%d")
        except ValueError:
            flash("结束时间筛选格式无效。", "danger")
            return redirect(url_for("main.employee_leave_list"))
        query = query.filter(
            LeaveRequest.end_time <= end_date_to.replace(hour=23, minute=59, second=59)
        )

    leave_requests = order_leave_created_desc(query).all()

    return render_template(
        "employee_leave_list.html",
        employee_nav_items=EMPLOYEE_NAV_ITEMS,
        active_endpoint="main.employee_leave_history",
        leave_requests=leave_requests,
        filters=filters,
        status_options=[
            LeaveRequest.STATUS_PENDING,
            LeaveRequest.STATUS_APPROVED,
            LeaveRequest.STATUS_REJECTED,
        ],
    )


@main_bp.route("/employee/leave/<int:leave_id>")
@login_required(role="employee")
def employee_leave_detail(leave_id):
    employee = get_current_employee()
    if employee is None or employee.status != Employee.STATUS_ACTIVE:
        session.clear()
        flash("当前员工状态不可用，请重新登录。", "warning")
        return redirect(url_for("main.login"))

    leave_request = LeaveRequest.query.filter_by(
        id=leave_id,
        employee_id=employee.employee_id,
    ).first()
    if leave_request is None:
        flash("未找到对应的请假记录。", "danger")
        return redirect(url_for("main.employee_leave_list"))

    return render_template(
        "employee_leave_detail.html",
        employee_nav_items=EMPLOYEE_NAV_ITEMS,
        active_endpoint="main.employee_leave_history",
        leave_request=leave_request,
    )


@main_bp.route("/employee/profile")
@login_required(role="employee")
def employee_profile():
    employee = get_current_employee()
    if employee is None or employee.status != Employee.STATUS_ACTIVE:
        session.clear()
        flash("当前员工状态不可用，请重新登录。", "warning")
        return redirect(url_for("main.login"))

    return render_template(
        "employee_profile.html",
        employee_nav_items=EMPLOYEE_NAV_ITEMS,
        active_endpoint="main.employee_profile",
        employee=employee,
        certificate_photo_url=build_upload_url(employee.photo_path),
        avatar_url=build_upload_url(employee.avatar_path),
    )


@main_bp.route("/employee/profile/avatar", methods=["POST"])
@login_required(role="employee")
def employee_avatar_upload():
    employee = get_current_employee()
    if employee is None or employee.status != Employee.STATUS_ACTIVE:
        session.clear()
        flash("当前员工状态不可用，请重新登录。", "warning")
        return redirect(url_for("main.login"))

    avatar = request.files.get("avatar")
    if avatar is None or not avatar.filename:
        flash("请选择要上传的头像图片。", "danger")
        return redirect(url_for("main.employee_profile"))

    if not allowed_image_file(avatar.filename):
        flash("头像仅支持 jpg、jpeg、png、webp 格式。", "danger")
        return redirect(url_for("main.employee_profile"))

    file_size = get_uploaded_file_size(avatar)
    if file_size > current_app.config["AVATAR_MAX_SIZE"]:
        flash("头像图片大小不能超过 2MB。", "danger")
        return redirect(url_for("main.employee_profile"))

    employee.avatar_path = save_employee_avatar(avatar, employee.employee_id)
    db.session.commit()
    flash("个人头像上传成功。", "success")
    return redirect(url_for("main.employee_profile"))


@main_bp.route("/api/employee/profile", methods=["GET"])
@api_login_required(role="employee")
def api_employee_profile():
    employee = get_current_employee()
    if employee is None or employee.status != Employee.STATUS_ACTIVE:
        session.clear()
        return api_response(False, "当前员工状态不可用，请重新登录。", status=403)

    return api_response(True, "获取个人资料成功。", serialize_employee(employee))


@main_bp.route("/employee/profile/edit", methods=["GET", "POST"])
@login_required(role="employee")
def employee_profile_edit():
    employee = get_current_employee()
    if employee is None or employee.status != Employee.STATUS_ACTIVE:
        session.clear()
        flash("当前员工状态不可用，请重新登录。", "warning")
        return redirect(url_for("main.login"))

    if request.method == "POST":
        contact = request.form.get("contact", "").strip()

        if not contact:
            flash("联系方式不能为空。", "danger")
        else:
            employee.contact = contact
            db.session.commit()
            flash("联系方式修改成功。", "success")
            return redirect(url_for("main.employee_profile"))

    return render_template(
        "employee_profile_edit.html",
        employee_nav_items=EMPLOYEE_NAV_ITEMS,
        active_endpoint="main.employee_profile",
        employee=employee,
    )


@main_bp.route("/api/employee/profile", methods=["PUT", "POST"])
@api_login_required(role="employee")
def api_employee_profile_update():
    employee = get_current_employee()
    if employee is None or employee.status != Employee.STATUS_ACTIVE:
        session.clear()
        return api_response(False, "当前员工状态不可用，请重新登录。", status=403)

    payload = get_request_payload()
    contact = str(payload.get("contact", "")).strip()
    if not contact:
        return api_response(False, "联系方式不能为空。", status=400)

    employee.contact = contact
    db.session.commit()
    return api_response(True, "个人资料更新成功。", serialize_employee(employee))


@main_bp.route("/employee/password")
@login_required(role="employee")
def employee_change_password():
    return redirect(url_for("main.employee_account_password"))


@main_bp.route("/employee/account/password", methods=["GET", "POST"])
@login_required(role="employee")
def employee_account_password():
    employee = get_current_employee()
    if employee is None or employee.status != Employee.STATUS_ACTIVE:
        session.clear()
        flash("当前员工状态不可用，请重新登录。", "warning")
        return redirect(url_for("main.login"))

    if request.method == "POST":
        old_password = request.form.get("old_password", "").strip()
        new_password = request.form.get("new_password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()

        if not old_password or not new_password or not confirm_password:
            flash("原密码、新密码和确认新密码不能为空。", "danger")
        elif employee.password != old_password:
            flash("原密码错误，无法修改。", "danger")
        elif new_password != confirm_password:
            flash("新密码和确认新密码不一致。", "danger")
        else:
            employee.password = new_password
            db.session.commit()
            flash("密码修改成功，请使用新密码登录。", "success")
            return redirect(url_for("main.employee_account_password"))

    return render_template(
        "employee_account_password.html",
        employee_nav_items=EMPLOYEE_NAV_ITEMS,
        active_endpoint="main.employee_change_password",
    )


@main_bp.route("/api/employee/password", methods=["PUT", "POST"])
@api_login_required(role="employee")
def api_employee_password():
    employee = get_current_employee()
    if employee is None or employee.status != Employee.STATUS_ACTIVE:
        session.clear()
        return api_response(False, "当前员工状态不可用，请重新登录。", status=403)

    payload = get_request_payload()
    old_password = str(payload.get("old_password", "")).strip()
    new_password = str(payload.get("new_password", "")).strip()
    confirm_password = str(payload.get("confirm_password", "")).strip()

    if not old_password or not new_password or not confirm_password:
        return api_response(False, "原密码、新密码和确认新密码不能为空。", status=400)
    if employee.password != old_password:
        return api_response(False, "原密码错误，无法修改。", status=400)
    if new_password != confirm_password:
        return api_response(False, "新密码和确认新密码不一致。", status=400)

    employee.password = new_password
    db.session.commit()
    return api_response(True, "员工密码修改成功。")


@main_bp.route("/api/employee/avatar", methods=["POST"])
@api_login_required(role="employee")
def api_employee_avatar():
    employee = get_current_employee()
    if employee is None or employee.status != Employee.STATUS_ACTIVE:
        session.clear()
        return api_response(False, "当前员工状态不可用，请重新登录。", status=403)

    avatar = request.files.get("avatar")
    if avatar is None or not avatar.filename:
        return api_response(False, "请选择要上传的头像图片。", status=400)
    if not allowed_image_file(avatar.filename):
        return api_response(False, "头像仅支持 jpg、jpeg、png、webp 格式。", status=400)

    file_size = get_uploaded_file_size(avatar)
    if file_size > current_app.config["AVATAR_MAX_SIZE"]:
        return api_response(False, "头像图片大小不能超过 2MB。", status=400)

    employee.avatar_path = save_employee_avatar(avatar, employee.employee_id)
    db.session.commit()
    return api_response(
        True,
        "个人头像上传成功。",
        {
            "avatar_path": employee.avatar_path,
            "avatar_url": build_upload_url(employee.avatar_path),
        },
    )


@main_bp.route("/api/employee/leave/list", methods=["GET"])
@api_login_required(role="employee")
def api_employee_leave_list_alias():
    return api_leave_my()


@main_bp.route("/admin")
@login_required(role="admin")
def admin():
    total_employees = Employee.query.count()
    active_employees = Employee.query.filter_by(status=Employee.STATUS_ACTIVE).count()
    pending_leaves = LeaveRequest.query.filter_by(
        status=LeaveRequest.STATUS_PENDING,
    ).count()
    active_face_templates = EmployeeFaceProfile.query.filter_by(is_active=True).count()
    recent_pending_leaves = (
        order_leave_created_desc(
            LeaveRequest.query.filter_by(status=LeaveRequest.STATUS_PENDING)
        )
        .limit(5)
        .all()
    )
    return render_template(
        "admin.html",
        admin_nav_items=ADMIN_NAV_ITEMS,
        active_endpoint=request.endpoint,
        dashboard_stats={
            "total_employees": total_employees,
            "active_employees": active_employees,
            "pending_leaves": pending_leaves,
            "active_face_templates": active_face_templates,
        },
        recent_pending_leaves=recent_pending_leaves,
    )


@main_bp.route("/admin/employee-entry")
@login_required(role="admin")
def admin_employee_entry():
    return redirect(url_for("main.admin_employee_create"))


@main_bp.route("/admin/employee/create", methods=["GET", "POST"])
@login_required(role="admin")
def admin_employee_create():
    success_info = session.pop("created_employee_info", None)
    try:
        next_employee_id = get_next_employee_id()
    except ValueError as exc:
        flash(str(exc), "danger")
        next_employee_id = ""

    if request.method == "POST":
        employee_id = next_employee_id
        photo = request.files.get("photo")
        name = request.form.get("name", "").strip()
        birth_date_raw = request.form.get("birth_date", "").strip()
        position = request.form.get("position", "").strip()
        contact = request.form.get("contact", "").strip()
        hire_date_raw = request.form.get("hire_date", "").strip()

        if not all([name, birth_date_raw, position, contact, hire_date_raw]):
            flash("请完整填写所有必填项。", "danger")
        elif photo is None or not photo.filename:
            flash("请上传证件照。", "danger")
        elif not allowed_image_file(photo.filename):
            flash("证件照仅支持 jpg、jpeg、png、webp 格式。", "danger")
        else:
            try:
                birth_date = parse_birth_date(birth_date_raw)
                hire_date = datetime.strptime(hire_date_raw, "%Y-%m-%d").date()
            except ValueError:
                flash("请填写有效的出生日期和入职时间。", "danger")
            else:
                if Employee.query.filter_by(employee_id=employee_id).first():
                    flash("系统生成的工号已存在，请重试。", "danger")
                else:
                    password = hire_date.strftime("%Y%m%d")
                    employee = Employee(
                        employee_id=employee_id,
                        name=name,
                        photo_path=save_employee_photo(photo, employee_id),
                        birth_date=birth_date,
                        position=position,
                        contact=contact,
                        hire_date=hire_date,
                        password=password,
                        status=Employee.STATUS_ACTIVE,
                    )
                    db.session.add(employee)
                    db.session.commit()

                    session["created_employee_info"] = {
                        "employee_id": employee.employee_id,
                        "name": employee.name,
                        "password": password,
                    }
                    flash("员工创建成功，账号信息已生成。", "success")
                    return redirect(url_for("main.admin_employee_create"))

    return render_template(
        "admin_employee_create.html",
        admin_nav_items=ADMIN_NAV_ITEMS,
        active_endpoint="main.admin_employee_entry",
        next_employee_id=next_employee_id,
        success_info=success_info,
        form_data=request.form,
    )


@main_bp.route("/admin/behavior-long-term")
@login_required(role="admin")
def admin_behavior_long_term():
    return redirect(url_for("main.admin_analysis_long"))


@main_bp.route("/admin/analysis/long")
@login_required(role="admin")
def admin_analysis_long():
    filters = {
        "employee_id": request.args.get("employee_id", "").strip(),
        "position": request.args.get("position", "").strip(),
        "hire_date": request.args.get("hire_date", "").strip(),
        "sort": request.args.get("sort", "created_desc").strip(),
    }
    selected_analysis_id = request.args.get("analysis_id", "").strip()

    employee_query = Employee.query
    analysis_query = Analysis.query.join(Employee).filter(Analysis.type == "long")

    if filters["employee_id"]:
        employee_query = employee_query.filter(
            Employee.employee_id.contains(filters["employee_id"])
        )
        analysis_query = analysis_query.filter(
            Employee.employee_id.contains(filters["employee_id"])
        )
    if filters["position"]:
        employee_query = employee_query.filter(Employee.position.contains(filters["position"]))
        analysis_query = analysis_query.filter(Employee.position.contains(filters["position"]))
    if filters["hire_date"]:
        try:
            hire_date_value = datetime.strptime(filters["hire_date"], "%Y-%m-%d").date()
        except ValueError:
            flash("入职时间筛选格式无效。", "danger")
            return redirect(url_for("main.admin_analysis_long"))
        employee_query = employee_query.filter(Employee.hire_date == hire_date_value)
        analysis_query = analysis_query.filter(Employee.hire_date == hire_date_value)

    sort_mapping = {
        "created_desc": Analysis.created_at.desc(),
        "created_asc": Analysis.created_at.asc(),
        "employee_asc": Analysis.employee_id.asc(),
        "employee_desc": Analysis.employee_id.desc(),
        "name_asc": Employee.name.asc(),
        "name_desc": Employee.name.desc(),
    }
    sort_order = sort_mapping.get(filters["sort"], Analysis.created_at.desc())

    employees = employee_query.order_by(Employee.employee_id.asc()).all()
    analyses = analysis_query.order_by(sort_order).all()

    selected_analysis = None
    if selected_analysis_id:
        selected_analysis = Analysis.query.filter_by(
            id=selected_analysis_id,
            type="long",
        ).first()
    if selected_analysis is None and analyses:
        selected_analysis = analyses[0]

    chart_data = None
    if selected_analysis is not None:
        try:
            chart_data = build_long_chart_data(selected_analysis.employee_id)
        except AnalysisServiceError:
            chart_data = None

    return render_template(
        "admin_analysis_long.html",
        admin_nav_items=ADMIN_NAV_ITEMS,
        active_endpoint="main.admin_behavior_long_term",
        filters=filters,
        employees=employees,
        analyses=analyses,
        selected_analysis=selected_analysis,
        chart_data=chart_data,
        sort_options=[
            ("created_desc", "报告时间：最新优先"),
            ("created_asc", "报告时间：最早优先"),
            ("employee_asc", "工号：升序"),
            ("employee_desc", "工号：降序"),
            ("name_asc", "姓名：升序"),
            ("name_desc", "姓名：降序"),
        ],
    )


@main_bp.route("/admin/analysis/long/generate", methods=["POST"])
@login_required(role="admin")
def admin_analysis_long_generate():
    employee_id = request.form.get("employee_id", "").strip()
    try:
        analysis = generate_long_analysis_for_employee(employee_id)
    except AnalysisServiceError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("main.admin_analysis_long"))

    flash(f"已为员工 {analysis.employee_id} 生成一条长期分析结果。", "success")
    return redirect(url_for("main.admin_analysis_long", analysis_id=analysis.id))


@main_bp.route("/api/analysis/long", methods=["POST"])
@api_login_required(role="admin")
def api_analysis_long():
    payload = get_request_payload()
    employee_id = str(payload.get("employee_id", "")).strip()
    if not employee_id:
        return api_response(False, "员工工号不能为空。", status=400)

    try:
        analysis = generate_long_analysis_for_employee(employee_id)
    except AnalysisServiceError as exc:
        return api_response(False, str(exc), status=400)

    return api_response(True, "长期分析生成成功。", serialize_analysis(analysis))


@main_bp.route("/admin/analysis/<int:analysis_id>")
@login_required(role="admin")
def admin_analysis_detail(analysis_id):
    analysis = Analysis.query.filter_by(id=analysis_id).first_or_404()
    return render_template(
        "admin_analysis_detail.html",
        admin_nav_items=ADMIN_NAV_ITEMS,
        active_endpoint=(
            "main.admin_behavior_long_term"
            if analysis.type == "long"
            else "main.admin_behavior_short_term"
        ),
        analysis=analysis,
    )


@main_bp.route("/admin/behavior-short-term")
@login_required(role="admin")
def admin_behavior_short_term():
    return redirect(url_for("main.admin_analysis_short"))


@main_bp.route("/admin/analysis/short")
@login_required(role="admin")
def admin_analysis_short():
    filters = {
        "employee_id": request.args.get("employee_id", "").strip(),
        "name": request.args.get("name", "").strip(),
    }
    selected_analysis_id = request.args.get("analysis_id", "").strip()

    employee_query = Employee.query
    analysis_query = Analysis.query.join(Employee).filter(Analysis.type == "short")

    if filters["employee_id"]:
        employee_query = employee_query.filter(
            Employee.employee_id.contains(filters["employee_id"])
        )
        analysis_query = analysis_query.filter(
            Employee.employee_id.contains(filters["employee_id"])
        )
    if filters["name"]:
        employee_query = employee_query.filter(Employee.name.contains(filters["name"]))
        analysis_query = analysis_query.filter(Employee.name.contains(filters["name"]))

    employees = employee_query.order_by(Employee.employee_id.asc()).all()
    employee_cards = [
        {
            "employee": employee,
            "recent_day_count": get_recent_attendance_day_count(employee.employee_id),
            "can_analyze": get_recent_attendance_day_count(employee.employee_id) >= 7,
        }
        for employee in employees
    ]

    analyses = analysis_query.order_by(Analysis.created_at.desc()).all()

    selected_analysis = None
    if selected_analysis_id:
        selected_analysis = Analysis.query.filter_by(
            id=selected_analysis_id,
            type="short",
        ).first()
    if selected_analysis is None and analyses:
        selected_analysis = analyses[0]

    chart_data = None
    if selected_analysis is not None:
        try:
            chart_data = build_short_chart_data(selected_analysis.employee_id)
        except AnalysisServiceError:
            chart_data = None

    return render_template(
        "admin_analysis_short.html",
        admin_nav_items=ADMIN_NAV_ITEMS,
        active_endpoint="main.admin_behavior_short_term",
        filters=filters,
        employee_cards=employee_cards,
        analyses=analyses,
        selected_analysis=selected_analysis,
        chart_data=chart_data,
    )


@main_bp.route("/admin/analysis/short/generate", methods=["POST"])
@login_required(role="admin")
def admin_analysis_short_generate():
    employee_id = request.form.get("employee_id", "").strip()
    try:
        analysis = generate_short_analysis_for_employee(employee_id)
    except AnalysisServiceError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("main.admin_analysis_short"))

    flash(f"已为员工 {analysis.employee_id} 生成一条短期分析结果。", "success")
    return redirect(url_for("main.admin_analysis_short", analysis_id=analysis.id))


@main_bp.route("/api/analysis/short", methods=["POST"])
@api_login_required(role="admin")
def api_analysis_short():
    payload = get_request_payload()
    employee_id = str(payload.get("employee_id", "")).strip()
    if not employee_id:
        return api_response(False, "员工工号不能为空。", status=400)

    try:
        analysis = generate_short_analysis_for_employee(employee_id)
    except AnalysisServiceError as exc:
        return api_response(False, str(exc), status=400)

    return api_response(True, "短期分析生成成功。", serialize_analysis(analysis))


@main_bp.route("/api/analysis/list", methods=["GET"])
@api_login_required(role="admin")
def api_analysis_list():
    analysis_type = request.args.get("type", "").strip()
    employee_id = request.args.get("employee_id", "").strip()

    query = Analysis.query
    if analysis_type:
        query = query.filter(Analysis.type == analysis_type)
    if employee_id:
        query = query.filter(Analysis.employee_id.contains(employee_id))

    items = query.order_by(Analysis.created_at.desc()).all()
    return api_response(
        True,
        "获取分析记录成功。",
        {"items": [serialize_analysis(item) for item in items]},
    )


@main_bp.route("/api/analysis/detail/<int:analysis_id>", methods=["GET"])
@api_login_required(role="admin")
def api_analysis_detail(analysis_id):
    analysis = Analysis.query.filter_by(id=analysis_id).first()
    if analysis is None:
        return api_response(False, "分析记录不存在。", status=404)

    return api_response(True, "获取分析详情成功。", serialize_analysis(analysis))


@main_bp.route("/admin/employees")
@login_required(role="admin")
def admin_employee_list():
    return redirect(url_for("main.admin_employee_list_page"))


@main_bp.route("/admin/employee/list")
@login_required(role="admin")
def admin_employee_list_page():
    filters = {
        "employee_id": request.args.get("employee_id", "").strip(),
        "name": request.args.get("name", "").strip(),
        "position": request.args.get("position", "").strip(),
        "status": request.args.get("status", "").strip(),
    }

    query = Employee.query
    if filters["employee_id"]:
        query = query.filter(Employee.employee_id.contains(filters["employee_id"]))
    if filters["name"]:
        query = query.filter(Employee.name.contains(filters["name"]))
    if filters["position"]:
        query = query.filter(Employee.position.contains(filters["position"]))
    if filters["status"]:
        query = query.filter(Employee.status == filters["status"])

    employees = query.order_by(Employee.employee_id.asc()).all()
    active_face_profiles = {
        item.employee_id: item
        for item in EmployeeFaceProfile.query.filter_by(is_active=True).all()
    }
    face_profile_counts = {}
    for profile in EmployeeFaceProfile.query.all():
        face_profile_counts[profile.employee_id] = (
            face_profile_counts.get(profile.employee_id, 0) + 1
        )

    return render_template(
        "admin_employee_list.html",
        admin_nav_items=ADMIN_NAV_ITEMS,
        active_endpoint="main.admin_employee_list_page",
        employees=employees,
        active_face_profiles=active_face_profiles,
        face_profile_counts=face_profile_counts,
        filters=filters,
        status_options=[Employee.STATUS_ACTIVE, Employee.STATUS_INACTIVE],
        face_source_labels={
            EmployeeFaceProfile.SOURCE_ID_PHOTO_COPY: get_face_profile_source_label(
                EmployeeFaceProfile.SOURCE_ID_PHOTO_COPY
            ),
            EmployeeFaceProfile.SOURCE_MANUAL_FACE_REGISTER: get_face_profile_source_label(
                EmployeeFaceProfile.SOURCE_MANUAL_FACE_REGISTER
            ),
            EmployeeFaceProfile.SOURCE_CAMERA_CAPTURE: get_face_profile_source_label(
                EmployeeFaceProfile.SOURCE_CAMERA_CAPTURE
            ),
        },
    )


@main_bp.route("/api/employee/create", methods=["POST"])
@api_login_required(role="admin")
def api_employee_create():
    try:
        next_employee_id = get_next_employee_id()
    except ValueError as exc:
        return api_response(False, str(exc), status=400)
    photo = request.files.get("photo")
    payload = get_request_payload()
    name = str(payload.get("name", "")).strip()
    birth_date_raw = str(payload.get("birth_date", "")).strip()
    position = str(payload.get("position", "")).strip()
    contact = str(payload.get("contact", "")).strip()
    hire_date_raw = str(payload.get("hire_date", "")).strip()

    if not all([name, birth_date_raw, position, contact, hire_date_raw]):
        return api_response(False, "请完整填写所有必填项。", status=400)
    if photo is None or not photo.filename:
        return api_response(False, "请上传证件照。", status=400)
    if not allowed_image_file(photo.filename):
        return api_response(False, "证件照仅支持 jpg、jpeg、png、webp 格式。", status=400)

    try:
        birth_date = parse_birth_date(birth_date_raw)
        hire_date = datetime.strptime(hire_date_raw, "%Y-%m-%d").date()
    except ValueError:
        return api_response(False, "请填写有效的出生日期和入职时间。", status=400)

    if Employee.query.filter_by(employee_id=next_employee_id).first():
        return api_response(False, "系统生成的工号已存在，请重试。", status=400)

    password = hire_date.strftime("%Y%m%d")
    employee = Employee(
        employee_id=next_employee_id,
        name=name,
        photo_path=save_employee_photo(photo, next_employee_id),
        birth_date=birth_date,
        position=position,
        contact=contact,
        hire_date=hire_date,
        password=password,
        status=Employee.STATUS_ACTIVE,
    )
    db.session.add(employee)
    db.session.commit()
    return api_response(
        True,
        "员工创建成功。",
        {
            "employee": {
                "employee_id": employee.employee_id,
                "name": employee.name,
                "login_account": employee.employee_id,
            },
            "initial_password": password,
        },
        status=201,
    )


@main_bp.route("/api/employee/list", methods=["GET"])
@api_login_required(role="admin")
def api_employee_list():
    filters = {
        "employee_id": request.args.get("employee_id", "").strip(),
        "name": request.args.get("name", "").strip(),
        "position": request.args.get("position", "").strip(),
        "status": request.args.get("status", "").strip(),
    }

    query = Employee.query
    if filters["employee_id"]:
        query = query.filter(Employee.employee_id.contains(filters["employee_id"]))
    if filters["name"]:
        query = query.filter(Employee.name.contains(filters["name"]))
    if filters["position"]:
        query = query.filter(Employee.position.contains(filters["position"]))
    if filters["status"]:
        query = query.filter(Employee.status == filters["status"])

    employees = query.order_by(Employee.employee_id.asc()).all()
    return api_response(
        True,
        "获取员工列表成功。",
        {"items": [serialize_employee(item) for item in employees]},
    )


@main_bp.route("/api/face-profile/active-candidates", methods=["GET"])
@api_login_required(role="admin")
def api_face_profile_active_candidates():
    profiles = get_active_face_profiles_for_recognition()
    return api_response(
        True,
        "获取当前可用于后续刷脸打卡的启用模板成功。",
        {"items": [serialize_face_profile(item) for item in profiles]},
    )


@main_bp.route("/api/employee/update", methods=["PUT", "POST"])
@api_login_required(role="admin")
def api_employee_update():
    payload = get_request_payload()
    employee_id = str(payload.get("employee_id", "")).strip()
    if not employee_id:
        return api_response(False, "员工工号不能为空。", status=400)
    requested_employee_id = str(payload.get("new_employee_id", "")).strip()
    if requested_employee_id and requested_employee_id != employee_id:
        return api_response(False, "员工工号不允许修改。", status=400)

    employee = Employee.query.filter_by(employee_id=employee_id).first()
    if employee is None:
        return api_response(False, "员工不存在。", status=404)

    password = payload.get("password")
    position = payload.get("position")
    contact = payload.get("contact")
    photo = request.files.get("photo")

    if password is not None:
        password = str(password).strip()
        if not password:
            return api_response(False, "密码不能为空。", status=400)
        employee.password = password
    if position is not None:
        position = str(position).strip()
        if not position:
            return api_response(False, "岗位不能为空。", status=400)
        employee.position = position
    if contact is not None:
        contact = str(contact).strip()
        if not contact:
            return api_response(False, "联系方式不能为空。", status=400)
        employee.contact = contact
    if photo and photo.filename:
        if not allowed_image_file(photo.filename):
            return api_response(False, "证件照格式不正确。", status=400)
        employee.photo_path = save_employee_photo(photo, employee.employee_id)

    db.session.commit()
    return api_response(True, "员工信息更新成功。", serialize_employee(employee))


@main_bp.route("/api/employee/status", methods=["PUT", "POST"])
@api_login_required(role="admin")
def api_employee_status():
    payload = get_request_payload()
    employee_id = str(payload.get("employee_id", "")).strip()
    status_value = str(payload.get("status", "")).strip()

    if not employee_id or status_value not in {
        Employee.STATUS_ACTIVE,
        Employee.STATUS_INACTIVE,
    }:
        return api_response(False, "员工工号或状态参数无效。", status=400)

    employee = Employee.query.filter_by(employee_id=employee_id).first()
    if employee is None:
        return api_response(False, "员工不存在。", status=404)

    employee.status = status_value
    db.session.commit()
    return api_response(True, "员工状态更新成功。", serialize_employee(employee))


@main_bp.route("/admin/employee/<employee_id>/update", methods=["POST"])
@login_required(role="admin")
def admin_employee_update(employee_id):
    employee = Employee.query.filter_by(employee_id=employee_id).first_or_404()
    submitted_employee_id = request.form.get("employee_id", "").strip()

    if submitted_employee_id and submitted_employee_id != employee_id:
        flash("员工工号不允许修改。", "danger")
        return redirect(url_for("main.admin_employee_list_page"))

    password = request.form.get("password", "").strip()
    position = request.form.get("position", "").strip()
    contact = request.form.get("contact", "").strip()
    photo = request.files.get("photo")

    if not password or not position or not contact:
        flash(f"员工 {employee.employee_id} 的密码、岗位和联系方式不能为空。", "danger")
        return redirect(url_for("main.admin_employee_list_page"))

    employee.password = password
    employee.position = position
    employee.contact = contact

    if photo and photo.filename:
        if not allowed_image_file(photo.filename):
            flash(f"员工 {employee.employee_id} 的证件照格式不正确。", "danger")
            return redirect(url_for("main.admin_employee_list_page"))
        employee.photo_path = save_employee_photo(photo, employee.employee_id)

    db.session.commit()
    flash(f"员工 {employee.employee_id} 信息已更新。", "success")
    return redirect(url_for("main.admin_employee_list_page"))


@main_bp.route("/admin/employee/<employee_id>/face-template/create", methods=["POST"])
@login_required(role="admin")
def admin_employee_face_template_create(employee_id):
    employee = Employee.query.filter_by(employee_id=employee_id).first_or_404()

    try:
        copy_id_photo_to_face_template(employee)
    except ValueError as exc:
        flash(str(exc), "warning")
    else:
        flash(f"已根据员工 {employee.employee_id} 的证件照生成初始人脸模板。", "success")

    return redirect(url_for("main.admin_employee_face_profile", employee_id=employee.employee_id))


@main_bp.route("/admin/face-profile/list")
@login_required(role="admin")
def admin_face_profile_list():
    employees = Employee.query.order_by(Employee.employee_id.asc()).all()
    active_face_profiles = {
        item.employee_id: item
        for item in EmployeeFaceProfile.query.filter_by(is_active=True).all()
    }
    face_profile_counts = {}
    for profile in EmployeeFaceProfile.query.all():
        face_profile_counts[profile.employee_id] = (
            face_profile_counts.get(profile.employee_id, 0) + 1
        )

    return render_template(
        "admin_face_profile_list.html",
        admin_nav_items=ADMIN_NAV_ITEMS,
        active_endpoint="main.admin_face_profile_list",
        employees=employees,
        active_face_profiles=active_face_profiles,
        face_profile_counts=face_profile_counts,
        source_label=get_face_profile_source_label,
    )


@main_bp.route("/admin/employee/<employee_id>/face-profile")
@login_required(role="admin")
def admin_employee_face_profile(employee_id):
    employee = Employee.query.filter_by(employee_id=employee_id).first_or_404()
    face_profiles = (
        EmployeeFaceProfile.query.filter_by(employee_id=employee.employee_id)
        .order_by(EmployeeFaceProfile.is_active.desc(), EmployeeFaceProfile.updated_at.desc())
        .all()
    )
    active_face_profile = next(
        (profile for profile in face_profiles if profile.is_active),
        None,
    )

    return render_template(
        "admin_employee_face_profile.html",
        admin_nav_items=ADMIN_NAV_ITEMS,
        active_endpoint="main.admin_face_profile_list",
        employee=employee,
        face_profiles=face_profiles,
        active_face_profile=active_face_profile,
        source_label=get_face_profile_source_label,
        face_template_max_size=current_app.config["FACE_TEMPLATE_MAX_SIZE"],
    )


@main_bp.route("/admin/employee/<employee_id>/face-profile/upload", methods=["POST"])
@login_required(role="admin")
def admin_employee_face_profile_upload(employee_id):
    employee = Employee.query.filter_by(employee_id=employee_id).first_or_404()
    photo = request.files.get("face_template")

    try:
        create_manual_face_template(employee, photo)
    except ValueError as exc:
        flash(str(exc), "danger")
    else:
        flash(f"已为员工 {employee.employee_id} 上传并启用新的人脸模板。", "success")

    return redirect(url_for("main.admin_employee_face_profile", employee_id=employee.employee_id))


@main_bp.route("/admin/face-profile/<int:profile_id>/activate", methods=["POST"])
@login_required(role="admin")
def admin_face_profile_activate(profile_id):
    profile = EmployeeFaceProfile.query.filter_by(id=profile_id).first_or_404()
    deactivate_employee_face_profiles(profile.employee_id, except_profile_id=profile.id)
    profile.is_active = True
    db.session.commit()
    flash("已将该模板设为当前启用模板。", "success")
    return redirect(url_for("main.admin_employee_face_profile", employee_id=profile.employee_id))


@main_bp.route("/admin/face-profile/<int:profile_id>/deactivate", methods=["POST"])
@login_required(role="admin")
def admin_face_profile_deactivate(profile_id):
    profile = EmployeeFaceProfile.query.filter_by(id=profile_id).first_or_404()
    profile.is_active = False
    db.session.commit()
    flash("已停用该人脸模板。", "warning")
    return redirect(url_for("main.admin_employee_face_profile", employee_id=profile.employee_id))


@main_bp.route("/admin/employee/<employee_id>/face-profile/camera-placeholder", methods=["POST"])
@login_required(role="admin")
def admin_employee_face_profile_camera_placeholder(employee_id):
    employee = Employee.query.filter_by(employee_id=employee_id).first_or_404()
    flash("摄像头采集接口已预留，本轮暂不接入真实采集流程。", "info")
    return redirect(url_for("main.admin_employee_face_profile", employee_id=employee.employee_id))


@main_bp.route("/admin/employee/<employee_id>/deactivate", methods=["POST"])
@login_required(role="admin")
def admin_employee_deactivate(employee_id):
    employee = Employee.query.filter_by(employee_id=employee_id).first_or_404()
    employee.status = Employee.STATUS_INACTIVE
    db.session.commit()
    flash(f"员工 {employee.employee_id} 已标记为离职。", "warning")
    return redirect(url_for("main.admin_employee_list_page"))


@main_bp.route("/admin/attendance/overview")
@login_required(role="admin")
def admin_attendance_overview():
    today = datetime.now().date()
    default_start_date = today.replace(day=1)
    filters = {
        "employee_id": request.args.get("employee_id", "").strip(),
        "name": request.args.get("name", "").strip(),
        "position": request.args.get("position", "").strip(),
        "start_date": request.args.get("start_date", default_start_date.isoformat()).strip(),
        "end_date": request.args.get("end_date", today.isoformat()).strip(),
    }

    try:
        start_date = datetime.strptime(filters["start_date"], "%Y-%m-%d").date()
        end_date = datetime.strptime(filters["end_date"], "%Y-%m-%d").date()
    except ValueError:
        flash("日期范围格式无效，请使用有效的开始日期和结束日期。", "danger")
        return redirect(url_for("main.admin_attendance_overview"))

    if start_date > end_date:
        flash("开始日期不能晚于结束日期。", "danger")
        return redirect(url_for("main.admin_attendance_overview"))

    if (end_date - start_date).days > 92:
        flash("单次最多查看 93 天出勤情况，请缩小日期范围。", "warning")
        return redirect(url_for("main.admin_attendance_overview"))

    query = Employee.query
    if filters["employee_id"]:
        query = query.filter(Employee.employee_id.contains(filters["employee_id"]))
    if filters["name"]:
        query = query.filter(Employee.name.contains(filters["name"]))
    if filters["position"]:
        query = query.filter(Employee.position.contains(filters["position"]))

    employees = query.order_by(Employee.employee_id.asc()).all()
    overview_rows, totals = build_attendance_overview(employees, start_date, end_date)

    return render_template(
        "admin_attendance_overview.html",
        admin_nav_items=ADMIN_NAV_ITEMS,
        active_endpoint="main.admin_attendance_overview",
        filters=filters,
        start_date=start_date,
        end_date=end_date,
        overview_rows=overview_rows,
        totals=totals,
        status_labels=[
            Attendance.STATUS_NORMAL_CHECKIN,
            Attendance.STATUS_LATE,
            Attendance.STATUS_EARLY_LEAVE,
            Attendance.STATUS_NORMAL_CHECKOUT,
            Attendance.STATUS_ABSENT,
            "请假",
        ],
    )


@main_bp.route("/admin/leave/list")
@login_required(role="admin")
def admin_leave_list():
    filters = {
        "employee_id": request.args.get("employee_id", "").strip(),
        "name": request.args.get("name", "").strip(),
        "status": request.args.get("status", "").strip(),
        "start_date_from": request.args.get("start_date_from", "").strip(),
        "end_date_to": request.args.get("end_date_to", "").strip(),
    }

    query = LeaveRequest.query.join(Employee)
    if filters["employee_id"]:
        query = query.filter(Employee.employee_id.contains(filters["employee_id"]))
    if filters["name"]:
        query = query.filter(Employee.name.contains(filters["name"]))
    if filters["status"]:
        query = query.filter(LeaveRequest.status == filters["status"])
    if filters["start_date_from"]:
        try:
            start_date_from = datetime.strptime(
                filters["start_date_from"], "%Y-%m-%d"
            )
        except ValueError:
            flash("开始时间筛选格式无效。", "danger")
            return redirect(url_for("main.admin_leave_list"))
        query = query.filter(LeaveRequest.start_time >= start_date_from)
    if filters["end_date_to"]:
        try:
            end_date_to = datetime.strptime(filters["end_date_to"], "%Y-%m-%d")
        except ValueError:
            flash("结束时间筛选格式无效。", "danger")
            return redirect(url_for("main.admin_leave_list"))
        query = query.filter(LeaveRequest.end_time <= end_date_to.replace(hour=23, minute=59, second=59))

    leave_requests = order_leave_created_desc(query).all()

    return render_template(
        "admin_leave_list.html",
        admin_nav_items=ADMIN_NAV_ITEMS,
        active_endpoint="main.admin_leave_list",
        leave_requests=leave_requests,
        filters=filters,
        status_options=[
            LeaveRequest.STATUS_PENDING,
            LeaveRequest.STATUS_APPROVED,
            LeaveRequest.STATUS_REJECTED,
        ],
    )


@main_bp.route("/api/admin/leave/list", methods=["GET"])
@api_login_required(role="admin")
def api_admin_leave_list():
    filters = {
        "employee_id": request.args.get("employee_id", "").strip(),
        "name": request.args.get("name", "").strip(),
        "status": request.args.get("status", "").strip(),
        "start_date_from": request.args.get("start_date_from", "").strip(),
        "end_date_to": request.args.get("end_date_to", "").strip(),
    }

    query = LeaveRequest.query.join(Employee)
    if filters["employee_id"]:
        query = query.filter(Employee.employee_id.contains(filters["employee_id"]))
    if filters["name"]:
        query = query.filter(Employee.name.contains(filters["name"]))
    if filters["status"]:
        query = query.filter(LeaveRequest.status == filters["status"])
    if filters["start_date_from"]:
        try:
            start_date_from = datetime.strptime(filters["start_date_from"], "%Y-%m-%d")
        except ValueError:
            return api_response(False, "开始时间筛选格式无效。", status=400)
        query = query.filter(LeaveRequest.start_time >= start_date_from)
    if filters["end_date_to"]:
        try:
            end_date_to = datetime.strptime(filters["end_date_to"], "%Y-%m-%d")
        except ValueError:
            return api_response(False, "结束时间筛选格式无效。", status=400)
        query = query.filter(
            LeaveRequest.end_time <= end_date_to.replace(hour=23, minute=59, second=59)
        )

    items = order_leave_created_desc(query).all()
    return api_response(
        True,
        "获取请假审核列表成功。",
        {"items": [serialize_leave_request(item, include_employee=True) for item in items]},
    )


@main_bp.route("/admin/leave/<int:leave_id>")
@login_required(role="admin")
def admin_leave_detail(leave_id):
    leave_request = LeaveRequest.query.filter_by(id=leave_id).first_or_404()
    return render_template(
        "admin_leave_detail.html",
        admin_nav_items=ADMIN_NAV_ITEMS,
        active_endpoint="main.admin_leave_list",
        leave_request=leave_request,
    )


@main_bp.route("/admin/leave/review", methods=["POST"])
@login_required(role="admin")
def admin_leave_review():
    leave_id = request.form.get("leave_id", "").strip()
    action = request.form.get("action", "").strip()

    if action not in {"approve", "reject"}:
        flash("审批动作无效。", "danger")
        return redirect(url_for("main.admin_leave_list"))

    if not leave_id.isdigit():
        flash("请假记录编号无效。", "danger")
        return redirect(url_for("main.admin_leave_list"))

    leave_request = LeaveRequest.query.filter_by(id=int(leave_id)).first()
    if leave_request is None:
        flash("请假记录不存在。", "danger")
        return redirect(url_for("main.admin_leave_list"))

    if leave_request.status != LeaveRequest.STATUS_PENDING:
        flash("该请假记录已审批，不能重复操作。", "warning")
        return redirect(url_for("main.admin_leave_list"))

    leave_request.status = (
        LeaveRequest.STATUS_APPROVED
        if action == "approve"
        else LeaveRequest.STATUS_REJECTED
    )
    leave_request.reviewed_at = datetime.utcnow()
    leave_request.reviewed_by = session.get("username")
    db.session.commit()

    flash(
        f"请假记录 #{leave_request.id} 已{'通过' if action == 'approve' else '拒绝'}。",
        "success",
    )
    return redirect(url_for("main.admin_leave_list"))


@main_bp.route("/api/admin/leave/review", methods=["POST"])
@api_login_required(role="admin")
def api_admin_leave_review():
    payload = get_request_payload()
    leave_id = str(payload.get("leave_id", "")).strip()
    action = str(payload.get("action", "")).strip()

    if action not in {"approve", "reject"}:
        return api_response(False, "审批动作无效。", status=400)
    if not leave_id.isdigit():
        return api_response(False, "请假记录编号无效。", status=400)

    leave_request = LeaveRequest.query.filter_by(id=int(leave_id)).first()
    if leave_request is None:
        return api_response(False, "请假记录不存在。", status=404)
    if leave_request.status != LeaveRequest.STATUS_PENDING:
        return api_response(False, "该请假记录已审批，不能重复操作。", status=400)

    leave_request.status = (
        LeaveRequest.STATUS_APPROVED
        if action == "approve"
        else LeaveRequest.STATUS_REJECTED
    )
    leave_request.reviewed_at = datetime.utcnow()
    leave_request.reviewed_by = session.get("username")
    db.session.commit()
    return api_response(True, "请假审核处理成功。", serialize_leave_request(leave_request, include_employee=True))


@main_bp.route("/admin/account", methods=["GET", "POST"])
@login_required(role="admin")
def admin_account_page():
    admin = Admin.query.filter_by(username=session.get("username")).first_or_404()

    if request.method == "POST":
        old_password = request.form.get("old_password", "").strip()
        new_password = request.form.get("new_password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()

        if not old_password or not new_password or not confirm_password:
            flash("原密码、新密码和确认新密码不能为空。", "danger")
        elif admin.password != old_password:
            flash("原密码错误，无法修改。", "danger")
        elif new_password != confirm_password:
            flash("新密码和确认新密码不一致。", "danger")
        else:
            admin.password = new_password
            db.session.commit()
            flash("密码修改成功，下次登录请使用新密码。", "success")
            return redirect(url_for("main.admin_account_page"))

    return render_template(
        "admin_account.html",
        admin_nav_items=ADMIN_NAV_ITEMS,
        active_endpoint="main.admin_account_page",
        admin=admin,
    )


@main_bp.route("/api/admin/account/password", methods=["PUT", "POST"])
@api_login_required(role="admin")
def api_admin_account_password():
    payload = get_request_payload()
    admin = Admin.query.filter_by(username=session.get("username")).first()
    if admin is None:
        return api_response(False, "管理员账号不存在。", status=404)

    old_password = str(payload.get("old_password", "")).strip()
    new_password = str(payload.get("new_password", "")).strip()
    confirm_password = str(payload.get("confirm_password", "")).strip()

    if not old_password or not new_password or not confirm_password:
        return api_response(False, "原密码、新密码和确认新密码不能为空。", status=400)
    if admin.password != old_password:
        return api_response(False, "原密码错误，无法修改。", status=400)
    if new_password != confirm_password:
        return api_response(False, "新密码和确认新密码不一致。", status=400)

    admin.password = new_password
    db.session.commit()
    return api_response(True, "管理员密码修改成功。")


@main_bp.route("/admin/accounts")
@login_required(role="admin")
def admin_account_manage():
    return redirect(url_for("main.admin_account_page"))


@main_bp.route("/logout")
def logout():
    session.clear()
    flash("您已退出登录。", "info")
    return redirect(url_for("main.login"))
