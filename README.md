# AeroRecorder

A lightweight, native-feeling Windows 11 screen recorder written in Python. AeroRecorder records a full desktop or selected region, optionally mixes microphone audio, remembers the save location, and lists earlier recordings inside the app.

## Features

- Windows 11-inspired dark Fluent interface
- Full-screen, drag-to-select region, and click-to-select window capture
- Microphone and Windows system-audio recording
- Pause and resume without including the paused time in the result
- Optional 0, 3, 5, or 10 second recording countdown
- Editable global start/stop and pause/resume keyboard shortcuts
- 30/60 FPS and three quality profiles
- Optional mouse cursor capture
- Default output in the Windows `Videos\AeroRecorder` known folder
- Custom save-folder chooser
- Recordings library with play, reveal, and delete actions
- Floating recording timer and Stop control excluded from capture when Windows supports it
- One lightweight WASAPI helper dependency for driver-free system-audio capture

## Quick start

Requirements: Windows 10 version 1803 or newer and an official Python 3 installation with Tkinter.

Verify Tkinter:

```powershell
python -m tkinter
```

Download the portable FFmpeg sidecar once:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\get-ffmpeg.ps1
```

Install the single runtime dependency, then start AeroRecorder:

```powershell
python -m pip install -r .\requirements.txt
powershell -ExecutionPolicy Bypass -File .\scripts\run.ps1
```

You can also run `python main.py` directly. No `pip install` step is needed.

## Build the EXE and installer

The release build uses PyInstaller only at packaging time. The application is built as a folder first for faster startup and then wrapped into a single installer.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build.ps1
```

Outputs:

- Portable application: `dist\AeroRecorder\AeroRecorder.exe`
- Installer, when Inno Setup 6 is installed: `installer\output\AeroRecorder-Setup.exe`

The installer is per-user and does not request administrator rights. End users do not need Python or FFmpeg installed separately.

## Tests

```powershell
python -m unittest discover -s tests -v
```

The tests use only the standard library and do not start the GUI or record the screen.

## Project layout

```text
aero_recorder/       Application modules
installer/           Inno Setup installer definition
packaging/           Windows application manifest
scripts/             Run, FFmpeg setup, and build scripts
tests/               Standard-library tests
tools/               Portable FFmpeg location
main.py              Application entry point
```

## FFmpeg notice

FFmpeg is a separate project. The included setup script downloads a prebuilt FFmpeg distribution. Before distributing a packaged build, review the selected build's LGPL/GPL configuration and include its corresponding license and source-offer materials as required.
