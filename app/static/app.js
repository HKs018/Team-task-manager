const state = {
  user: null,
  users: [],
  projects: [],
  tasks: [],
  selectedProjectId: null,
  currentView: "dashboard",
};

const $ = (selector) => document.querySelector(selector);

const authView = $("#authView");
const appView = $("#appView");
const loginForm = $("#loginForm");
const signupForm = $("#signupForm");
const toast = $("#toast");
const navButtons = document.querySelectorAll(".nav-list button[data-view]");
const jumpButtons = document.querySelectorAll("[data-jump-view]");

function showToast(message) {
  toast.textContent = message;
  toast.classList.remove("hidden");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.add("hidden"), 3200);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

  const contentType = response.headers.get("content-type") || "";
  const text = await response.text();
  let data = {};

  if (text) {
    if (contentType.includes("application/json")) {
      try {
        data = JSON.parse(text);
      } catch {
        data = { detail: "Server returned broken JSON." };
      }
    } else {
      data = { detail: text };
    }
  }

  if (!response.ok) {
    throw new Error(data.detail || `Request failed with status ${response.status}.`);
  }
  return data;
}

function formJson(form) {
  const data = new FormData(form);
  return Object.fromEntries(data.entries());
}

function setFormBusy(form, isBusy) {
  form.dataset.busy = isBusy ? "true" : "false";
  const submitButton = form.querySelector('button[type="submit"]');
  if (submitButton) {
    submitButton.disabled = isBusy;
  }
}

function setAuthMode(mode) {
  const isLogin = mode === "login";
  $("#loginTab").classList.toggle("active", isLogin);
  $("#signupTab").classList.toggle("active", !isLogin);
  loginForm.classList.toggle("hidden", !isLogin);
  signupForm.classList.toggle("hidden", isLogin);
}

async function setActiveView(view) {
  state.currentView = view;
  const titles = {
    dashboard: "Dashboard",
    projects: "Projects",
    tasks: "Tasks",
  };

  navButtons.forEach((button) => {
    button.classList.toggle("active", button.dataset.view === view);
  });

  $("#pageTitle").textContent = titles[view];
  $("#dashboardView").classList.toggle("hidden", view !== "dashboard");
  $("#projectsView").classList.toggle("hidden", view !== "projects");
  $("#tasksView").classList.toggle("hidden", view !== "tasks");

  if (view === "tasks") {
    await loadAllTasks();
  }
}

function showLoggedOut() {
  state.user = null;
  state.currentView = "dashboard";
  authView.classList.remove("hidden");
  appView.classList.add("hidden");
}

function showLoggedIn() {
  authView.classList.add("hidden");
  appView.classList.remove("hidden");
  $("#userName").textContent = state.user.name;
  $("#userMeta").textContent = `${state.user.email} • ${state.user.role}`;
  $("#roleBadge").textContent = state.user.role;
  document.querySelectorAll(".admin-only").forEach((node) => {
    node.classList.toggle("hidden", state.user.role !== "admin");
  });
}

function statusLabel(status) {
  return {
    todo: "To do",
    in_progress: "In progress",
    done: "Done",
  }[status];
}

function projectProgress(project) {
  if (!project.task_count) return 0;
  return Math.round((project.done_count / project.task_count) * 100);
}

