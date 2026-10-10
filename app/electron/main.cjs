// Electron shell: starts the Python backend on a free localhost port and opens the viewer.
const { app, BrowserWindow, shell } = require("electron");
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

async function createWindow() {
  const win = new BrowserWindow({ width: 1500, height: 950, title: "Spec Viewer", autoHideMenuBar: true });
  // External links (3GPP portal, Tdoc pages) open in the system browser.
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });

  // Dev: Vite serves the UI and proxies /api to a backend started by hand (specview serve).
  const devUrl = process.env.SPECVIEW_UI_URL;
  if (devUrl) return win.loadURL(devUrl);

  const port = await freePort();
  const python = process.env.SPECVIEW_PYTHON || (process.platform === "win32" ? "python" : "python3");
  const backendDir = path.join(__dirname, "..", "..", "backend");
  const staticDir = path.join(__dirname, "..", "dist");
  backend = spawn(python, ["-m", "specview.cli", "serve", "--port", String(port), "--static", staticDir], {
    cwd: backendDir,
    stdio: "inherit",
  });
  await waitFor(`http://127.0.0.1:${port}/api/specs`);
  await win.loadURL(`http://127.0.0.1:${port}/`);
}

app.whenReady().then(createWindow);
app.on("window-all-closed", () => app.quit());
app.on("quit", () => backend && backend.kill());
