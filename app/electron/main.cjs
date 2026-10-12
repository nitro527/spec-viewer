// Electron shell: starts the Python backend on a free localhost port and opens the viewer.
const { app, BrowserWindow, Menu, dialog, shell } = require("electron");
const { spawn } = require("child_process");
const net = require("net");
const path = require("path");

let backend;

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.listen(0, "127.0.0.1", () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
    srv.on("error", reject);
  });
}

async function waitFor(url, timeoutMs = 20000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const r = await fetch(url);
      if (r.ok) return;
    } catch (_) {}
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`backend did not start: ${url}`);
}

const SAFE = { contextIsolation: true, nodeIntegration: false, sandbox: true };

// Only web links leave the app (spec and meeting documents carry arbitrary hyperlinks: file:, smb:, javascript:).
function openOutside(url) {
  if (/^https?:\/\//i.test(url)) shell.openExternal(url);
}

// A window never navigates away from the app: links to the outside go to the system browser, so a click on a
// hyperlink inside a spec or meeting document cannot replace the viewer (and every open tab) with a web page.
function guard(wc) {
  const own = (url) => { try { return new URL(url).origin === new URL(wc.getURL()).origin; } catch (_) { return false; } };
  wc.setWindowOpenHandler(({ url }) => {
    if (own(url)) {  // our own pages (CR / meeting document viewer) open in a new app window
      return { action: "allow", overrideBrowserWindowOptions: { width: 1100, height: 900, autoHideMenuBar: true, webPreferences: SAFE } };
    }
    openOutside(url);
    return { action: "deny" };
  });
  wc.on("will-navigate", (e, url) => {
    if (wc.getURL() && !own(url)) {
      e.preventDefault();
      openOutside(url);
    }
  });
}

const HOME = process.env.SPECVIEW_HOME || path.join(require("os").homedir(), ".specview");
let quitting = false;
let mainWin;

/** Start the Python backend on a free port and wait until it answers; resolves to the port. */
async function startBackend() {
  const port = await freePort();
  const args = ["serve", "--port", String(port)];
  let cmd, cwd;
  const env = { ...process.env };
  if (app.isPackaged) {
    // Installed build: PyInstaller backend, bundled pandoc and the built UI live in resources/.
    const res = process.resourcesPath;
    cmd = path.join(res, "backend", process.platform === "win32" ? "specview.exe" : "specview");
    cwd = path.join(res, "backend");
    args.push("--static", path.join(res, "ui"));
    env.PATH = `${path.join(res, "pandoc")}${path.delimiter}${env.PATH || ""}`;
  } else {
    cmd = process.env.SPECVIEW_PYTHON || (process.platform === "win32" ? "python" : "python3");
    cwd = path.join(__dirname, "..", "..", "backend");
    args.unshift("-m", "specview");
    args.push("--static", path.join(__dirname, "..", "dist"));
  }
  // no console window next to the app on Windows: errors go to the log file (Help → 로그 폴더 열기)
  const proc = spawn(cmd, args, { cwd, env, stdio: "inherit", windowsHide: true });
  backend = proc;
  const exited = new Promise((_, reject) => {
    proc.on("exit", (code) => reject(new Error(`backend exited (code ${code})`)));
    proc.on("error", reject);  // the executable is missing or blocked
  });
  exited.catch(() => undefined);
  await Promise.race([waitFor(`http://127.0.0.1:${port}/api/specs`, 60000), exited]);  // a backend that dies at once fails now
  proc.on("exit", (code) => { if (!quitting && backend === proc) backendDied(code); });
  return port;
}

// The backend can be killed (out of memory on a huge spec, an antivirus, a crash): say so and offer a restart
// instead of leaving a window whose every request fails.
async function backendDied(code) {
  backend = null;
  if (!mainWin || mainWin.isDestroyed()) return;
  const r = await dialog.showMessageBox(mainWin, {
    type: "error", title: "Spec Viewer", buttons: ["다시 시작", "로그 폴더 열기", "닫기"], defaultId: 0, cancelId: 2,
    message: "백엔드가 종료되었습니다.",
    detail: `종료 코드 ${code}. 진행 중이던 다운로드·AI 작업은 멈췄고, 저장된 내용은 남아 있습니다.\n` +
      "다시 시작하면 열려 있던 탭이 닫힙니다.",
  });
  if (r.response === 1) {  // look at the log, then decide: the same question again
    await shell.openPath(path.join(HOME, "logs"));
    return backendDied(code);
  }
  if (r.response === 2) return app.quit();
  try {
    const port = await startBackend();
    BrowserWindow.getAllWindows().filter((w) => w !== mainWin).forEach((w) => w.close());  // doc windows of the old port
    await mainWin.loadURL(`http://127.0.0.1:${port}/`);
  } catch (e) {
    dialog.showErrorBox("Spec Viewer", `백엔드를 다시 시작하지 못했습니다.\n${e.message}`);
    app.quit();
  }
}

async function createWindow() {
  // No default menu: its Reload (Ctrl+R / F5) would drop every open tab. Copy/paste keep working via the edit role.
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    { label: "Edit", submenu: [
      { role: "undo" }, { role: "redo" }, { type: "separator" }, { role: "cut" }, { role: "copy" }, { role: "paste" },
      { role: "selectAll" }, { type: "separator" }, { role: "toggleDevTools" }] },
    { label: "Help", submenu: [
      { label: "로그 폴더 열기", click: () => shell.openPath(path.join(HOME, "logs")) },
      { label: "데이터 폴더 열기", click: () => shell.openPath(HOME) }] },
  ]));
  const win = new BrowserWindow({ width: 1500, height: 950, title: "Spec Viewer", autoHideMenuBar: true, webPreferences: SAFE });
  mainWin = win;

  // Dev: Vite serves the UI and proxies /api to a backend started by hand (specview serve).
  const devUrl = process.env.SPECVIEW_UI_URL;
  if (devUrl) return win.loadURL(devUrl);

  let port;
  try {
    port = await startBackend();
  } catch (e) {
    dialog.showErrorBox("Spec Viewer", `백엔드를 시작하지 못했습니다.\n${e.message}\n\n백신·보안 프로그램이 specview.exe 를 막고 있지 않은지 확인하세요.\n로그: ${path.join(HOME, "logs")}`);
    app.quit();
    return;
  }
  await win.loadURL(`http://127.0.0.1:${port}/`);
}

app.on("web-contents-created", (_e, wc) => guard(wc));  // every window, the document viewers included
app.whenReady().then(createWindow);
app.on("window-all-closed", () => app.quit());
app.on("before-quit", () => { quitting = true; });
// The backend may be running pandoc or LibreOffice for a download: on Windows killing it alone leaves those
// converting for minutes after the app is gone, so end the whole process tree.
function stopBackend() {
  if (!backend) return;
  if (process.platform === "win32" && backend.pid) {
    try {
      require("child_process").spawnSync("taskkill", ["/pid", String(backend.pid), "/T", "/F"], { windowsHide: true });
      return;
    } catch (_) { /* fall back to a plain kill */ }
  }
  backend.kill();
}
app.on("quit", stopBackend);
