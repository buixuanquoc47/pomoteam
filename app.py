import os, datetime, functools
from flask import Flask, request, redirect, url_for, session, render_template, abort, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from jinja2 import DictLoader

# -------------------- Setup --------------------
app = Flask(__name__)

# Secret key cho session
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-change-me")

# Ưu tiên DATABASE_URL (PostgreSQL trên Render). Nếu không có thì fallback SQLite.
db_url = os.getenv("DATABASE_URL", "").strip()
# Chuẩn hoá prefix "postgres://" -> "postgresql://"
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = db_url or "sqlite:///pomoteam.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Khuyến nghị cho môi trường free (tự sleep/wake): tránh lỗi kết nối "stale"
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,
    "pool_recycle": 300,   # tái chế kết nối mỗi 5 phút
}

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
TPL_BASE = """<!doctype html>..."""  # FULL base HTML từ bản trước
TPL_LOGIN = """{% extends 'base.html' %}..."""  
TPL_REGISTER = """{% extends 'base.html' %}..."""  
TPL_DASH = """{% extends 'base.html' %}..."""  
TPL_TEAM = """{% extends 'base.html' %}..."""  
TPL_BASE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ APP_NAME }}</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<style>
  .timer { font-size: 56px; font-weight: 600; letter-spacing: 2px; }
  .mono { font-variant-numeric: tabular-nums; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
</style>
</head>
<body class="bg-light">
<nav class="navbar navbar-expand-lg bg-white shadow-sm">
  <div class="container">
    <a class="navbar-brand" href="{{ url_for('dashboard') }}">{{ APP_NAME }}</a>
    <div class="ms-auto">
      {% if session.get('uid') %}
        <a href="{{ url_for('team_dashboard') }}" class="btn btn-outline-secondary btn-sm me-2">Team</a>
        <a href="{{ url_for('logout') }}" class="btn btn-outline-danger btn-sm">Logout</a>
      {% endif %}
    </div>
  </div>
</nav>
<div class="container py-4">
  {% with messages = get_flashed_messages(with_categories=true) %}
    {% if messages %}
      {% for cat, msg in messages %}
      <div class="alert alert-{{cat}}">{{ msg }}</div>
      {% endfor %}
    {% endif %}
  {% endwith %}
  {% block content %}{% endblock %}
</div>
<script>
async function startSession(taskId, planned=25){
  const res = await fetch("{{ url_for('start_session') }}", {
    method:"POST", headers:{'Content-Type':'application/x-www-form-urlencoded'},
    body:new URLSearchParams({task_id: taskId||'', planned_minutes: planned})
  });
  return (await res.json()).session_id;
}
async function finishSession(sessionId, completed=true, notes=''){
  const res = await fetch("{{ url_for('finish_session') }}", {
    method:"POST", headers:{'Content-Type':'application/x-www-form-urlencoded'},
    body:new URLSearchParams({session_id: sessionId, completed: completed?'true':'false', notes})
  });
  return await res.json();
}
</script>
</body></html>
"""
TPL_LOGIN = """
{% extends 'base.html' %}{% block content %}
<div class="row justify-content-center">
  <div class="col-md-5">
    <div class="card shadow-sm">
      <div class="card-body">
        <h4 class="mb-3">Sign in</h4>
        <form method="post">
          <div class="mb-3"><label class="form-label">Email</label>
            <input name="email" type="email" class="form-control" required>
          </div>
          <div class="mb-3"><label class="form-label">Password</label>
            <input name="password" type="password" class="form-control" required>
          </div>
          <button class="btn btn-dark w-100">Login</button>
        </form>
        <hr><p class="mb-0">No account? <a href="{{ url_for('register') }}">Register</a></p>
      </div>
    </div>
  </div>
</div>
{% endblock %}
"""
TPL_REGISTER = """
{% extends 'base.html' %}{% block content %}
<div class="row justify-content-center">
  <div class="col-md-6">
    <div class="card shadow-sm">
      <div class="card-body">
        <h4 class="mb-3">Create account</h4>
        <form method="post">
          <div class="row">
            <div class="col-md-6 mb-3"><label class="form-label">Name</label>
              <input name="name" class="form-control">
            </div>
            <div class="col-md-6 mb-3"><label class="form-label">Email</label>
              <input name="email" type="email" class="form-control" required>
            </div>
          </div>
          <div class="mb-3"><label class="form-label">Password</label>
            <input name="password" type="password" class="form-control" required>
          </div>
          <button class="btn btn-dark w-100">Register</button>
        </form>
        <p class="text-muted mt-2">User đầu tiên sẽ là <b>leader</b>.</p>
      </div>
    </div>
  </div>
</div>
{% endblock %}
"""
TPL_DASH = """
{% extends 'base.html' %}{% block content %}
<div class="row g-4">
  <div class="col-lg-4">
    <div class="card shadow-sm">
      <div class="card-body">
        <h5 class="mb-3">Start Pomodoro</h5>
        <form onsubmit="return false;">
          <div class="mb-3">
            <label class="form-label">Task</label>
            <select id="taskSelect" class="form-select">
              <option value="">(no task)</option>
              {% for t in tasks %}
              <option value="{{t.id}}">{{ t.title }}</option>
              {% endfor %}
            </select>
          </div>
          <div class="mb-3">
            <label class="form-label">Minutes</label>
            <input id="minutes" type="number" value="25" min="5" class="form-control">
          </div>
          <div class="text-center my-3">
            <div id="timer" class="timer mono">25:00</div>
          </div>
          <div class="d-flex gap-2">
            <button id="btnStart" class="btn btn-dark flex-grow-1">Start</button>
            <button id="btnPause" class="btn btn-outline-secondary" disabled>Pause</button>
            <button id="btnReset" class="btn btn-outline-danger" disabled>Reset</button>
          </div>
        </form>
        <script>
        let secs=25*60, running=false, itv=null, sid=null, paused=false;
        const elTimer = document.getElementById('timer');
        const elMin = document.getElementById('minutes');
        const elStart = document.getElementById('btnStart');
        const elPause = document.getElementById('btnPause');
        const elReset = document.getElementById('btnReset');
        const elTask = document.getElementById('taskSelect');
        function fmt(s){const m=Math.floor(s/60), r=s%60; return (m+'').padStart(2,'0')+':'+(r+'').padStart(2,'0');}
        function setMinutes(m){ secs=m*60; elTimer.textContent=fmt(secs); }
        elMin.addEventListener('change', e=> setMinutes(parseInt(e.target.value||25)));
        elStart.addEventListener('click', async ()=>{
          if(!running){
            sid = await startSession(elTask.value, parseInt(elMin.value||25));
            running=true; paused=false;
            elPause.disabled=false; elReset.disabled=false; elStart.textContent='Stop';
            itv=setInterval(()=>{ if(!paused){ secs=Math.max(0,secs-1); elTimer.textContent=fmt(secs);
              if(secs===0){ running=false; clearInterval(itv); finishSession(sid,true); elStart.textContent='Start'; elPause.disabled=true; } } },1000);
          }else{
            running=false; clearInterval(itv); elStart.textContent='Start';
            finishSession(sid, secs===0);
            elPause.disabled=true; elReset.disabled=true;
          }
        });
        elPause.addEventListener('click', ()=>{ paused=!paused; elPause.textContent=paused?'Resume':'Pause';});
        elReset.addEventListener('click', ()=>{ secs=parseInt(elMin.value||25)*60; elTimer.textContent=fmt(secs);});
        </script>
      </div>
    </div>
    <div class="card shadow-sm mt-3">
      <div class="card-body">
        <h6 class="text-muted mb-2">Today</h6>
        <div class="d-flex gap-4">
          <div><div class="h4 mono">{{ mins_today }}</div><div class="text-muted">focus mins</div></div>
          <div><div class="h4 mono">{{ pomos_today }}</div><div class="text-muted">pomos</div></div>
        </div>
      </div>
    </div>
  </div>

  <div class="col-lg-8">
    <div class="card shadow-sm mb-3">
      <div class="card-body">
        <h5 class="mb-3">My Tasks</h5>
        <form class="row g-2" method="post" action="{{ url_for('create_task') }}">
          <div class="col-md-4"><input name="title" class="form-control" placeholder="Task title" required></div>
          <div class="col-md-2"><input name="estimate_pomos" type="number" min="0" class="form-control" placeholder="Est. pomos"></div>
          <div class="col-md-2">
            <select name="priority" class="form-select">
              <option>normal</option><option>high</option><option>low</option>
            </select>
          </div>
          <div class="col-md-2"><input name="due_date" type="date" class="form-control"></div>
          <div class="col-md-2">
            <select name="project_id" class="form-select">
              <option value="">(project)</option>
              {% for p in projects %}<option value="{{p.id}}">{{p.name}}</option>{% endfor %}
            </select>
          </div>
          <div class="col-12"><textarea name="description" class="form-control" placeholder="Description (optional)"></textarea></div>
          <div class="col-12"><button class="btn btn-dark">Add Task</button></div>
        </form>
        <hr>
        <div class="table-responsive">
          <table class="table align-middle">
            <thead><tr><th>Title</th><th>Status</th><th>Est/Act</th><th>Priority</th><th>Due</th><th></th></tr></thead>
            <tbody>
              {% for t in tasks %}
              <tr>
                <td>
                  <div class="fw-medium">{{ t.title }}</div>
                  <div class="small text-muted">{{ t.description or '' }}</div>
                </td>
                <td>
                  <form method="post" action="{{ url_for('update_task', task_id=t.id) }}">
                    <select name="status" class="form-select form-select-sm" onchange="this.form.submit()">
                      {% for s in ['todo','doing','done','blocked'] %}
                        <option value="{{s}}" {% if t.status==s %}selected{% endif %}>{{s}}</option>
                      {% endfor %}
                    </select>
                  </form>
                </td>
                <td class="mono">{{ t.estimate_pomos }}/{{ t.actual_pomos }}</td>
                <td>{{ t.priority }}</td>
                <td>{{ t.due_date or '' }}</td>
                <td>
                  <form method="post" action="{{ url_for('update_task', task_id=t.id) }}" class="d-flex gap-2">
                    <input type="hidden" name="title" value="{{t.title}}">
                    <input type="hidden" name="description" value="{{t.description or ''}}">
                    <input type="number" name="estimate_pomos" value="{{ t.estimate_pomos }}" class="form-control form-control-sm" style="width:90px;">
                    <button class="btn btn-sm btn-outline-primary">Save</button>
                  </form>
                </td>
              </tr>
              {% else %}
              <tr><td colspan="6" class="text-muted">No tasks yet.</td></tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <div class="card shadow-sm">
      <div class="card-body">
        <h5 class="mb-3">Projects</h5>
        <form class="d-flex gap-2" method="post" action="{{ url_for('create_project') }}">
          <input name="name" class="form-control" placeholder="New project name" required>
          <button class="btn btn-outline-dark">Add</button>
        </form>
        <ul class="mt-3">
          {% for p in projects %}<li>{{ p.name }}</li>{% else %}<li class="text-muted">No projects.</li>{% endfor %}
        </ul>
      </div>
    </div>
  </div>
</div>
{% endblock %}
"""
TPL_TEAM = """
{% extends 'base.html' %}{% block content %}
<h4 class="mb-3">Team Dashboard <small class="text-muted">({{ rng }})</small></h4>
<div class="mb-3">
  <a class="btn btn-sm btn-outline-secondary" href="{{ url_for('team_dashboard', range='day') }}">Day</a>
  <a class="btn btn-sm btn-outline-secondary" href="{{ url_for('team_dashboard', range='week') }}">Week</a>
  <a class="btn btn-sm btn-outline-secondary" href="{{ url_for('team_dashboard', range='month') }}">Month</a>
</div>
<div class="table-responsive">
<table class="table align-middle">
  <thead><tr><th>Member</th><th>Focus mins</th><th>Pomos</th><th>Tasks done</th><th>Est. accuracy</th></tr></thead>
  <tbody>
  {% for m in members_stats %}
    <tr>
      <td>{{ m.user.name or m.user.email.split("@")[0] }}</td>
      <td class="mono">{{ m.focus_minutes }}</td>
      <td class="mono">{{ m.pomos }}</td>
      <td class="mono">{{ m.tasks_done }}</td>
      <td class="mono">{{ m.estimate_accuracy }}%</td>
    </tr>
  {% else %}
    <tr><td colspan="5" class="text-muted">No members.</td></tr>
  {% endfor %}
  </tbody>
</table>
</div>
<h5 class="mt-4">Blocked Tasks</h5>
<ul>
  {% for t in blocked %}
    <li><b>{{ t.title }}</b> — {{ t.assignee.name if t.assignee else 'Unassigned' }} {% if t.due_date %}(due {{ t.due_date }}){% endif %}</li>
  {% else %}
    <li class="text-muted">No blocked tasks 🎉</li>
  {% endfor %}
</ul>
{% endblock %}
"""

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
app.jinja_loader = DictLoader({
    "base.html": TPL_BASE,
    "login.html": TPL_LOGIN,
    "register.html": TPL_REGISTER,
    "dash.html": TPL_DASH,
    "team.html": TPL_TEAM,
})
