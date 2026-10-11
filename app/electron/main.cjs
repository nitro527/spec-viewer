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

async function createWindow() {
  // No default menu: its Reload (Ctrl+R / F5) would drop every open tab. Copy/paste keep working via the edit role.
  Menu.setApplicationMenu(Menu.buildFromTemplate([{ label: "Edit", submenu: [
    { role: "undo" }, { role: "redo" }, { type: "separator" }, { role: "cut" }, { role: "copy" }, { role: "paste" },
    { role: "selectAll" }, { type: "separator" }, { role: "toggleDevTools" }] }]));
  const win = new BrowserWindow({ width: 1500, height: 950, title: "Spec Viewer", autoHideMenuBar: true, webPreferences: SAFE });

  // Dev: Vite serves the UI and proxies /api to a backend started by hand (specview serve).
  const devUrl = process.env.SPECVIEW_UI_URL;
  if (devUrl) return win.loadURL(devUrl);

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
  backend = spawn(cmd, args, { cwd, env, stdio: "inherit" });
  try {
    await waitFor(`http://127.0.0.1:${port}/api/specs`, 60000);
  } catch (e) {
    dialog.showErrorBox("Spec Viewer", `백엔드를 시작하지 못했습니다.\n${e.message}\n\n백신·보안 프로그램이 specview.exe 를 막고 있지 않은지 확인하세요.`);
    app.quit();
    return;
  }
  await win.loadURL(`http://127.0.0.1:${port}/`);
}

app.on("web-contents-created", (_e, wc) => guard(wc));  // every window, the document viewers included
app.whenReady().then(createWindow);
app.on("window-all-closed", () => app.quit());
app.on("quit", () => backend && backend.kill());
