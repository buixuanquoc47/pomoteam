import os, datetime, functools
from flask import Flask, request, redirect, url_for, session, render_template, abort, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from jinja2 import DictLoader

# -------------------- Setup --------------------
app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-change-me")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///pomoteam.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# -------------------- Models --------------------
class Team(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), default="Default Team")

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    name = db.Column(db.String(120))
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default="member")  # member | leader
    team_id = db.Column(db.Integer, db.ForeignKey("team.id"))
    team = db.relationship("Team")

class Project(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey("team.id"))
    name = db.Column(db.String(200), nullable=False)

class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("project.id"))
    assignee_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    status = db.Column(db.String(20), default="todo")  # todo | doing | done | blocked
    priority = db.Column(db.String(20), default="normal")
    estimate_pomos = db.Column(db.Integer, default=0)
    actual_pomos = db.Column(db.Integer, default=0)
    due_date = db.Column(db.Date)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    project = db.relationship("Project")
    assignee = db.relationship("User")

class FocusSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    task_id = db.Column(db.Integer, db.ForeignKey("task.id"))
    project_id = db.Column(db.Integer, db.ForeignKey("project.id"))
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime)
    planned_minutes = db.Column(db.Integer, default=25)
    actual_minutes = db.Column(db.Integer, default=0)
    was_completed = db.Column(db.Boolean, default=False)
    notes = db.Column(db.Text)

    user = db.relationship("User")
    task = db.relationship("Task")
    project = db.relationship("Project")

with app.app_context():
    db.create_all()
    if Team.query.count() == 0:
        db.session.add(Team(name="Main Team"))
        db.session.commit()

# -------------------- Helpers --------------------
def current_user():
    uid = session.get("uid")
    return User.query.get(uid) if uid else None

def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped

def leader_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        u = current_user()
        if not u or u.role != "leader":
            abort(403)
        return view(*args, **kwargs)
    return wrapped

# -------------------- Auth --------------------
@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        name = request.form.get("name","").strip()
        password = request.form["password"]
        if User.query.filter_by(email=email).first():
            flash("Email already registered", "danger")
            return redirect(url_for("register"))
        team = Team.query.first()
        role = "leader" if User.query.count() == 0 else "member"
        user = User(email=email, name=name, password_hash=generate_password_hash(password),
                    role=role, team=team)
        db.session.add(user)
        db.session.commit()
        session["uid"] = user.id
        return redirect(url_for("dashboard"))
    return render_template("register.html", APP_NAME="PomoTeam")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        user = User.query.filter_by(email=email).first()
        if not user or not check_password_hash(user.password_hash, password):
            flash("Invalid credentials", "danger")
            return redirect(url_for("login"))
        session["uid"] = user.id
        return redirect(url_for("dashboard"))
    return render_template("login.html", APP_NAME="PomoTeam")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# -------------------- Pages --------------------
@app.route("/")
@login_required
def dashboard():
    me = current_user()
    tasks = Task.query.filter(Task.assignee_id == me.id).order_by(Task.created_at.desc()).all()
    start_of_day = datetime.datetime.combine(datetime.date.today(), datetime.time(0,0,0))
    sessions_today = (FocusSession.query
                      .filter(FocusSession.user_id==me.id, FocusSession.start_time>=start_of_day)
                      .all())
    mins_today = sum(s.actual_minutes or 0 for s in sessions_today)
    pomos_today = sum(1 for s in sessions_today if s.was_completed)
    projects = Project.query.filter(Project.team_id==me.team_id).order_by(Project.name).all()
    return render_template("dash.html", APP_NAME="PomoTeam",
                           me=me, tasks=tasks, mins_today=mins_today,
                           pomos_today=pomos_today, projects=projects)

