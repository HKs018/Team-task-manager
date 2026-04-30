from __future__ import annotations

import base64
import hashlib
import os
import secrets
import sqlite3
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
DB_PATH = Path(os.getenv("DATABASE_PATH", PROJECT_DIR / "team_task_manager.db"))
SESSION_DAYS = 7
STATUSES = {"todo", "in_progress", "done"}


class SignupPayload(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    email: str = Field(min_length=5, max_length=120)
    password: str = Field(min_length=6, max_length=128)


class LoginPayload(BaseModel):
    email: str = Field(min_length=5, max_length=120)
    password: str = Field(min_length=6, max_length=128)


class RolePayload(BaseModel):
    role: Literal["admin", "member"]


class ProjectPayload(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=800)


class MemberPayload(BaseModel):
    user_id: int


class TaskPayload(BaseModel):
    title: str = Field(min_length=2, max_length=160)
    description: str = Field(default="", max_length=1000)
    assigned_to: Optional[int] = None
    status: Literal["todo", "in_progress", "done"] = "todo"
    due_date: Optional[date] = None


class TaskUpdatePayload(BaseModel):
    title: Optional[str] = Field(default=None, min_length=2, max_length=160)
    description: Optional[str] = Field(default=None, max_length=1000)
    assigned_to: Optional[int] = None
    status: Optional[Literal["todo", "in_progress", "done"]] = None
    due_date: Optional[date] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Team Task Manager", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def unix_now() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def model_dump(model: BaseModel, **kwargs):
    if hasattr(model, "model_dump"):
        return model.model_dump(**kwargs)
    return model.dict(**kwargs)


def connect_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def get_db():
    db = connect_db()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    db = connect_db()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('admin', 'member')),
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            owner_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS project_members (
            project_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (project_id, user_id),
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            assigned_to INTEGER,
            status TEXT NOT NULL CHECK (status IN ('todo', 'in_progress', 'done')),
            due_date TEXT,
            created_by INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY (assigned_to) REFERENCES users(id) ON DELETE SET NULL,
            FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE CASCADE
        );
        """
    )
    db.commit()
    db.close()


def normalize_email(email: str) -> str:
    email = email.strip().lower()
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(status_code=400, detail="Enter a valid email address.")
    return email


def hash_password(password: str) -> str:
    iterations = 160_000
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "$".join(
        [
            "pbkdf2_sha256",
            str(iterations),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        ]
    )


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations, salt_b64, digest_b64 = stored_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, int(iterations)
        )
        return secrets.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def public_user(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "email": row["email"],
        "role": row["role"],
        "created_at": row["created_at"],
    }


def create_session(db: sqlite3.Connection, user_id: int, response: Response) -> None:
    token = secrets.token_urlsafe(32)
    expires_at = unix_now() + int(timedelta(days=SESSION_DAYS).total_seconds())
    db.execute(
        "INSERT INTO sessions (token, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
        (token, user_id, expires_at, now_iso()),
    )
    response.set_cookie(
        key="session_token",
        value=token,
        httponly=True,
        samesite="lax",
        secure=os.getenv("COOKIE_SECURE", "false").lower() == "true",
        max_age=SESSION_DAYS * 24 * 60 * 60,
    )


def get_current_user(
    request: Request, db: sqlite3.Connection = Depends(get_db)
) -> dict:
    token = request.cookies.get("session_token")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Login required.")
    row = db.execute(
        """
        SELECT users.*
        FROM sessions
        JOIN users ON users.id = sessions.user_id
        WHERE sessions.token = ? AND sessions.expires_at > ?
        """,
        (token, unix_now()),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired.")
    return public_user(row)


def require_admin(user: dict) -> None:
    if user["role"] != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")


def ensure_user_exists(db: sqlite3.Connection, user_id: int) -> sqlite3.Row:
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    return user


def ensure_project_exists(db: sqlite3.Connection, project_id: int) -> sqlite3.Row:
    project = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found.")
    return project


def ensure_project_access(
    db: sqlite3.Connection, project_id: int, user: dict
) -> sqlite3.Row:
    project = ensure_project_exists(db, project_id)
    return project


def task_response(row: sqlite3.Row) -> dict:
    task = {
        "id": row["id"],
        "project_id": row["project_id"],
        "title": row["title"],
        "description": row["description"],
        "assigned_to": row["assigned_to"],
        "assigned_name": row["assigned_name"],
        "status": row["status"],
        "due_date": row["due_date"],
        "created_by": row["created_by"],
        "created_by_name": row["created_by_name"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if "project_name" in row.keys():
        task["project_name"] = row["project_name"]
    return task


def fetch_task(db: sqlite3.Connection, task_id: int) -> sqlite3.Row:
    task = db.execute(
        """
        SELECT tasks.*, assignee.name AS assigned_name, creator.name AS created_by_name
        FROM tasks
        LEFT JOIN users assignee ON assignee.id = tasks.assigned_to
        JOIN users creator ON creator.id = tasks.created_by
        WHERE tasks.id = ?
        """,
        (task_id,),
    ).fetchone()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    return task


def add_project_member(db: sqlite3.Connection, project_id: int, user_id: int) -> None:
    ensure_user_exists(db, user_id)
    db.execute(
        """
        INSERT OR IGNORE INTO project_members (project_id, user_id, created_at)
        VALUES (?, ?, ?)
        """,
        (project_id, user_id, now_iso()),
    )


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.post("/api/auth/signup")
def signup(
    payload: SignupPayload, response: Response, db: sqlite3.Connection = Depends(get_db)
):
    email = normalize_email(payload.email)
    user_count = db.execute("SELECT COUNT(*) AS count FROM users").fetchone()["count"]
    role = "admin" if user_count == 0 else "member"
    try:
        cursor = db.execute(
            """
            INSERT INTO users (name, email, password_hash, role, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                payload.name.strip(),
                email,
                hash_password(payload.password),
                role,
                now_iso(),
            ),
        )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="That email is already registered.")
    create_session(db, cursor.lastrowid, response)
    db.commit()
    user = db.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return {"user": public_user(user)}