function shortDate(value) {
  if (!value) return "";
  const date = new Date(`${value}T00:00:00`);
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function isOverdue(task) {
  if (!task.due_date || task.status === "done") return false;
  const today = new Date().toISOString().slice(0, 10);
  return task.due_date < today;
}

function renderStats(stats) {
  $("#statProjects").textContent = stats.projects;
  $("#statTasks").textContent = stats.tasks;
  $("#statProgress").textContent = stats.in_progress;
  $("#statOverdue").textContent = stats.overdue;
}

function renderDashboard() {
  renderDashboardProjects();
  renderDashboardTasks();
}

function renderDashboardProjects() {
  const list = $("#dashboardProjectList");
  list.innerHTML = "";

  if (!state.projects.length) {
    list.innerHTML = '<p class="muted">No projects yet.</p>';
    return;
  }

  state.projects.slice(0, 5).forEach((project) => {
    const progress = projectProgress(project);
    const row = document.createElement("button");
    row.type = "button";
    row.className = "compact-row";
    row.innerHTML = `
      <div>
        <strong>${escapeHtml(project.name)}</strong>
        <span>${escapeHtml(project.owner_name)} • ${project.member_count} members</span>
      </div>
      <div class="mini-progress" aria-label="${progress}% complete">
        <span style="width: ${progress}%"></span>
      </div>
    `;
    row.addEventListener("click", () => openProject(project.id));
    list.appendChild(row);
  });
}

function renderDashboardTasks() {
  const list = $("#dashboardTaskList");
  list.innerHTML = "";

  const activeTasks = state.tasks.filter((task) => task.status !== "done").slice(0, 5);
  if (!activeTasks.length) {
    list.innerHTML = '<p class="muted">No active tasks.</p>';
    return;
  }

  activeTasks.forEach((task) => {
    const row = document.createElement("article");
    row.className = "compact-row task-row";
    row.innerHTML = `
      <div>
        <strong>${escapeHtml(task.title)}</strong>
        <span>${escapeHtml(task.project_name || "Project")} • ${escapeHtml(task.assigned_name || "Unassigned")}</span>
      </div>
      <span class="status-pill ${isOverdue(task) ? "overdue" : task.status}">
        ${isOverdue(task) ? "Overdue" : statusLabel(task.status)}
      </span>
    `;
    list.appendChild(row);
  });
}

function renderUserOptions() {
  const memberSelect = $("#memberSelect");
  const taskAssignee = $("#taskAssignee");
  memberSelect.innerHTML = "";
  taskAssignee.innerHTML = '<option value="">Unassigned</option>';

  state.users.forEach((user) => {
    const memberOption = document.createElement("option");
    memberOption.value = user.id;
    memberOption.textContent = `${user.name} (${user.role})`;
    memberSelect.appendChild(memberOption);

    const taskOption = document.createElement("option");
    taskOption.value = user.id;
    taskOption.textContent = user.name;
    taskAssignee.appendChild(taskOption);
  });
}

function renderProjects() {
  const list = $("#projectList");
  list.innerHTML = "";

  if (!state.projects.length) {
    list.innerHTML = '<p class="muted">No projects yet.</p>';
    return;
  }

  state.projects.forEach((project) => {
    const item = document.createElement("article");
    item.className = "project-item";
    item.classList.toggle("active", project.id === state.selectedProjectId);

    const openButton = document.createElement("button");
    openButton.type = "button";
    openButton.className = "project-select";
    const progress = project.task_count
      ? `${project.done_count}/${project.task_count} tasks done`
      : "No tasks";
    const percent = projectProgress(project);
    openButton.innerHTML = `
      <span class="project-title-row">
        <strong>${escapeHtml(project.name)}</strong>
        <span class="soft-pill">${percent}%</span>
      </span>
      <span>${escapeHtml(project.owner_name)} • ${project.member_count} members • ${progress}</span>
      <span class="project-progress"><span style="width: ${percent}%"></span></span>
    `;
    openButton.addEventListener("click", () => selectProject(project.id));
    item.appendChild(openButton);

    if (state.user.role === "admin") {
      const deleteButton = document.createElement("button");
      deleteButton.type = "button";
      deleteButton.className = "danger-btn";
      deleteButton.textContent = "Delete";
      deleteButton.addEventListener("click", () => deleteProject(project.id));
      item.appendChild(deleteButton);
    }

    list.appendChild(item);
  });
}

function renderMembers(members) {
  const list = $("#memberList");
  list.innerHTML = "";

  if (!members.length) {
    list.innerHTML = '<p class="muted">No members.</p>';
    return;
  }

  members.forEach((member) => {
    const row = document.createElement("div");
    row.className = "member-row";
    row.innerHTML = `
      <div>
        <strong>${escapeHtml(member.name)}</strong>
        <span>${escapeHtml(member.email)} • ${member.role}</span>
      </div>
    `;

    if (state.user.role === "admin" && member.id !== state.user.id) {
      const remove = document.createElement("button");
      remove.className = "danger-btn";
      remove.type = "button";
      remove.textContent = "Remove";
      remove.addEventListener("click", () => removeMember(member.id));
      row.appendChild(remove);
    }

    list.appendChild(row);
  });
}

function createTaskCard(task, options = {}) {
  const card = document.createElement("article");
  card.className = "task-card";
  card.dataset.status = task.status;
  const overdue = isOverdue(task);
  card.innerHTML = `
    <div>
      <strong>${escapeHtml(task.title)}</strong>
      <p>${escapeHtml(task.description || "No description")}</p>
    </div>
    <div class="task-meta">
      <span class="status-pill ${task.status}">${statusLabel(task.status)}</span>
      ${overdue ? '<span class="status-pill overdue">Overdue</span>' : ""}
      ${options.showProject && task.project_name ? `<span class="soft-pill">${escapeHtml(task.project_name)}</span>` : ""}
      <span class="soft-pill">${escapeHtml(task.assigned_name || "Unassigned")}</span>
      ${task.due_date ? `<span class="soft-pill">Due ${shortDate(task.due_date)}</span>` : ""}
    </div>
  `;

  const canUpdate = state.user.role === "admin" || task.assigned_to === state.user.id;
  if (canUpdate) {
    const actions = document.createElement("div");
    actions.className = "task-actions";

    const statusSelect = document.createElement("select");
    ["todo", "in_progress", "done"].forEach((status) => {
      const option = document.createElement("option");
      option.value = status;
      option.textContent = statusLabel(status);
      option.selected = status === task.status;
      statusSelect.appendChild(option);
    });
    statusSelect.addEventListener("change", () => updateTask(task.id, { status: statusSelect.value }));
    actions.appendChild(statusSelect);

    if (state.user.role === "admin") {
      const del = document.createElement("button");
      del.type = "button";
      del.textContent = "Delete";
      del.addEventListener("click", () => deleteTask(task.id));
      actions.appendChild(del);
    }

    card.appendChild(actions);
  }

  return card;
}

function renderTasks(tasks) {
  const buckets = {
    todo: $("#todoTasks"),
    in_progress: $("#progressTasks"),
    done: $("#doneTasks"),
  };

  Object.values(buckets).forEach((bucket) => {
    bucket.innerHTML = "";
  });

  $("#todoCount").textContent = tasks.filter((task) => task.status === "todo").length;
  $("#progressCount").textContent = tasks.filter((task) => task.status === "in_progress").length;
  $("#doneCount").textContent = tasks.filter((task) => task.status === "done").length;

  tasks.forEach((task) => {
    buckets[task.status].appendChild(createTaskCard(task));
  });

  Object.values(buckets).forEach((bucket) => {
    if (!bucket.children.length) {
      bucket.innerHTML = '<p class="muted">No tasks.</p>';
    }
  });
}

function renderAllTasks(tasks) {
  const list = $("#allTaskList");
  list.innerHTML = "";

  if (!tasks.length) {
    list.innerHTML = '<p class="muted">No tasks yet.</p>';
    return;
  }

  tasks.forEach((task) => {
    list.appendChild(createTaskCard(task, { showProject: true }));
  });
}

function renderProjectDetail(project, members, tasks) {
  $("#emptyProject").classList.add("hidden");
  $("#projectDetail").classList.remove("hidden");
  $("#selectedProjectName").textContent = project.name;
  $("#selectedProjectDescription").textContent = project.description || "";
  $("#selectedProjectOwner").textContent = `Owner: ${project.owner_name}`;
  renderProjectMetrics(project, members, tasks);
  renderMembers(members);
  renderTasks(tasks);
}

function renderProjectMetrics(project, members, tasks) {
  const doneCount = tasks.filter((task) => task.status === "done").length;
  const progress = tasks.length ? Math.round((doneCount / tasks.length) * 100) : 0;
  const overdueCount = tasks.filter(isOverdue).length;
  $("#projectMetrics").innerHTML = `
    <span><strong>${members.length}</strong> members</span>
    <span><strong>${tasks.length}</strong> tasks</span>
    <span><strong>${progress}%</strong> complete</span>
    <span><strong>${overdueCount}</strong> overdue</span>
  `;
}

async function loadApp() {
  showLoggedIn();
  const [usersData, dashboardData, projectsData, tasksData] = await Promise.all([
    api("/api/users"),
    api("/api/dashboard"),
    api("/api/projects"),
    api("/api/tasks"),
  ]);
  state.users = usersData.users;
  state.projects = projectsData.projects;
  state.tasks = tasksData.tasks;
  renderUserOptions();
  renderStats(dashboardData.stats);
  renderDashboard();

  if (!state.selectedProjectId && state.projects.length) {
    state.selectedProjectId = state.projects[0].id;
  }
  renderProjects();

  if (state.selectedProjectId) {
    await selectProject(state.selectedProjectId, false);
  } else {
    $("#emptyProject").classList.remove("hidden");
    $("#projectDetail").classList.add("hidden");
  }
  await setActiveView(state.currentView);
}

async function selectProject(projectId, refreshList = true) {
  state.selectedProjectId = projectId;
  if (refreshList) renderProjects();
  const [projectData, membersData, tasksData] = await Promise.all([
    api(`/api/projects/${projectId}`),
    api(`/api/projects/${projectId}/members`),
    api(`/api/projects/${projectId}/tasks`),
  ]);
  renderProjectDetail(projectData.project, membersData.members, tasksData.tasks);
}

async function openProject(projectId) {
  await selectProject(projectId);
  await setActiveView("projects");
}

async function loadAllTasks() {
  const data = await api("/api/tasks");
  state.tasks = data.tasks;
  renderAllTasks(data.tasks);
}

async function refreshAfterChange() {
  const currentProject = state.selectedProjectId;
  const [dashboardData, projectsData, tasksData] = await Promise.all([
    api("/api/dashboard"),
    api("/api/projects"),
    api("/api/tasks"),
  ]);
  state.projects = projectsData.projects;
  state.tasks = tasksData.tasks;
  renderStats(dashboardData.stats);
  renderDashboard();
  renderProjects();
  if (currentProject) await selectProject(currentProject, false);
  if (state.currentView === "tasks") await loadAllTasks();
}

async function removeMember(userId) {
  if (!state.selectedProjectId) return;
  await api(`/api/projects/${state.selectedProjectId}/members/${userId}`, {
    method: "DELETE",
  });
  showToast("Member removed.");
  await selectProject(state.selectedProjectId, false);
}

async function updateTask(taskId, payload) {
  await api(`/api/tasks/${taskId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
  await refreshAfterChange();
}

async function deleteTask(taskId) {
  await api(`/api/tasks/${taskId}`, { method: "DELETE" });
  showToast("Task deleted.");
  await refreshAfterChange();
}

async function deleteProject(projectId) {
  const project = state.projects.find((item) => item.id === projectId);
  const projectName = project ? project.name : "this project";
  if (!window.confirm(`Delete "${projectName}" and all its tasks?`)) return;

  await api(`/api/projects/${projectId}`, { method: "DELETE" });
  showToast("Project deleted.");
  if (state.selectedProjectId === projectId) {
    state.selectedProjectId = null;
  }
  await loadApp();
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

$("#loginTab").addEventListener("click", () => setAuthMode("login"));
$("#signupTab").addEventListener("click", () => setAuthMode("signup"));

navButtons.forEach((button) => {
  button.addEventListener("click", () => {
    setActiveView(button.dataset.view).catch((error) => showToast(error.message));
  });
});

jumpButtons.forEach((button) => {
  button.addEventListener("click", () => {
    setActiveView(button.dataset.jumpView).catch((error) => showToast(error.message));
  });
});

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const data = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify(formJson(loginForm)),
    });
    state.user = data.user;
    state.currentView = "dashboard";
    await loadApp();
  } catch (error) {
    showToast(error.message);
  }
});

signupForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const data = await api("/api/auth/signup", {
      method: "POST",
      body: JSON.stringify(formJson(signupForm)),
    });
    state.user = data.user;
    state.currentView = "dashboard";
    await loadApp();
  } catch (error) {
    showToast(error.message);
  }
});

$("#logoutBtn").addEventListener("click", async () => {
  await api("/api/auth/logout", { method: "POST" });
  state.selectedProjectId = null;
  showLoggedOut();
});

$("#projectForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  if (form.dataset.busy === "true") return;
  setFormBusy(form, true);
  try {
    await api("/api/projects", {
      method: "POST",
      body: JSON.stringify(formJson(form)),
    });
    form.reset();
    showToast("Project created.");
    state.selectedProjectId = null;
    await loadApp();
  } catch (error) {
    showToast(error.message);
  } finally {
    setFormBusy(form, false);
  }
});

$("#memberForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.selectedProjectId) return;
  const form = event.currentTarget;
  if (form.dataset.busy === "true") return;
  setFormBusy(form, true);
  try {
    const data = formJson(form);
    await api(`/api/projects/${state.selectedProjectId}/members`, {
      method: "POST",
      body: JSON.stringify({ user_id: Number(data.user_id) }),
    });
    showToast("Member added.");
    await selectProject(state.selectedProjectId, false);
  } catch (error) {
    showToast(error.message);
  } finally {
    setFormBusy(form, false);
  }
});

$("#taskForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.selectedProjectId) return;
  const form = event.currentTarget;
  if (form.dataset.busy === "true") return;
  setFormBusy(form, true);
  try {
    const data = formJson(form);
    const payload = {
      title: data.title,
      description: data.description,
      assigned_to: data.assigned_to ? Number(data.assigned_to) : null,
      status: data.status,
      due_date: data.due_date || null,
    };
    await api(`/api/projects/${state.selectedProjectId}/tasks`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    form.reset();
    showToast("Task created.");
    await refreshAfterChange();
  } catch (error) {
    showToast(error.message);
  } finally {
    setFormBusy(form, false);
  }
});

(async function boot() {
  try {
    const data = await api("/api/me");
    state.user = data.user;
    await loadApp();
  } catch {
    showLoggedOut();
  }
})();