@app.route("/team")
@login_required
@leader_required
def team_dashboard():
    me = current_user()
    team_users = User.query.filter(User.team_id==me.team_id).all()
    rng = request.args.get("range","week")
    now = datetime.datetime.utcnow()
    if rng == "day":
        start = datetime.datetime.combine(datetime.date.today(), datetime.time(0,0,0))
    elif rng == "month":
        start = (now.replace(day=1, hour=0, minute=0, second=0, microsecond=0))
    else:
        today = datetime.date.today()
        monday = today - datetime.timedelta(days=today.weekday())
        start = datetime.datetime.combine(monday, datetime.time(0,0,0))
    members_stats = []
    for u in team_users:
        ss = FocusSession.query.filter(FocusSession.user_id==u.id, FocusSession.start_time>=start).all()
        mins = sum(s.actual_minutes or 0 for s in ss)
        pomos = sum(1 for s in ss if s.was_completed)
        tasks_done = Task.query.filter(Task.assignee_id==u.id, Task.status=="done").count()
        done_tasks = Task.query.filter(Task.assignee_id==u.id, Task.status=="done").all()
        est = sum(t.estimate_pomos or 0 for t in done_tasks) or 0
        act = sum(t.actual_pomos or 0 for t in done_tasks) or 0
        acc = (act/est*100) if est>0 else 0
        members_stats.append(dict(user=u, focus_minutes=mins, pomos=pomos,
                                  tasks_done=tasks_done, estimate_accuracy=round(acc,1)))
    blocked = (Task.query
               .filter(Task.project_id.in_([p.id for p in Project.query.filter_by(team_id=me.team_id)]),
                       Task.status=="blocked")
               .order_by(Task.due_date.asc().nullslast()).all())
    return render_template("team.html", APP_NAME="PomoTeam",
                           me=me, members_stats=members_stats,
                           blocked=blocked, start=start, rng=rng)

# -------------------- Tasks --------------------
@app.route("/tasks/create", methods=["POST"])
@login_required
def create_task():
    me = current_user()
    title = request.form["title"].strip()
    if not title:
        return redirect(url_for("dashboard"))
    t = Task(
        title=title,
        description=request.form.get("description","").strip() or None,
        assignee_id=int(request.form.get("assignee_id", me.id)),
        project_id=(int(request.form["project_id"]) if request.form.get("project_id") else None),
        estimate_pomos=int(request.form.get("estimate_pomos",0) or 0),
        priority=request.form.get("priority","normal"),
        due_date=(datetime.datetime.strptime(request.form["due_date"], "%Y-%m-%d").date()
                  if request.form.get("due_date") else None)
    )
    db.session.add(t); db.session.commit()
    return redirect(url_for("dashboard"))

@app.route("/tasks/<int:task_id>/update", methods=["POST"])
@login_required
def update_task(task_id):
    me = current_user()
    task = Task.query.get_or_404(task_id)
    if me.role != "leader" and task.assignee_id != me.id:
        abort(403)
    task.title = request.form.get("title", task.title)
    task.description = request.form.get("description", task.description)
    task.status = request.form.get("status", task.status)
    task.priority = request.form.get("priority", task.priority)
    task.estimate_pomos = int(request.form.get("estimate_pomos", task.estimate_pomos) or 0)
    if request.form.get("assignee_id"):
        task.assignee_id = int(request.form["assignee_id"])
    if request.form.get("project_id"):
        task.project_id = int(request.form["project_id"])
    if request.form.get("due_date"):
        task.due_date = datetime.datetime.strptime(request.form["due_date"], "%Y-%m-%d").date()
    db.session.commit()
    return redirect(request.referrer or url_for("dashboard"))

# -------------------- Projects --------------------
@app.route("/projects/create", methods=["POST"])
@login_required
def create_project():
    me = current_user()
    name = request.form["name"].strip()
    if not name: return redirect(url_for("dashboard"))
    p = Project(team_id=me.team_id, name=name)
    db.session.add(p); db.session.commit()
    return redirect(url_for("dashboard"))

# -------------------- Focus Sessions --------------------
@app.route("/sessions/start", methods=["POST"])
@login_required
def start_session():
    me = current_user()
    task_id = int(request.form["task_id"]) if request.form.get("task_id") else None
    planned = int(request.form.get("planned_minutes", 25) or 25)
    project_id = None
    if task_id:
        task = Task.query.get(task_id)
        project_id = task.project_id if task else None
    s = FocusSession(user_id=me.id, task_id=task_id, project_id=project_id,
                     start_time=datetime.datetime.utcnow(), planned_minutes=planned)
    db.session.add(s); db.session.commit()
    return {"session_id": s.id}