@app.post("/api/auth/login")
def login(
    payload: LoginPayload, response: Response, db: sqlite3.Connection = Depends(get_db)
):
    email = normalize_email(payload.email)
    user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    db.execute("DELETE FROM sessions WHERE expires_at <= ?", (unix_now(),))
    create_session(db, user["id"], response)
    db.commit()
    return {"user": public_user(user)}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response, db: sqlite3.Connection = Depends(get_db)):
    token = request.cookies.get("session_token")
    if token:
        db.execute("DELETE FROM sessions WHERE token = ?", (token,))
        db.commit()
    response.delete_cookie("session_token")
    return {"message": "Logged out"}


@app.get("/api/me")
def me(user: dict = Depends(get_current_user)):
    return {"user": user}


@app.get("/api/users")
def list_users(
    user: dict = Depends(get_current_user), db: sqlite3.Connection = Depends(get_db)
):
    rows = db.execute("SELECT * FROM users ORDER BY name COLLATE NOCASE").fetchall()
    return {"users": [public_user(row) for row in rows]}


@app.patch("/api/users/{user_id}/role")
def update_role(
    user_id: int,
    payload: RolePayload,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    require_admin(user)
    ensure_user_exists(db, user_id)
    if user_id == user["id"] and payload.role != "admin":
        raise HTTPException(status_code=400, detail="You cannot remove your own admin role.")
    db.execute("UPDATE users SET role = ? WHERE id = ?", (payload.role, user_id))
    db.commit()
    updated = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return {"user": public_user(updated)}


@app.get("/api/projects")
def list_projects(
    user: dict = Depends(get_current_user), db: sqlite3.Connection = Depends(get_db)
):
    rows = db.execute(
        """
        SELECT
            p.*,
            owner.name AS owner_name,
            (SELECT COUNT(*) FROM project_members pm WHERE pm.project_id = p.id) AS member_count,
            (SELECT COUNT(*) FROM tasks t WHERE t.project_id = p.id) AS task_count,
            (SELECT COUNT(*) FROM tasks t WHERE t.project_id = p.id AND t.status = 'done') AS done_count
        FROM projects p
        JOIN users owner ON owner.id = p.owner_id
        ORDER BY p.created_at DESC
        """,
    ).fetchall()
    return {"projects": [dict(row) for row in rows]}


@app.post("/api/projects", status_code=201)
def create_project(
    payload: ProjectPayload,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    require_admin(user)
    cursor = db.execute(
        """
        INSERT INTO projects (name, description, owner_id, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (payload.name.strip(), payload.description.strip(), user["id"], now_iso()),
    )
    add_project_member(db, cursor.lastrowid, user["id"])
    db.commit()
    return {"project_id": cursor.lastrowid}


@app.get("/api/projects/{project_id}")
def get_project(
    project_id: int,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    ensure_project_access(db, project_id, user)
    row = db.execute(
        """
        SELECT p.*, owner.name AS owner_name
        FROM projects p
        JOIN users owner ON owner.id = p.owner_id
        WHERE p.id = ?
        """,
        (project_id,),
    ).fetchone()
    return {"project": dict(row)}


@app.delete("/api/projects/{project_id}")
def delete_project(
    project_id: int,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    require_admin(user)
    ensure_project_exists(db, project_id)
    db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    db.commit()
    return {"message": "Project deleted"}


@app.get("/api/projects/{project_id}/members")
def list_project_members(
    project_id: int,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    ensure_project_access(db, project_id, user)
    rows = db.execute(
        """
        SELECT users.id, users.name, users.email, users.role, project_members.created_at
        FROM project_members
        JOIN users ON users.id = project_members.user_id
        WHERE project_members.project_id = ?
        ORDER BY users.name COLLATE NOCASE
        """,
        (project_id,),
    ).fetchall()
    return {"members": [dict(row) for row in rows]}


@app.post("/api/projects/{project_id}/members", status_code=201)
def create_project_member(
    project_id: int,
    payload: MemberPayload,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    require_admin(user)
    ensure_project_exists(db, project_id)
    add_project_member(db, project_id, payload.user_id)
    db.commit()
    return {"message": "Member added"}


@app.delete("/api/projects/{project_id}/members/{user_id}")
def delete_project_member(
    project_id: int,
    user_id: int,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    require_admin(user)
    project = ensure_project_exists(db, project_id)
    if project["owner_id"] == user_id:
        raise HTTPException(status_code=400, detail="Project owner cannot be removed.")
    db.execute(
        "DELETE FROM project_members WHERE project_id = ? AND user_id = ?",
        (project_id, user_id),
    )
    db.commit()
    return {"message": "Member removed"}


@app.get("/api/projects/{project_id}/tasks")
def list_project_tasks(
    project_id: int,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    ensure_project_access(db, project_id, user)
    rows = db.execute(
        """
        SELECT tasks.*, assignee.name AS assigned_name, creator.name AS created_by_name
        FROM tasks
        LEFT JOIN users assignee ON assignee.id = tasks.assigned_to
        JOIN users creator ON creator.id = tasks.created_by
        WHERE tasks.project_id = ?
        ORDER BY
            CASE tasks.status
                WHEN 'todo' THEN 1
                WHEN 'in_progress' THEN 2
                ELSE 3
            END,
            COALESCE(tasks.due_date, '9999-12-31'),
            tasks.created_at DESC
        """,
        (project_id,),
    ).fetchall()
    return {"tasks": [task_response(row) for row in rows]}


@app.get("/api/tasks")
def list_tasks(
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    rows = db.execute(
        """
        SELECT
            tasks.*,
            projects.name AS project_name,
            assignee.name AS assigned_name,
            creator.name AS created_by_name
        FROM tasks
        JOIN projects ON projects.id = tasks.project_id
        LEFT JOIN users assignee ON assignee.id = tasks.assigned_to
        JOIN users creator ON creator.id = tasks.created_by
        ORDER BY
            CASE tasks.status
                WHEN 'todo' THEN 1
                WHEN 'in_progress' THEN 2
                ELSE 3
            END,
            COALESCE(tasks.due_date, '9999-12-31'),
            tasks.created_at DESC
        """,
    ).fetchall()
    return {"tasks": [task_response(row) for row in rows]}


@app.post("/api/projects/{project_id}/tasks", status_code=201)
def create_task(
    project_id: int,
    payload: TaskPayload,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    require_admin(user)
    ensure_project_exists(db, project_id)
    if payload.assigned_to is not None:
        add_project_member(db, project_id, payload.assigned_to)
    cursor = db.execute(
        """
        INSERT INTO tasks (
            project_id, title, description, assigned_to, status,
            due_date, created_by, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            project_id,
            payload.title.strip(),
            payload.description.strip(),
            payload.assigned_to,
            payload.status,
            payload.due_date.isoformat() if payload.due_date else None,
            user["id"],
            now_iso(),
            now_iso(),
        ),
    )
    db.commit()
    return {"task_id": cursor.lastrowid}


@app.patch("/api/tasks/{task_id}")
def update_task(
    task_id: int,
    payload: TaskUpdatePayload,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    task = fetch_task(db, task_id)
    ensure_project_access(db, task["project_id"], user)
    updates = model_dump(payload, exclude_unset=True)
    if not updates:
        return {"task": task_response(task)}

    if user["role"] != "admin":
        if task["assigned_to"] != user["id"]:
            raise HTTPException(status_code=403, detail="You can only update tasks assigned to you.")
        if set(updates) != {"status"}:
            raise HTTPException(status_code=403, detail="Members can only update task status.")

    assignments = []
    values = []
    for field, value in updates.items():
        if field == "assigned_to" and value is not None:
            add_project_member(db, task["project_id"], value)
        if field == "due_date" and value is not None:
            value = value.isoformat()
        if isinstance(value, str):
            value = value.strip()
        assignments.append(f"{field} = ?")
        values.append(value)
    assignments.append("updated_at = ?")
    values.append(now_iso())
    values.append(task_id)
    db.execute(
        f"UPDATE tasks SET {', '.join(assignments)} WHERE id = ?",
        tuple(values),
    )
    db.commit()
    return {"task": task_response(fetch_task(db, task_id))}


@app.delete("/api/tasks/{task_id}")
def delete_task(
    task_id: int,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    require_admin(user)
    fetch_task(db, task_id)
    db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    db.commit()
    return {"message": "Task deleted"}


@app.get("/api/dashboard")
def dashboard(
    user: dict = Depends(get_current_user), db: sqlite3.Connection = Depends(get_db)
):
    today = date.today().isoformat()
    project_count = db.execute("SELECT COUNT(*) AS count FROM projects").fetchone()["count"]

    totals = db.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN status = 'todo' THEN 1 ELSE 0 END) AS todo,
            SUM(CASE WHEN status = 'in_progress' THEN 1 ELSE 0 END) AS in_progress,
            SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END) AS done,
            SUM(CASE WHEN due_date IS NOT NULL AND due_date < ? AND status != 'done' THEN 1 ELSE 0 END) AS overdue
        FROM tasks
        """,
        (today,),
    ).fetchone()
    return {
        "stats": {
            "projects": project_count,
            "tasks": totals["total"] or 0,
            "todo": totals["todo"] or 0,
            "in_progress": totals["in_progress"] or 0,
            "done": totals["done"] or 0,
            "overdue": totals["overdue"] or 0,
        }
    }

@app.get("/")
def root():
    return {"message": "App is working"}