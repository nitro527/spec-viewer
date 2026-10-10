# Windows 설치 파일 만들기

설치형 앱은 Python 설치 없이 동작하도록 다음 세 가지를 함께 묶습니다.

| 구성 | 위치 (설치 후 `resources/`) | 만드는 법 |
|---|---|---|
| 백엔드 실행 파일 | `backend/specview.exe` | PyInstaller (`backend/packaging/launcher.py`) |
| pandoc | `pandoc/pandoc.exe` | pandoc Windows zip 에서 `pandoc.exe` 복사 |
| UI | `ui/` | `npm run build` 결과 (`app/dist`) |

Electron(`app/electron/main.cjs`)은 설치본(`app.isPackaged`)이면 `resources/backend/specview.exe` 를
실행하고 `resources/pandoc` 을 PATH 앞에 붙입니다. 개발 중에는 `python -m specview` 를 그대로 씁니다.

## 빌드 순서 (Windows, PowerShell)

```powershell
# 1) 백엔드 → backend\dist\specview\specview.exe
cd backend
pip install -e . pyinstaller
pyinstaller -y -n specview --onedir --collect-submodules specview --collect-submodules uvicorn `
  --paths . --distpath dist packaging\launcher.py

# 2) pandoc → app\vendor\pandoc\pandoc.exe
#    https://github.com/jgm/pandoc/releases 에서 windows-x86_64.zip 을 받아 pandoc.exe 만 복사
mkdir ..\app\vendor\pandoc
copy <압축 푼 폴더>\pandoc.exe ..\app\vendor\pandoc\

# 3) 설치 파일 → app\release\Spec Viewer Setup x.y.z.exe
cd ..\app
npm install
npm run dist
```

## 참고

- 사내 프록시를 거쳐야 하면 실행 전에 `HTTPS_PROXY` 환경 변수를 설정합니다 (`requests` 가 그대로 사용).
- 라이브러리 위치는 기본 `%USERPROFILE%\.specview` 이며 `SPECVIEW_HOME` 으로 바꿀 수 있습니다.
- LibreOffice 는 선택 사항입니다. 구형 `.doc` 스펙(주로 Rel-8 이전)과 EMF 그림 변환에만 쓰이고,
  38 시리즈의 WMF 수식은 내장 변환기가 처리합니다.
- 코드 서명이 없으면 Windows SmartScreen 경고가 뜹니다. 사내 배포라면 사내 인증서로 서명하세요
  (`build.win.certificateFile` 등 electron-builder 설정).