@app.route("/sessions/finish", methods=["POST"])
@login_required
def finish_session():
    me = current_user()
    sid = int(request.form["session_id"])
    s = FocusSession.query.get_or_404(sid)
    if s.user_id != me.id: abort(403)
    s.end_time = datetime.datetime.utcnow()
    delta = int((s.end_time - s.start_time).total_seconds() // 60)
    s.actual_minutes = max(delta, 0)
    s.was_completed = request.form.get("completed","true") == "true"
    notes = request.form.get("notes")
    if notes: s.notes = notes
    if s.task_id and s.was_completed and s.actual_minutes >= (s.planned_minutes - 1):
        t = Task.query.get(s.task_id)
        t.actual_pomos = (t.actual_pomos or 0) + 1
    db.session.commit()
    return {"ok": True}

# -------------------- Reports --------------------
@app.route("/reports/me")
@login_required
def my_report():
    me = current_user()
    frm = request.args.get("from")
    to = request.args.get("to")
    if frm and to:
        start = datetime.datetime.fromisoformat(frm)
        end = datetime.datetime.fromisoformat(to)
    else:
        today = datetime.date.today()
        monday = today - datetime.timedelta(days=today.weekday())
        start = datetime.datetime.combine(monday, datetime.time(0,0,0))
        end = datetime.datetime.utcnow()
    sessions = (FocusSession.query
                .filter(FocusSession.user_id==me.id,
                        FocusSession.start_time>=start,
                        FocusSession.start_time<=end)
                .all())
    focus_minutes = sum(s.actual_minutes or 0 for s in sessions)
    pomos = sum(1 for s in sessions if s.was_completed)
    tasks_done = Task.query.filter(Task.assignee_id==me.id, Task.status=="done").count()
    done_tasks = Task.query.filter(Task.assignee_id==me.id, Task.status=="done").all()
    est = sum(t.estimate_pomos or 0 for t in done_tasks) or 0
    act = sum(t.actual_pomos or 0 for t in done_tasks) or 0
    acc = (act/est*100) if est>0 else 0
    return {
        "range": {"from": start.isoformat(), "to": end.isoformat()},
        "focus_minutes": focus_minutes,
        "pomos": pomos,
        "tasks_done": tasks_done,
        "estimate_accuracy_pct": round(acc,1)
    }

@app.route("/reports/team")
@login_required
@leader_required
def team_report():
    me = current_user()
    frm = request.args.get("from")
    to = request.args.get("to")
    if frm and to:
        start = datetime.datetime.fromisoformat(frm)
        end = datetime.datetime.fromisoformat(to)
    else:
        today = datetime.date.today()
        monday = today - datetime.timedelta(days=today.weekday())
        start = datetime.datetime.combine(monday, datetime.time(0,0,0))
        end = datetime.datetime.utcnow()
    members = User.query.filter_by(team_id=me.team_id).all()
    data = []
    for u in members:
        ss = (FocusSession.query
              .filter(FocusSession.user_id==u.id,
                      FocusSession.start_time>=start,
                      FocusSession.start_time<=end).all())
        focus_minutes = sum(s.actual_minutes or 0 for s in ss)
        pomos = sum(1 for s in ss if s.was_completed)
        tasks_done = Task.query.filter(Task.assignee_id==u.id, Task.status=="done").count()
        done_tasks = Task.query.filter(Task.assignee_id==u.id, Task.status=="done").all()
        est = sum(t.estimate_pomos or 0 for t in done_tasks) or 0
        act = sum(t.actual_pomos or 0 for t in done_tasks) or 0
        acc = (act/est*100) if est>0 else 0
        data.append(dict(user_id=u.id, name=u.name or u.email.split("@")[0],
                         focus_minutes=focus_minutes, pomos=pomos,
                         tasks_done=tasks_done, estimate_accuracy_pct=round(acc,1)))
    return {"range":{"from":start.isoformat(),"to":end.isoformat()}, "members": data}

# -------------------- Templates --------------------
TPL_BASE = """..."""  # giữ nguyên full HTML base mình gửi trước
TPL_LOGIN = """{% extends 'base.html' %} ..."""
TPL_REGISTER = """{% extends 'base.html' %} ..."""
TPL_DASH = """{% extends 'base.html' %} ..."""
TPL_TEAM = """{% extends 'base.html' %} ..."""

# DictLoader
app.jinja_loader = DictLoader({
    "base.html": TPL_BASE,
    "login.html": TPL_LOGIN,
    "register.html": TPL_REGISTER,
    "dash.html": TPL_DASH,
    "team.html": TPL_TEAM,
})

# -------------------- Run --------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
