# TortoiseHg Dark Themes

Dark theme support for TortoiseHg 7.0.1

## Features

 - 6 built-in dark themes
 - 1-minute installation
 - tested on Windows 10/11 and Ubuntu 22.04
 - works with PyQt5 and PyQt6

![Dark](screenshots/Win11_1_dark.png)

## Installation on Linux

See [linux/README.md](linux/README.md)


## Installation on Windows (Recommended)

1. Install **[TortoiseHg 7.0.1](https://tortoisehg.bitbucket.io)** and **[7-Zip](https://7-zip.org)** to their default locations
2. Download this repository as a zip and extract it to a path without spaces (e.g. `C:\Projects\tortoisehg-dark-themes`)
3. Run `tools\install_and_run_on_windows.bat` **as Administrator**
4. Select a theme in: **File / Settings / Workbench / Theme** (restart required)

> Without Administrator rights, the patched `library.zip` is created in the repo but not copied automatically. Copy it manually to `C:\Program Files\TortoiseHg\lib\`.

---

## Manual Installation on Windows

1. Backup `C:\Program Files\TortoiseHg\lib\library.zip` to the repo folder
2. Extract it into `repo\library`
3. Run `tools\apply_patch.bat`
4. Repack `library.zip` using **7-Zip** or **WinRAR** (not `tar` or `Compress-Archive`)
5. Copy the new `library.zip` back to `C:\Program Files\TortoiseHg\lib\`

---


## Custom Themes

For custom themes, see: [custom_themes/README.md](custom_themes/README.md)

---

## Screenshots

![Dark VSCode](screenshots/Win11_2_dark_vscode.png)
![Dark Gruvbox](screenshots/Win11_3_dark_gruvbox.png)
![Dark Dracula](screenshots/Win11_4_dark_dracula.png)
![Dark One Dark](screenshots/Win11_5_dark_onedark.png)
![Dark Nord](screenshots/Win11_6_dark_nord.png)

## Known limitations

- On Windows 10, the window title bar remains black and cannot be recolored due to OS limitations.

---

Screenshot from Windows 10
![Dark](screenshots/1_dark.png)