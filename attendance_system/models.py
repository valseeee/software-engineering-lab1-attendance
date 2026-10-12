from datetime import datetime

from sqlalchemy import inspect, text
from flask_sqlalchemy import SQLAlchemy


db = SQLAlchemy()


class Admin(db.Model):
    __tablename__ = "admin"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)

    def __repr__(self):
        return f"<Admin {self.username}>"


class Employee(db.Model):
    __tablename__ = "employee"

    STATUS_ACTIVE = "在职"
    STATUS_INACTIVE = "离职"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.String(9), unique=True, nullable=False)
    name = db.Column(db.String(50), nullable=False)
    photo_path = db.Column(db.String(255))
    avatar_path = db.Column(db.String(255))
    birth_date = db.Column(db.Date)
    position = db.Column(db.String(100))
    contact = db.Column(db.String(50))
    hire_date = db.Column(db.Date, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(20), nullable=False, default=STATUS_ACTIVE)

    attendances = db.relationship("Attendance", back_populates="employee", lazy=True)
    leave_requests = db.relationship("LeaveRequest", back_populates="employee", lazy=True)
    analyses = db.relationship("Analysis", back_populates="employee", lazy=True)
    face_profiles = db.relationship("EmployeeFaceProfile", back_populates="employee", lazy=True)

    def __repr__(self):
        return f"<Employee {self.employee_id}>"


class Attendance(db.Model):
    __tablename__ = "attendance"

    TYPE_CLOCK_IN = "clock_in"
    TYPE_CLOCK_OUT = "clock_out"

    STATUS_NORMAL_CHECKIN = "正常出勤"
    STATUS_LATE = "迟到"
    STATUS_EARLY_LEAVE = "早退"
    STATUS_NORMAL_CHECKOUT = "正常下班"
    STATUS_ABSENT = "缺勤"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(
        db.String(9),
        db.ForeignKey("employee.employee_id"),
        nullable=False,
    )
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    type = db.Column(db.String(20), nullable=False)
    attendance_status = db.Column(db.String(20), nullable=True)

    employee = db.relationship("Employee", back_populates="attendances")


class LeaveRequest(db.Model):
    __tablename__ = "leave_request"

    STATUS_PENDING = "待审批"
    STATUS_APPROVED = "通过"
    STATUS_REJECTED = "拒绝"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(
        db.String(9),
        db.ForeignKey("employee.employee_id"),
        nullable=False,
    )
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime, nullable=False)
    reason = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), nullable=False, default=STATUS_PENDING)
    created_at = db.Column(db.DateTime, nullable=True, default=datetime.utcnow)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    reviewed_by = db.Column(db.String(50), nullable=True)

    employee = db.relationship("Employee", back_populates="leave_requests")


class Analysis(db.Model):
    __tablename__ = "analysis"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(
        db.String(9),
        db.ForeignKey("employee.employee_id"),
        nullable=False,
    )
    type = db.Column(db.String(50), nullable=False)
    report_text = db.Column(db.Text)
    personality = db.Column(db.Text)
    motivation = db.Column(db.Text)
    health = db.Column(db.Text)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    employee = db.relationship("Employee", back_populates="analyses")


class EmployeeFaceProfile(db.Model):
    __tablename__ = "employee_face_profile"

    SOURCE_ID_PHOTO_COPY = "id_photo_copy"
    SOURCE_MANUAL_FACE_REGISTER = "manual_face_register"
    SOURCE_CAMERA_CAPTURE = "camera_capture"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(
        db.String(9),
        db.ForeignKey("employee.employee_id"),
        nullable=False,
    )
    source_type = db.Column(db.String(50), nullable=False, default=SOURCE_ID_PHOTO_COPY)
    source_image_path = db.Column(db.String(255), nullable=False)
    face_encoding = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    employee = db.relationship("Employee", back_populates="face_profiles")


def ensure_leave_request_columns():
    inspector = inspect(db.engine)
    if "leave_request" not in inspector.get_table_names():
        return

    existing_columns = {
        column["name"] for column in inspector.get_columns("leave_request")
    }
    alter_statements = []

    if "created_at" not in existing_columns:
        alter_statements.append(
            "ALTER TABLE leave_request ADD COLUMN created_at DATETIME"
        )
    if "reviewed_at" not in existing_columns:
        alter_statements.append(
            "ALTER TABLE leave_request ADD COLUMN reviewed_at DATETIME"
        )
    if "reviewed_by" not in existing_columns:
        alter_statements.append(
            "ALTER TABLE leave_request ADD COLUMN reviewed_by VARCHAR(50)"
        )

    if not alter_statements:
        return

    for statement in alter_statements:
        db.session.execute(text(statement))
    db.session.commit()


def ensure_employee_columns():
    inspector = inspect(db.engine)
    if "employee" not in inspector.get_table_names():
        return

    existing_columns = {
        column["name"] for column in inspector.get_columns("employee")
    }
    alter_statements = []

    if "avatar_path" not in existing_columns:
        alter_statements.append(
            "ALTER TABLE employee ADD COLUMN avatar_path VARCHAR(255)"
        )

    if not alter_statements:
        return

    for statement in alter_statements:
        db.session.execute(text(statement))
    db.session.commit()


def ensure_attendance_columns():
    inspector = inspect(db.engine)
    if "attendance" not in inspector.get_table_names():
        return

    existing_columns = {
        column["name"] for column in inspector.get_columns("attendance")
    }
    alter_statements = []

    if "attendance_status" not in existing_columns:
        alter_statements.append(
            "ALTER TABLE attendance ADD COLUMN attendance_status VARCHAR(20)"
        )

    if not alter_statements:
        return

    for statement in alter_statements:
        db.session.execute(text(statement))
    db.session.commit()


def ensure_core_tables():
    inspector = inspect(db.engine)
    existing_tables = set(inspector.get_table_names())

    for model in [Admin, Employee, Attendance, LeaveRequest, Analysis, EmployeeFaceProfile]:
        if model.__tablename__ not in existing_tables:
            model.__table__.create(bind=db.engine, checkfirst=True)


def init_db():
    ensure_core_tables()
    ensure_employee_columns()
    ensure_attendance_columns()
    ensure_leave_request_columns()

    admin = Admin.query.filter_by(username="root").first()
    if admin is None:
        db.session.add(Admin(username="root", password="123456"))
        db.session.commit()
